"""Exact-window validation and Prometheus query regressions (no live servers)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from loki_monitor.time_range import (
    InvalidExactRange, MAX_RANGE_NS, validate_cursor, validate_exact_range,
)


def _ns(date: datetime) -> str:
    return str(int(date.timestamp()) * 1_000_000_000)


def test_historical_exact_window_and_cursor(monkeypatch):
    import loki_monitor.time_range as dates
    monkeypatch.setattr(dates.time, 'time_ns', lambda: 1_900_000_000_000_000_000)
    a = _ns(datetime(2026, 10, 28, 15, 0, 25, tzinfo=timezone.utc))
    b = _ns(datetime(2026, 10, 28, 15, 50, 20, tzinfo=timezone.utc))
    start, end = validate_exact_range(a, b)
    assert (end - start) // 1_000_000_000 == 2995
    assert validate_cursor(None, start, end) == end
    assert validate_cursor(str(end - 1), start, end) == end - 1
    with pytest.raises(InvalidExactRange):
        validate_cursor(str(end + 1), start, end)
    with pytest.raises(InvalidExactRange):
        validate_cursor(str(start - 1), start, end)


def test_exact_range_limits(monkeypatch):
    import loki_monitor.time_range as dates
    monkeypatch.setattr(dates.time, 'time_ns', lambda: 1_900_000_000_000_000_000)
    a = 1_800_000_000_000_000_000
    assert validate_exact_range(str(a), str(a + MAX_RANGE_NS))[1] == a + MAX_RANGE_NS
    for begin, end in [
        ('abc', str(a)), (str(a), str(a)), (str(a + 1), str(a + 65_000_000_000)),
        (str(a), str(a+59_000_000_000)), (str(a), str(a+MAX_RANGE_NS+1_000_000_000)),
        (str(a), '1900000900000000000'), ('', ''),
    ]:
        with pytest.raises(InvalidExactRange):
            validate_exact_range(begin, end)


def test_prometheus_query_uses_anchor_and_exact_seconds(monkeypatch):
    import loki_monitor.time_range as dates
    monkeypatch.setattr(dates.time, 'time_ns', lambda: 1_900_000_000_000_000_000)
    from loki_monitor.prometheus import PrometheusClient
    from datetime import datetime, timezone
    start=_ns(datetime(2026, 10, 28, 15, 0, 25, tzinfo=timezone.utc))
    end=_ns(datetime(2026, 10, 28, 15, 50, 20, tzinfo=timezone.utc))
    class FakeClient(PrometheusClient):
        async def _query(self, query, *, at_seconds=None):
            self.called=(query, at_seconds)
            return {'status':'success','data':{'resultType':'vector','result':[
                {'metric':{'host':'CoreELEC','service_name':'kodi','severity_group':'high'},
                 'value':[int(end)//1_000_000_000,'3.5']}
            ]}}
    client=FakeClient(None,'http://prometheus:9090')
    import asyncio
    result=asyncio.run(client.async_counter_exact_range(start,end))
    assert result[('CoreELEC','kodi','high')]==3.5
    assert client.called[1]==int(end)//1_000_000_000
    assert '[2995s]' in client.called[0]
    assert 'offset 2995s' in client.called[0]
    assert 'min_over_time' in client.called[0]
