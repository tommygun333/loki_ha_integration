"""Verify upgrade removes only obsolete entities belonging to this config entry."""
from __future__ import annotations
import importlib,sys
from types import ModuleType,SimpleNamespace
import pytest

class Registry:
    def __init__(self,entries):self.entries=entries;self.removed=[]
    def async_remove(self,entity_id):
        self.removed.append(entity_id)
        self.entries=[x for x in self.entries if x.entity_id!=entity_id]

def registry_entry(entry_id,key,platform='loki_monitor',domain='sensor'):
    return SimpleNamespace(entity_id=f'{domain}.{entry_id}_{key}_{platform}',unique_id=f'{entry_id}_{key}',
                           platform=platform,domain=domain,config_entry_id=entry_id)

@pytest.fixture
def sensor_module(monkeypatch):
    ha=ModuleType('homeassistant');ha.__path__=[]
    comp=ModuleType('homeassistant.components');comp.__path__=[]
    sensor=ModuleType('homeassistant.components.sensor');sensor.SensorEntity=type('SensorEntity',(),{})
    sensor.SensorStateClass=SimpleNamespace(MEASUREMENT='measurement')
    ce=ModuleType('homeassistant.config_entries');ce.ConfigEntry=object
    core=ModuleType('homeassistant.core');core.HomeAssistant=object
    helpers=ModuleType('homeassistant.helpers');helpers.__path__=[]
    ep=ModuleType('homeassistant.helpers.entity_platform');ep.AddEntitiesCallback=object
    ent=ModuleType('homeassistant.helpers.entity');ent.EntityCategory=SimpleNamespace(DIAGNOSTIC='diagnostic')
    er=ModuleType('homeassistant.helpers.entity_registry')
    er.async_get=lambda hass:hass.registry
    er.async_entries_for_config_entry=lambda reg,entry_id:[x for x in reg.entries if x.config_entry_id==entry_id]
    le=ModuleType('loki_monitor.entity')
    class FakeLokiEntity:
        def __init__(self,coordinator,entry_id,key):
            self.coordinator=coordinator;self.key=key;self.unique_id=f'{entry_id}_{key}'
        @property
        def available(self):return True
    le.LokiEntity=FakeLokiEntity
    for name,value in [('homeassistant',ha),('homeassistant.components',comp),
                       ('homeassistant.components.sensor',sensor),('homeassistant.config_entries',ce),
                       ('homeassistant.core',core),('homeassistant.helpers',helpers),
                       ('homeassistant.helpers.entity_platform',ep),('homeassistant.helpers.entity',ent),
                       ('homeassistant.helpers.entity_registry',er),('loki_monitor.entity',le)]:
        monkeypatch.setitem(sys.modules,name,value)
    sys.modules.pop('loki_monitor.sensor',None)
    mod=importlib.import_module('loki_monitor.sensor')
    yield mod
    sys.modules.pop('loki_monitor.sensor',None)

@pytest.mark.asyncio
async def test_compact_sensors_and_cleanup(sensor_module):
    id='one';entries=[registry_entry(id,x) for x in [
        'errors_1h','errors_24h','warnings_1h','last_error','monitor_a_errors_1h',
        'monitor_a_warnings_24h','monitor_gone_errors_1h','custom_old']]
    entries+=[registry_entry(id,'monitor_gone_errors_1h',platform='foreign'),
              registry_entry('other','monitor_gone_errors_1h')]
    hass=SimpleNamespace(registry=Registry(entries))
    entry=SimpleNamespace(entry_id=id,runtime_data=SimpleNamespace(coordinator=object()),
       options={'monitors':[{'id':'a','host':'pve','name':'pve','service':''}]})
    added=[]
    await sensor_module.async_setup_entry(hass,entry,added.extend)
    assert len(added)==4  # one fleet, one host, two diagnostics
    assert {'one_fleet_summary','one_monitor_a_summary'} <= {x.unique_id for x in added}
    assert len(hass.registry.removed)==8
    assert any(x.platform=='foreign' for x in hass.registry.entries)
    assert any(x.config_entry_id=='other' for x in hass.registry.entries)

@pytest.mark.asyncio
async def test_cleanup_idempotent(sensor_module):
    id='one';hass=SimpleNamespace(registry=Registry([registry_entry(id,'errors_1h')]))
    entry=SimpleNamespace(entry_id=id,runtime_data=SimpleNamespace(coordinator=object()),options={})
    await sensor_module.async_setup_entry(hass,entry,lambda _:None)
    await sensor_module.async_setup_entry(hass,entry,lambda _:None)
    assert hass.registry.removed==['sensor.one_errors_1h_loki_monitor']
