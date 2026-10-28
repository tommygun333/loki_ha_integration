"""Regression tests for no background Loki requests and compact count summaries."""
from __future__ import annotations
import importlib
import sys
from types import ModuleType
from typing import Generic, TypeVar
import pytest
T = TypeVar('T')

class FakeUpdateCoordinator(Generic[T]):
    def __init__(self, hass, logger, **kwargs):
        self.name = kwargs['name']
        self.update_interval = kwargs['update_interval']

class UpdateFailed(Exception): pass
class ConfigEntryAuthFailed(Exception): pass

@pytest.fixture
def coordinator_class(monkeypatch):
    ha=ModuleType('homeassistant');ha.__path__=[]
    core=ModuleType('homeassistant.core');core.HomeAssistant=object
    exc=ModuleType('homeassistant.exceptions');exc.ConfigEntryAuthFailed=ConfigEntryAuthFailed
    helpers=ModuleType('homeassistant.helpers');helpers.__path__=[]
    uc=ModuleType('homeassistant.helpers.update_coordinator')
    uc.DataUpdateCoordinator=FakeUpdateCoordinator;uc.UpdateFailed=UpdateFailed
    for k,v in [('homeassistant',ha),('homeassistant.core',core),('homeassistant.exceptions',exc),
                ('homeassistant.helpers',helpers),('homeassistant.helpers.update_coordinator',uc)]:
        monkeypatch.setitem(sys.modules,k,v)
    sys.modules.pop('loki_monitor.coordinator',None)
    mod=importlib.import_module('loki_monitor.coordinator')
    yield mod.LokiCoordinator
    sys.modules.pop('loki_monitor.coordinator',None)

class NoLokiCalls:
    async def async_query(self,*a,**kw):
        raise AssertionError('Unexpected scheduled LogQL query')
    async def async_labels(self,*a,**kw):
        raise AssertionError('Unexpected scheduled Loki label query')
    async def async_recent_logs(self,*a,**kw):
        raise AssertionError('Unexpected latest-error query')

class FakePrometheus:
    def __init__(self, data=None, failing=()):
        self.data = data or {
            ('CoreELEC','kodi','high'): 2.4,
            ('CoreELEC','kodi','warning'): 5,
            ('OPNsense.internal','opnsense-system','high'): 3,
            ('CoreELEC','kodi','normal'): 20,
        }
        self.failing=set(failing);self.windows=[]
    async def async_counter_window(self,window):
        self.windows.append(window)
        if window in self.failing: raise RuntimeError('Offline')
        return self.data

@pytest.mark.asyncio
async def test_fleet_host_service_summary_uses_only_two_prometheus_queries(coordinator_class):
    prom=FakePrometheus()
    options={'monitors':[
        {'id':'a','host':'CoreELEC','name':'Kodi box','service':''},
        {'id':'b','host':'CoreELEC','name':'Kodi service','service':'kodi'},
        {'id':'c','host':'missing','name':'Missing','service':''}]}
    result=await coordinator_class(None, NoLokiCalls(),options,prometheus=prom)._async_update_data()
    assert sorted(prom.windows)==['1h','24h']
    assert result['fleet_summary']['high_1h']==5
    assert result['fleet_summary']['high_24h']==5
    assert result['fleet_summary']['normal_1h']==20
    assert result['monitor_summaries']['a']['high_1h']==2
    assert result['monitor_summaries']['b']['warning_24h']==5
    assert result['monitor_summaries']['c']['high_1h']==0
    assert result['prometheus_available'] is True

@pytest.mark.asyncio
async def test_partial_prometheus_failure_marks_window_unavailable(coordinator_class):
    result=await coordinator_class(None,NoLokiCalls(),{},prometheus=FakePrometheus(failing=['24h']))._async_update_data()
    assert result['fleet_summary']['high_1h'] is not None
    assert result['fleet_summary']['high_24h'] is None
    assert result['prometheus_available'] is False

@pytest.mark.asyncio
async def test_both_prometheus_queries_failing_is_update_failure(coordinator_class):
    coord=coordinator_class(None,NoLokiCalls(),{},prometheus=FakePrometheus(failing=['1h','24h']))
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()

@pytest.mark.asyncio
async def test_custom_query_is_disabled_by_default(coordinator_class):
    coord=coordinator_class(None,NoLokiCalls(),{'custom_queries':[{'id':'test','query':'expensive'}]},prometheus=FakePrometheus())
    result=await coord._async_update_data()
    assert result['custom_test'] is None
