"""Exercise coordinator behavior using lightweight HA API stubs."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Generic, TypeVar

T = TypeVar("T")

import pytest

from loki_monitor.api import LokiAuthError, LokiQueryError


class FakeDataUpdateCoordinator(Generic[T]):
    def __init__(self, hass, logger, **kwargs):
        self.name = kwargs["name"]
        self.update_interval = kwargs["update_interval"]


class FakeUpdateFailed(Exception):
    pass


class FakeConfigEntryAuthFailed(Exception):
    pass


@pytest.fixture
def coordinator_class(monkeypatch):
    ha = ModuleType("homeassistant")
    ha.__path__ = []
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    exceptions = ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = FakeConfigEntryAuthFailed
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    update_coordinator = ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = FakeDataUpdateCoordinator
    update_coordinator.UpdateFailed = FakeUpdateFailed
    for name, value in [
        ("homeassistant", ha),
        ("homeassistant.core", core),
        ("homeassistant.exceptions", exceptions),
        ("homeassistant.helpers", helpers),
        ("homeassistant.helpers.update_coordinator", update_coordinator),
    ]:
        monkeypatch.setitem(sys.modules, name, value)
    sys.modules.pop("loki_monitor.coordinator", None)
    cls = importlib.import_module("loki_monitor.coordinator").LokiCoordinator
    yield cls
    sys.modules.pop("loki_monitor.coordinator", None)


class FakeAPI:
    def __init__(self, *, broken_custom=False, auth_failure=False):
        self.broken_custom = broken_custom
        self.auth_failure = auth_failure
        self.metric_queries = []
        self.recent_queries = []

    async def async_query(self, query):
        self.metric_queries.append(query)
        if self.auth_failure:
            raise LokiAuthError("bad credentials")
        if "my_custom_metric" in query and self.broken_custom:
            raise LokiQueryError("bad query")
        return {"resultType": "vector", "result": [{"value": [1, "9"]}]}

    async def async_recent_logs(self, query, **kwargs):
        self.recent_queries.append((query, kwargs))
        return {"resultType": "streams", "result": [
            {"stream": {"host": "pve"}, "values": [["3000000000", "error!"]]}
        ]}

    async def async_labels(self):
        return ["host", "level", "service_name"]

    async def async_label_values(self, label):
        return ["CoreELEC", "pve"]


@pytest.mark.asyncio
async def test_builtin_and_custom_queries(coordinator_class):
    coordinator = coordinator_class(None, FakeAPI(), {
        "custom_queries": [{"id": "test", "name": "My custom", "query": "my_custom_metric"}]
    })
    result = await coordinator._async_update_data()
    assert result["errors_1h"] == 9
    assert result["warnings_24h"] == 9
    assert result["active_hosts"] == 2
    assert result["severity_label_present"] is True
    assert result["custom_test"] == 9
    assert result["last_error"].timestamp() == 3


@pytest.mark.asyncio
async def test_invalid_custom_query_is_isolated(coordinator_class):
    coordinator = coordinator_class(None, FakeAPI(broken_custom=True), {
        "custom_queries": [{"id": "test", "name": "Broken custom", "query": "my_custom_metric"}]
    })
    result = await coordinator._async_update_data()
    assert result["errors_1h"] == 9
    assert result["custom_test"] is None
    assert "custom_test" in coordinator._failing_queries


@pytest.mark.asyncio
async def test_auth_errors_request_reauth(coordinator_class):
    coordinator = coordinator_class(None, FakeAPI(auth_failure=True), {})
    with pytest.raises(FakeConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_default_builtins_use_detected_severity_and_dashboard_scope(coordinator_class):
    api = FakeAPI()
    coordinator = coordinator_class(None, api, {})
    await coordinator._async_update_data()
    assert any('detected_level=~"(?i)warn"[1h]' in q for q in api.metric_queries)
    assert any('detected_level=~"(?i)warn"[24h]' in q for q in api.metric_queries)
    assert any('detected_level=~"(?i)(error|critical|fatal)"[24h]' in q for q in api.metric_queries)
    for query in api.metric_queries:
        assert '{host=~".+",source=~".*",service_name=~".*",syslog_identifier=~".*"}' in query
    assert 'detected_level=~"(?i)(error|critical|fatal)"' in api.recent_queries[0][0]
    assert api.recent_queries[0][1] == {"minutes": 1440, "limit": 1}


@pytest.mark.asyncio
async def test_legacy_indexed_level_mode_retained(coordinator_class):
    api = FakeAPI()
    coordinator = coordinator_class(None, api, {
        "severity_source": "indexed",
        "level_label": "custom_level",
        "warn_levels": "warning,alert",
    })
    await coordinator._async_update_data()
    assert any('{custom_level=~"(?i)(warning|alert)"}[24h]' in q for q in api.metric_queries)
    assert not any('detected_level' in q for q in api.metric_queries)
    assert not any('detected_level' in query for query, _ in api.recent_queries)


@pytest.mark.asyncio
async def test_custom_sensor_unchanged_by_severity_source(coordinator_class):
    api = FakeAPI()
    query = 'sum(count_over_time({job="custom"}[15m])) or vector(0)'
    coordinator = coordinator_class(None, api, {
        "custom_queries": [{"id":"test", "name":"My metric", "query":query}],
    })
    await coordinator._async_update_data()
    assert query in api.metric_queries


@pytest.mark.asyncio
async def test_scoped_monitor_counts_and_stable_ids(coordinator_class):
    api = FakeAPI()
    options = {"monitors": [{"id": "abc", "name": "Kodi", "host": "CoreELEC", "service": "kodi"}]}
    coordinator = coordinator_class(None, api, options)
    result = await coordinator._async_update_data()
    assert result["monitor_abc_errors_1h"] == 9
    assert result["monitor_abc_errors_24h"] == 9
    assert result["monitor_abc_warnings_1h"] == 9
    assert result["monitor_abc_warnings_24h"] == 9
    assert sum('{host="CoreELEC",service_name="kodi"}' in q for q in api.metric_queries) == 4


@pytest.mark.asyncio
async def test_individual_builtin_failure_isolated(coordinator_class):
    class PartlyBrokenAPI(FakeAPI):
        async def async_query(self, query):
            if 'detected_level=~"(?i)warn"[24h]' in query:
                raise LokiQueryError("warning metric failed")
            return await super().async_query(query)

        async def async_label_values(self, label):
            raise LokiQueryError("host lookup failed")

    coordinator = coordinator_class(None, PartlyBrokenAPI(), {})
    result = await coordinator._async_update_data()
    assert result["errors_1h"] == 9
    assert result["warnings_1h"] == 9
    assert result["warnings_24h"] is None
    assert result["active_hosts"] is None
    assert "warnings_24h" in result["failed_queries"]
    assert "active_hosts" in result["failed_queries"]
    assert result["failed_query_count"] >= 2


@pytest.mark.asyncio
async def test_failed_monitor_does_not_break_builtins(coordinator_class):
    class PartlyBrokenAPI(FakeAPI):
        async def async_query(self, query):
            if 'host="FailingHost"' in query:
                raise LokiQueryError("bad monitor query")
            return await super().async_query(query)

    coordinator = coordinator_class(None, PartlyBrokenAPI(), {
        "monitors": [{"id":"xyz", "name":"Failing", "host":"FailingHost", "service":""}]
    })
    result = await coordinator._async_update_data()
    assert result["errors_1h"] == 9
    assert result["monitor_xyz_errors_24h"] is None
    assert "monitor_xyz_errors_24h" in result["failed_queries"]


@pytest.mark.asyncio
async def test_complete_loki_outage_marks_update_failed(coordinator_class):
    class OfflineAPI(FakeAPI):
        async def async_query(self, query):
            raise LokiQueryError("offline")

        async def async_recent_logs(self, query, **kwargs):
            raise LokiQueryError("offline")

        async def async_labels(self):
            raise LokiQueryError("offline")

        async def async_label_values(self, label):
            raise LokiQueryError("offline")

    coordinator = coordinator_class(None, OfflineAPI(), {})
    with pytest.raises(FakeUpdateFailed):
        await coordinator._async_update_data()
