"""Coordinator contracts: no event count ever reads Loki, including services."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Generic, TypeVar

import pytest

from loki_monitor.api import LokiAuthError, LokiQueryError
from loki_monitor.prometheus import PrometheusError

T = TypeVar('T')


class FakeDataUpdateCoordinator(Generic[T]):
    def __init__(self, hass, logger, **kwargs):
        self.name = kwargs['name']
        self.update_interval = kwargs['update_interval']


class FakeUpdateFailed(Exception):
    pass


class FakeConfigEntryAuthFailed(Exception):
    pass


@pytest.fixture
def coordinator_class(monkeypatch):
    ha = ModuleType('homeassistant'); ha.__path__ = []
    core = ModuleType('homeassistant.core'); core.HomeAssistant = type('HomeAssistant', (), {})
    exceptions = ModuleType('homeassistant.exceptions'); exceptions.ConfigEntryAuthFailed = FakeConfigEntryAuthFailed
    helpers = ModuleType('homeassistant.helpers'); helpers.__path__ = []
    updates = ModuleType('homeassistant.helpers.update_coordinator')
    updates.DataUpdateCoordinator = FakeDataUpdateCoordinator
    updates.UpdateFailed = FakeUpdateFailed
    for name, module in [
        ('homeassistant', ha), ('homeassistant.core', core),
        ('homeassistant.exceptions', exceptions), ('homeassistant.helpers', helpers),
        ('homeassistant.helpers.update_coordinator', updates),
    ]:
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules.pop('loki_monitor.coordinator', None)
    cls = importlib.import_module('loki_monitor.coordinator').LokiCoordinator
    yield cls
    sys.modules.pop('loki_monitor.coordinator', None)


class FakeLoki:
    def __init__(self, broken_custom=False, failed_auth=False):
        self.queries = []
        self.recent_queries = []
        self.broken_custom = broken_custom
        self.failed_auth = failed_auth

    async def async_query(self, query):
        self.queries.append(query)
        if self.broken_custom:
            raise LokiQueryError('broken custom query')
        return {'resultType': 'vector', 'result': [{'value': [100, '7']} ]}

    async def async_recent_logs(self, query, **kwargs):
        self.recent_queries.append((query, kwargs))
        if self.failed_auth:
            raise LokiAuthError('auth')
        return {'resultType': 'streams', 'result': [
            {'stream': {'host': 'pve'}, 'values': [['3000000000', 'error']]}
        ]}

    async def async_labels(self):
        return ['host', 'level', 'service_name']


class FakePrometheus:
    def __init__(self, fail_window=None):
        self.calls = []
        self.fail_window = fail_window

    async def async_counter_window(self, window):
        self.calls.append(window)
        if self.fail_window == window:
            raise PrometheusError('offline')
        scale = 1 if window == '1h' else 10
        return {
            ('pve', 'pvedaemon.service', 'high'): 2 * scale,
            ('pve', 'systemd-journald.service', 'warning'): 4 * scale,
            ('pve', 'pvedaemon.service', 'normal'): 100 * scale,
            ('CoreELEC', 'kodi', 'high'): 1 * scale,
            ('CoreELEC', 'kodi', 'unknown'): 5 * scale,
        }


@pytest.mark.asyncio
async def test_prometheus_only_counts_for_fleet_hosts_and_services(coordinator_class):
    loki, prom = FakeLoki(), FakePrometheus()
    monitors = [
        {'id': 'host-pve', 'name': 'PVE', 'host': 'pve', 'service': ''},
        {'id': 'svc-pve', 'name': 'PVE Daemon', 'host': 'pve', 'service': 'pvedaemon.service'},
        {'id': 'svc-kodi', 'name': 'Kodi', 'host': 'CoreELEC', 'service': 'kodi'},
    ]
    result = await coordinator_class(None, loki, {'monitors': monitors}, prometheus=prom)._async_update_data()
    assert prom.calls == ['1h', '24h']
    assert loki.queries == []  # No Loki count_over_time for ANY monitored host/service
    assert result['errors_1h'] == 3
    assert result['errors_24h'] == 30
    assert result['warnings_1h'] == 4
    assert result['warnings_24h'] == 40
    assert result['monitor_host-pve_errors_1h'] == 2
    assert result['monitor_host-pve_warnings_24h'] == 40
    assert result['monitor_svc-pve_errors_24h'] == 20
    assert result['monitor_svc-pve_warnings_1h'] == 0
    assert result['monitor_svc-kodi_errors_1h'] == 1
    assert result['active_hosts'] == 2
    assert result['hosts'] == ['CoreELEC', 'pve']
    assert result['last_error'].timestamp() == 3
    assert result['failed_query_count'] == 0


@pytest.mark.asyncio
async def test_failed_prometheus_24h_never_falls_back_to_loki(coordinator_class):
    loki, prom = FakeLoki(), FakePrometheus(fail_window='24h')
    monitored = {'monitors': [{'id': 'test', 'host': 'pve', 'name': 'PVE', 'service': ''}]}
    result = await coordinator_class(None, loki, monitored, prometheus=prom)._async_update_data()
    assert result['errors_1h'] == 3
    assert result['errors_24h'] is None
    assert result['monitor_test_errors_24h'] is None
    assert 'errors_24h' in result['failed_queries']
    assert 'monitor_test_errors_24h' in result['failed_queries']
    assert 'prometheus_24h' in result['failed_queries']
    assert loki.queries == []


@pytest.mark.asyncio
async def test_custom_logql_stays_on_loki_and_is_isolated(coordinator_class):
    loki, prom = FakeLoki(broken_custom=True), FakePrometheus()
    result = await coordinator_class(None, loki, {
        'custom_queries': [{'id': 'custom-id', 'name': 'Custom', 'query': 'vector(1)'}]
    }, prometheus=prom)._async_update_data()
    assert loki.queries == ['vector(1)']
    assert result['custom_custom-id'] is None
    assert result['errors_24h'] == 30
    assert 'custom_custom-id' in result['failed_queries']


@pytest.mark.asyncio
async def test_all_loki_queries_can_fail_without_zeroing_counts(coordinator_class):
    class Offline(FakeLoki):
        async def async_recent_logs(self, query, **kwargs):
            raise LokiQueryError('offline')
        async def async_labels(self):
            raise LokiQueryError('offline')
    result = await coordinator_class(None, Offline(), {}, prometheus=FakePrometheus())._async_update_data()
    assert result['errors_24h'] == 30
    assert result['last_error'] is None
    assert 'last_error' in result['failed_queries']


@pytest.mark.asyncio
async def test_auth_failure_requests_reauthentication(coordinator_class):
    with pytest.raises(FakeConfigEntryAuthFailed):
        await coordinator_class(None, FakeLoki(failed_auth=True), {}, prometheus=FakePrometheus())._async_update_data()


@pytest.mark.asyncio
async def test_alert_failure_isolated_and_does_not_break_counters(coordinator_class):
    class BrokenAlertEngine:
        async def async_check(self, rules):
            raise RuntimeError('error')
    data = await coordinator_class(
        None, FakeLoki(), {'alert_rules': [{'id': 'broken'}]},
        alerts=BrokenAlertEngine(), prometheus=FakePrometheus(),
    )._async_update_data()
    assert data['errors_24h'] == 30
    assert data['alert_rule_failures_count'] == 1


def test_prometheus_dependency_required(coordinator_class):
    with pytest.raises(ValueError, match='Prometheus'):
        coordinator_class(None, FakeLoki(), {})
