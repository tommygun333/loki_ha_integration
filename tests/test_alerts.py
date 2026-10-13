"""Alert rules: validation, cooldown, persistence, no replay, and safe events."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from loki_monitor.alerts import (
    DEFAULT_RULE, EVENT_ALERT, LokiAlertEngine, clean_rule, make_rule_query,
    test_alert_rule as preview_rule,
)
from loki_monitor.viewer import CANONICAL_SEVERITY_TEMPLATE


class MemoryStore:
    def __init__(self):
        self.data = None
        self.writes = 0
        self.fail_save = False

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        if self.fail_save:
            raise OSError("write failed")
        self.data = {"states": {k: dict(v) for k, v in data["states"].items()}}
        self.writes += 1


class FakeAPI:
    def __init__(self, now_ns):
        self.now_ns = now_ns
        self.events = []
        self.bad = False
        self.query_calls = []

    async def async_query_streams_ns(self, query, *, start_ns, end_ns, limit):
        assert limit == 1
        self.query_calls.append(query)
        if self.bad:
            raise ValueError("simulated Loki error")
        values = [[str(t), line] for t, line in self.events if start_ns <= t <= end_ns]
        values.sort(key=lambda v: int(v[0]), reverse=True)
        return {"resultType": "streams", "result": [
            {"stream": {"host":"CoreELEC"}, "values":values[:limit]}
        ]}

    async def async_query(self, query):
        self.query_calls.append(query)
        if self.bad:
            raise ValueError("simulated Loki error")
        import re
        minutes = int(re.search(r'\[(\d+)m\]', query).group(1))
        count = sum(self.now_ns - minutes * 60_000_000_000 <= t <= self.now_ns
                    for t, line in self.events)
        return {"resultType":"vector","result":[{"value":[1,str(count)]}]}


def build_rule(**updates):
    return {"id": "rule1", **clean_rule({"name":"Kodi crashes", "host":"CoreELEC", **updates})}


def build_engine(now):
    events = []
    bus = SimpleNamespace(async_fire=lambda kind, data: events.append((kind, data)))
    hass = SimpleNamespace(bus=bus)
    return FakeAPI(now), MemoryStore(), hass, events


def test_rule_sanitization_and_canonical_severity():
    rule = build_rule(severity="errors", text="bind()", threshold=3, window_minutes=5)
    query = make_rule_query(rule)
    assert CANONICAL_SEVERITY_TEMPLATE in query
    assert '{host="CoreELEC"}' in query
    assert ' |= "bind()"' in query
    assert ' | severity=~"(?i)^(emergency|alert|critical|fatal|error)$"' in query
    for bad in ({"threshold": 0}, {"window_minutes": 1441}, {"regex":"["},
                {"severity":"garbage"}, {"host":"ab\ncd"}, {"enabled":"yes"}):
        with pytest.raises(ValueError):
            build_rule(**bad)


@pytest.mark.asyncio
async def test_preview_count_does_not_emit_events():
    now=900_000_000_000_000
    api, store, hass, events = build_engine(now)
    api.events.append((now-20_000_000_000, "some error"))
    rule=build_rule()
    assert await preview_rule(api, rule) == 1
    assert not events
    assert not store.writes


@pytest.mark.asyncio
async def test_alert_new_log_threshold_cooldown_and_restart(monkeypatch):
    now = 900_000_000_000_000
    monkeypatch.setattr("loki_monitor.alerts.time.time_ns", lambda: now)
    api, store, hass, events = build_engine(now)
    rule=build_rule(threshold=3, window_minutes=5, cooldown_minutes=15)
    # Historical logs must not trigger alerts when the rule is first enabled.
    api.events = [(now-30_000_000_000, "old"), (now-20_000_000_000, "old")]
    engine=LokiAlertEngine(hass,api,"entry1",store)
    await engine.async_initialize([rule])
    assert (await engine.async_check([rule]))["alerts_triggered"] == 0
    assert store.writes == 1  # no rewrites in quiet polling
    # Third match arrives after setup. The count window includes older logs.
    api.events.append((now+10_000_000_000, "NEW CREDENTIAL=secret"))
    api.now_ns = now+20_000_000_000
    monkeypatch.setattr("loki_monitor.alerts.time.time_ns", lambda: api.now_ns)
    result=await engine.async_check([rule])
    assert result["alerts_triggered"] == 1
    assert events[0][0] == EVENT_ALERT
    assert events[0][1]["rule_id"] == "rule1"
    assert events[0][1]["count"] == 3
    assert "message" not in events[0][1]
    assert (await engine.async_check([rule]))["alerts_triggered"] == 0
    # State persisted: restart should not replay alert.
    restarted=LokiAlertEngine(hass,api,"entry1",store)
    await restarted.async_initialize([rule])
    assert (await restarted.async_check([rule]))["alerts_triggered"] == 0
    # New log during cooldown is marked seen but must not trigger.
    api.now_ns += 60_000_000_000
    monkeypatch.setattr("loki_monitor.alerts.time.time_ns",lambda:api.now_ns)
    api.events.append((api.now_ns, "another error"))
    assert (await restarted.async_check([rule]))["alerts_triggered"] == 0
    # After cooldown, another new log can generate an event.
    api.now_ns += 16 * 60_000_000_000
    monkeypatch.setattr("loki_monitor.alerts.time.time_ns",lambda:api.now_ns)
    api.events.extend([(api.now_ns-30_000_000_000,"error"),
                       (api.now_ns-20_000_000_000,"error"),
                       (api.now_ns-10_000_000_000,"error")])
    assert (await restarted.async_check([rule]))["alerts_triggered"] == 1
    assert len(events)==2


@pytest.mark.asyncio
async def test_rule_failures_dont_fire_or_move_checkpoint(monkeypatch):
    now=900_000_000_000_000
    monkeypatch.setattr("loki_monitor.alerts.time.time_ns",lambda:now)
    api, store, hass, events=build_engine(now)
    rule=build_rule(threshold=1)
    engine=LokiAlertEngine(hass,api,"entry1",store)
    await engine.async_initialize([rule])
    original=dict(store.data["states"]["rule1"])
    api.events.append((now+1_000_000_000,"fresh error"))
    api.now_ns=now+2_000_000_000
    monkeypatch.setattr("loki_monitor.alerts.time.time_ns",lambda:api.now_ns)
    api.bad=True
    result=await engine.async_check([rule])
    assert result["alert_rule_failures"] == ["rule1"]
    assert result["alerts_triggered"] == 0
    assert store.data["states"]["rule1"] == original
    api.bad=False
    assert (await engine.async_check([rule]))["alerts_triggered"] == 1
    assert len(events)==1


@pytest.mark.asyncio
async def test_opt_in_message_and_no_event_for_disabled_rule(monkeypatch):
    now=900_000_000_000_000
    monkeypatch.setattr("loki_monitor.alerts.time.time_ns",lambda:now)
    api,store,hass,events=build_engine(now)
    rule=build_rule(threshold=1,include_message=True)
    engine=LokiAlertEngine(hass,api,"entry1",store)
    await engine.async_initialize([rule])
    api.now_ns=now+10_000_000_000
    monkeypatch.setattr("loki_monitor.alerts.time.time_ns",lambda:api.now_ns)
    api.events.append((now+5_000_000_000,"hello world"))
    assert (await engine.async_check([{**rule,"enabled":False}]))["alerts_triggered"] == 0
    assert not api.query_calls
    assert (await engine.async_check([rule]))["alerts_triggered"] == 1
    assert events[-1][1]["message"] == "hello world"

@pytest.mark.asyncio
async def test_storage_failure_does_not_acknowledge_or_emit(monkeypatch):
    now=900_000_000_000_000
    monkeypatch.setattr("loki_monitor.alerts.time.time_ns",lambda:now)
    api, store, hass, events=build_engine(now)
    rule=build_rule(threshold=1)
    engine=LokiAlertEngine(hass,api,"entry1",store)
    await engine.async_initialize([rule])
    api.now_ns=now+2_000_000_000
    monkeypatch.setattr("loki_monitor.alerts.time.time_ns",lambda:api.now_ns)
    api.events.append((now+1_000_000_000,"new error"))
    previous=dict(store.data["states"]["rule1"])
    store.fail_save=True
    with pytest.raises(OSError):
        await engine.async_check([rule])
    assert not events
    assert engine._states["rule1"] == previous
    store.fail_save=False
    assert (await engine.async_check([rule]))["alerts_triggered"] == 1
    assert len(events)==1
