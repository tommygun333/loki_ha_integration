"""Regression tests for entity-registry cleanup after removing user queries."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


class FakeRegistry:
    def __init__(self, entries):
        self.entries = list(entries)
        self.removed: list[str] = []

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.entries = [e for e in self.entries if e.entity_id != entity_id]


def registry_entry(entry_id, key, *, domain="sensor", platform="loki_monitor"):
    return SimpleNamespace(
        entity_id=(
            f"{domain}.{key}"
            if entry_id == "loki-one" and platform == "loki_monitor" and domain == "sensor"
            else f"{domain}.{key}_{platform}_{entry_id}"
        ),
        config_entry_id=entry_id,
        unique_id=f"{entry_id}_{key}",
        domain=domain,
        platform=platform,
    )


@pytest.fixture
def sensor_module(monkeypatch):
    ha = ModuleType("homeassistant")
    ha.__path__ = []
    components = ModuleType("homeassistant.components")
    components.__path__ = []
    sensor = ModuleType("homeassistant.components.sensor")
    sensor.SensorEntity = type("SensorEntity", (), {})
    sensor.SensorDeviceClass = SimpleNamespace(TIMESTAMP="timestamp")
    sensor.SensorStateClass = SimpleNamespace(MEASUREMENT="measurement")
    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    entity_platform = ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    entity = ModuleType("homeassistant.helpers.entity")
    entity.EntityCategory = SimpleNamespace(DIAGNOSTIC="diagnostic")
    entity_registry = ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: hass.registry
    entity_registry.async_entries_for_config_entry = (
        lambda registry, entry_id: [
            item for item in registry.entries if item.config_entry_id == entry_id
        ]
    )
    loki_entity = ModuleType("loki_monitor.entity")

    class FakeLokiEntity:
        def __init__(self, coordinator, entry_id, key):
            self.coordinator = coordinator
            self.key = key
            self.unique_id = f"{entry_id}_{key}"

    loki_entity.LokiEntity = FakeLokiEntity

    for key, value in {
        "homeassistant": ha,
        "homeassistant.components": components,
        "homeassistant.components.sensor": sensor,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity_platform": entity_platform,
        "homeassistant.helpers.entity": entity,
        "homeassistant.helpers.entity_registry": entity_registry,
        "loki_monitor.entity": loki_entity,
    }.items():
        monkeypatch.setitem(sys.modules, key, value)
    sys.modules.pop("loki_monitor.sensor", None)
    module = importlib.import_module("loki_monitor.sensor")
    yield module
    sys.modules.pop("loki_monitor.sensor", None)


@pytest.mark.asyncio
async def test_deleted_custom_query_and_monitor_are_pruned(sensor_module):
    """Old dynamic registry records disappear, while current IDs are retained."""
    entry_id = "loki-one"
    keep_monitor = "monitor_abc"
    stale_monitor = "monitor_deleted"
    entries = [
        registry_entry(entry_id, "errors_1h"),
        registry_entry(entry_id, "custom_keep"),
        registry_entry(entry_id, "custom_deleted"),
        registry_entry(entry_id, "monitor_abc_errors_1h"),
        registry_entry(entry_id, "monitor_abc_warnings_24h"),
        *[
            registry_entry(entry_id, f"{stale_monitor}_{severity}_{window}")
            for severity in ("errors", "warnings")
            for window in ("1h", "24h")
        ],
        # This integration must never change other entries, domains or platforms.
        registry_entry("other-loki", "custom_deleted"),
        registry_entry(entry_id, "custom_deleted", platform="other_integration"),
        registry_entry(entry_id, "monitor_deleted_errors_1h", domain="binary_sensor"),
    ]
    hass = SimpleNamespace(registry=FakeRegistry(entries))
    entry = SimpleNamespace(
        entry_id=entry_id,
        runtime_data=SimpleNamespace(coordinator=object()),
        options={
            "custom_queries": [{"id": "keep", "name": "Still here", "query": "vector(42)"}],
            "monitors": [{"id": "abc", "name": "Kodi", "host": "CoreELEC", "service": "kodi"}],
        },
    )
    added = []
    await sensor_module.async_setup_entry(hass, entry, added.extend)
    expected = {"sensor.custom_deleted"} | {
        f"sensor.{stale_monitor}_{severity}_{window}"
        for severity in ("errors", "warnings")
        for window in ("1h", "24h")
    }
    assert set(hass.registry.removed) == expected
    assert len(added) == 8 + 1 + 4
    assert {x.unique_id for x in added if x.key.startswith("monitor_")} == {
        f"{entry_id}_{keep_monitor}_{severity}_{window}"
        for severity in ("errors", "warnings")
        for window in ("1h", "24h")
    }
    assert all(x.entity_id not in expected for x in hass.registry.entries)
    assert any(x.config_entry_id == "other-loki" for x in hass.registry.entries)
    assert any(x.platform == "other_integration" for x in hass.registry.entries)
    assert any(x.domain == "binary_sensor" for x in hass.registry.entries)


@pytest.mark.asyncio
async def test_previous_orphans_cleaned_even_when_no_dynamic_options(sensor_module):
    """Upgrading also clears registry records from earlier manual deletions."""
    entry_id = "loki-one"
    hass = SimpleNamespace(registry=FakeRegistry([
        registry_entry(entry_id, "custom_orphan"),
        registry_entry(entry_id, "monitor_orphan_errors_24h"),
        registry_entry(entry_id, "last_error"),
        registry_entry(entry_id, "failed_query_count"),
    ]))
    entry = SimpleNamespace(
        entry_id=entry_id,
        runtime_data=SimpleNamespace(coordinator=object()),
        options={},
    )
    added = []
    await sensor_module.async_setup_entry(hass, entry, added.extend)
    assert set(hass.registry.removed) == {
        "sensor.custom_orphan",
        "sensor.monitor_orphan_errors_24h",
    }
    assert len(added) == 8


@pytest.mark.asyncio
async def test_registry_cleanup_is_idempotent(sensor_module):
    entry_id = "loki-one"
    hass = SimpleNamespace(registry=FakeRegistry([
        registry_entry(entry_id, "custom_old"),
    ]))
    entry = SimpleNamespace(
        entry_id=entry_id,
        runtime_data=SimpleNamespace(coordinator=object()),
        options={},
    )
    await sensor_module.async_setup_entry(hass, entry, lambda _: None)
    await sensor_module.async_setup_entry(hass, entry, lambda _: None)
    assert hass.registry.removed == ["sensor.custom_old"]
