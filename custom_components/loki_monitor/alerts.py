"""Validated alert rules and persistent, read-only Loki alert evaluation.

Rules alert only when the newest matching log is newer than the persisted
checkpoint AND the count in the rule's rolling window meets its threshold.
No raw LogQL is accepted from a configuration form.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from .logic import flatten_streams
from .viewer import SEVERITY_PATTERNS, ViewerInputError, build_viewer_query

_LOGGER = logging.getLogger(__name__)
CONF_ALERT_RULES = "alert_rules"
MAX_ALERT_RULES = 12
EVENT_ALERT = "loki_monitor_alert"
DEFAULT_RULE = {
    "enabled": True,
    "host": "",
    "source": "",
    "service": "",
    "program": "",
    "severity": "errors",
    "text": "",
    "regex": "",
    "threshold": 3,
    "window_minutes": 5,
    "cooldown_minutes": 15,
    "include_message": False,
}
RULE_FIELDS = tuple(DEFAULT_RULE)
_NS_MINUTE = 60_000_000_000


def clean_rule(values: dict[str, Any]) -> dict[str, Any]:
    """Sanitize and bound rule settings from the options form."""
    merged = {**DEFAULT_RULE, **values}
    name = merged.get("name", "")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128:
        raise ValueError("A rule name is required (max 128 characters)")
    if any(ch in name for ch in "\r\n\x00"):
        raise ValueError("Invalid rule name")
    result: dict[str, Any] = {"name": name.strip()}
    for key in ("host", "source", "service", "program", "text", "regex"):
        value = merged[key]
        if not isinstance(value, str) or len(value) > (256 if key in ("text", "regex") else 128):
            raise ValueError(f"Invalid {key}")
        if any(ch in value for ch in "\r\n\x00"):
            raise ValueError(f"Invalid {key}")
        result[key] = value.strip()
    if result["regex"]:
        try:
            re.compile(result["regex"])
        except re.error as err:
            raise ValueError("Invalid regex") from err
    if merged["severity"] not in SEVERITY_PATTERNS:
        raise ValueError("Invalid severity")
    result["severity"] = merged["severity"]
    for key, lo, hi in (("threshold", 1, 100), ("window_minutes", 1, 1440),
                        ("cooldown_minutes", 0, 10080)):
        number = merged[key]
        if isinstance(number, bool) or not isinstance(number, int) or not lo <= number <= hi:
            raise ValueError(f"Invalid {key}")
        result[key] = number
    for key in ("enabled", "include_message"):
        if not isinstance(merged[key], bool):
            raise ValueError(f"Invalid {key}")
        result[key] = merged[key]
    # The same bounded, escaped query generator used by the native viewer.
    make_rule_query(result)
    return result


def make_rule_query(rule: dict[str, Any], host_label: str = "host") -> str:
    """Use normalized indexed Alloy severity (no per-query detection)."""
    return build_viewer_query(
        host_label=host_label,
        host=rule.get("host", ""), source=rule.get("source", ""),
        service=rule.get("service", ""), program=rule.get("program", ""),
        severity=rule.get("severity", "errors"), text=rule.get("text", ""),
        regex=rule.get("regex", ""), severity_mode="indexed",
    )


async def test_alert_rule(api, rule: dict[str, Any], host_label: str = 'host') -> float:
    """Preview bounded matching records, capped at the configured threshold."""
    now_ns = time.time_ns()
    result = await api.async_query_streams_ns(
        make_rule_query(rule, host_label),
        start_ns=max(0, now_ns - rule['window_minutes'] * _NS_MINUTE),
        end_ns=now_ns, limit=rule['threshold'],
    )
    return float(len(flatten_streams(result, rule['threshold'])))


class LokiAlertEngine:
    """Per-entry stateful alert processor. Call check() from the shared poll."""

    def __init__(self, hass, api, entry_id: str, store, *, host_label: str = "host"):
        self.hass = hass
        self.api = api
        self.entry_id = entry_id
        self.store = store
        self.host_label = host_label
        self._states: dict[str, dict[str, int]] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    async def async_initialize(self, rules: list[dict[str, Any]]) -> None:
        saved = await self.store.async_load() or {}
        restored = saved.get("states", {}) if isinstance(saved, dict) else {}
        now = time.time_ns()
        ids = {rule["id"] for rule in rules}
        self._states = {
            key: {
                "seen_ns": int(value.get("seen_ns", now)),
                "fired_ns": int(value.get("fired_ns", 0)),
            }
            for key, value in restored.items()
            if key in ids and isinstance(value, dict)
        }
        # New rules begin at creation time, not at the first successful query.
        for rule_id in ids:
            self._states.setdefault(rule_id, {"seen_ns": now, "fired_ns": 0})
        self._loaded = True
        await self.store.async_save({"states": self._states})

    async def async_check(self, rules: list[dict[str, Any]]) -> dict[str, Any]:
        """Check new matches and persist the checkpoint BEFORE emitting events."""
        if not self._loaded:
            raise RuntimeError("Alert engine must be initialized before polling")
        failures: list[str] = []
        triggered = 0
        if not rules:
            return {"alert_rule_failures": failures, "alerts_triggered": 0}
        async with self._lock:
            now = time.time_ns()
            previous_states = {key: dict(value) for key, value in self._states.items()}
            # A per-entry concurrency bound limits the additional Loki load.
            limit = asyncio.Semaphore(3)

            async def evaluate(rule):
                async with limit:
                    return await self._check_rule(rule, now)

            outcomes = await asyncio.gather(
                *(evaluate(rule) for rule in rules if rule.get("enabled", True)),
                return_exceptions=True,
            )
            enabled = [rule for rule in rules if rule.get("enabled", True)]
            pending: list[dict[str, Any]] = []
            for rule, result in zip(enabled, outcomes, strict=True):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, BaseException):
                    failures.append(rule["id"])
                    _LOGGER.warning("Loki alert rule %s check failed (%s)", rule["id"], type(result).__name__)
                elif result is not None:
                    pending.append(result)
            # Persist seen/fired markers before events. This favors not replaying
            # duplicate events after restart over at-least-once delivery.
            if self._states != previous_states:
                try:
                    await self.store.async_save({"states": self._states})
                except Exception:
                    # Do not acknowledge logs in memory if the durable
                    # checkpoint could not be saved. Never emit an event first.
                    self._states = previous_states
                    raise
            for data in pending:
                self.hass.bus.async_fire(EVENT_ALERT, data)
                triggered += 1
        return {"alert_rule_failures": failures, "alerts_triggered": triggered}

    async def _check_rule(self, rule: dict[str, Any], now_ns: int) -> dict[str, Any] | None:
        rule_id = rule["id"]
        state = self._states[rule_id]
        window_ns = rule["window_minutes"] * _NS_MINUTE
        start_ns = max(0, now_ns - window_ns)
        query = make_rule_query(rule, self.host_label)
        # One bounded Loki stream query replaces the old count_over_time scan.
        # We only need to establish whether threshold matching lines exist.
        if not 1 <= rule['threshold'] <= 100:
            raise ValueError('Alert threshold must be between 1 and 100')
        recent = await self.api.async_query_streams_ns(
            query, start_ns=start_ns, end_ns=now_ns, limit=rule['threshold']
        )
        items = flatten_streams(recent, rule['threshold'])
        if not items:
            return None
        newest = int(items[0]['timestamp_ns'])
        if newest <= state['seen_ns']:
            return None
        state['seen_ns'] = newest
        count = len(items)  # lower bound, capped at threshold
        if count < rule['threshold']:
            return None
        if now_ns < state["fired_ns"] + rule["cooldown_minutes"] * _NS_MINUTE:
            return None
        state["fired_ns"] = now_ns
        data: dict[str, Any] = {
            "entry_id": self.entry_id,
            "rule_id": rule_id,
            "name": rule["name"],
            "severity": rule["severity"],
            "host": rule.get("host", ""),
            "service": rule.get("service", ""),
            "source": rule.get("source", ""),
            "program": rule.get("program", ""),
            "count": int(count),
            "threshold": rule["threshold"],
            "window_minutes": rule["window_minutes"],
            "last_match_ns": str(newest),
        }
        if rule.get("include_message", False):
            data["message"] = items[0]["line"][:500]
        return data
