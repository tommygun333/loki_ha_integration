"""Per-collector sensor creation, status and dynamic discovery."""
from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def health_module(monkeypatch):
    ha = ModuleType('homeassistant'); ha.__path__ = []
    components = ModuleType('homeassistant.components'); components.__path__ = []
    binary = ModuleType('homeassistant.components.binary_sensor')
    binary.BinarySensorEntity = type('BinarySensorEntity', (), {})
    binary.BinarySensorDeviceClass = SimpleNamespace(CONNECTIVITY='connectivity')
    config = ModuleType('homeassistant.config_entries'); config.ConfigEntry = object
    core = ModuleType('homeassistant.core'); core.HomeAssistant = object
    helpers = ModuleType('homeassistant.helpers'); helpers.__path__ = []
    platform = ModuleType('homeassistant.helpers.entity_platform'); platform.AddEntitiesCallback = object
    entity = ModuleType('homeassistant.helpers.entity'); entity.EntityCategory = SimpleNamespace(DIAGNOSTIC='diagnostic')
    registry = ModuleType('homeassistant.helpers.entity_registry')
    registry.async_get = lambda _: object()
    registry.async_entries_for_config_entry = lambda *_: []
    base = ModuleType('loki_monitor.entity')

    class FakeEntity:
        def __init__(self, coordinator, entry_id, key):
            self.coordinator = coordinator
            self.unique_id = f'{entry_id}_{key}'

        @property
        def available(self):
            return self.coordinator.last_update_success

    base.LokiEntity = FakeEntity
    modules = {
        'homeassistant': ha, 'homeassistant.components': components,
        'homeassistant.components.binary_sensor': binary,
        'homeassistant.config_entries': config, 'homeassistant.core': core,
        'homeassistant.helpers': helpers,
        'homeassistant.helpers.entity_platform': platform,
        'homeassistant.helpers.entity': entity,
        'homeassistant.helpers.entity_registry': registry,
        'loki_monitor.entity': base,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules.pop('loki_monitor.binary_sensor', None)
    mod = importlib.import_module('loki_monitor.binary_sensor')
    yield mod
    sys.modules.pop('loki_monitor.binary_sensor', None)


@pytest.mark.asyncio
async def test_health_entities_are_per_collector_and_track_disappearing_targets(health_module):
    class Coordinator:
        last_update_success = True
        data = {'alloy_scrapes': {
            'CoreELEC': {'up': False, 'targets': [{'instance': '192.168.1.17:12345', 'up': False}]},
        }}

        def async_add_listener(self, callback):
            self.listener = callback
            return lambda: None

    coordinator = Coordinator()
    entry = SimpleNamespace(entry_id='demo', runtime_data=SimpleNamespace(coordinator=coordinator))
    entry.async_on_unload = lambda _: None
    added = []
    await health_module.async_setup_entry(None, entry, added.extend)
    assert len(added) == 2  # Existing backend health plus one CoreELEC agent
    agent = next(item for item in added if isinstance(item, health_module.LokiAlloyHealth))
    assert agent._attr_name == 'CoreELEC Alloy scrape healthy'
    assert agent.available and agent.is_on is False
    assert agent.extra_state_attributes['targets'][0]['instance'] == '192.168.1.17:12345'

    coordinator.data['alloy_scrapes']['CoreELEC']['up'] = True
    coordinator.listener()
    assert agent.is_on is True and len(added) == 2

    coordinator.data['alloy_scrapes']['ct107'] = {'up': True, 'targets': []}
    coordinator.listener()
    assert len(added) == 3
    assert len({item.unique_id for item in added}) == 3

    coordinator.data['alloy_scrapes'] = None  # Prometheus failed, not Alloy down
    coordinator.listener()
    assert agent.available is False
    coordinator.data['alloy_scrapes'] = {}  # Target deleted from scrape config
    assert agent.available is False
