"""Compact Prometheus-backed summary sensors; no 4-entities-per-host explosion."""
from __future__ import annotations

import logging
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers import entity_registry as er

from .const import CONF_CUSTOM_QUERIES, CONF_ICON, CONF_UNIT, DOMAIN
from .entity import LokiEntity
from .monitoring import CONF_MONITORS

_LOGGER = logging.getLogger(__name__)
OLD_BUILTINS = {'errors_1h', 'errors_24h', 'warnings_1h', 'warnings_24h', 'active_hosts', 'last_error'}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [LokiSummarySensor(coordinator, entry.entry_id, None)]
    entities.extend(LokiSummarySensor(coordinator, entry.entry_id, m) for m in entry.options.get(CONF_MONITORS, []))
    entities.extend([
        LokiMetricSensor(coordinator, entry.entry_id, 'failed_query_count', 'Query failures'),
        LokiMetricSensor(coordinator, entry.entry_id, 'alert_rule_failures_count', 'Alert rule failures'),
    ])
    if entry.options.get('poll_custom_queries', False):
        entities.extend(LokiCustomSensor(coordinator, entry.entry_id, item)
                        for item in entry.options.get(CONF_CUSTOM_QUERIES, []))
    _remove_orphaned_entities(hass, entry, entities)
    async_add_entities(entities)


def _remove_orphaned_entities(hass, entry, desired_entities):
    """Prune only this entry's obsolete Loki Monitor sensors, not unrelated entities."""
    desired = {x.unique_id for x in desired_entities}
    registry = er.async_get(hass)
    deleted = 0
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.domain != 'sensor' or item.platform != DOMAIN or item.config_entry_id != entry.entry_id:
            continue
        uid = item.unique_id
        old = (uid.removeprefix(f'{entry.entry_id}_') in OLD_BUILTINS)
        dynamic = uid.startswith((f'{entry.entry_id}_monitor_', f'{entry.entry_id}_custom_'))
        if (old or dynamic) and uid not in desired:
            registry.async_remove(item.entity_id)
            deleted += 1
    if deleted:
        _LOGGER.info('Removed %s old Loki Monitor sensor registry entries', deleted)


class LokiSummarySensor(LokiEntity, SensorEntity):
    """Primary state is high severity in one hour; 1h/24h groups are attributes."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = 'mdi:alert-circle-outline'

    def __init__(self, coordinator, entry_id: str, monitor: dict | None):
        self._monitor = monitor
        key = f"monitor_{monitor['id']}_summary" if monitor else 'fleet_summary'
        super().__init__(coordinator, entry_id, key)
        self._attr_name = f"{monitor['name']} Log Summary" if monitor else 'Fleet Log Summary'
        self._entry_id = entry_id

    def _summary(self):
        if self.coordinator.data is None:
            return None
        if self._monitor:
            return self.coordinator.data.get('monitor_summaries', {}).get(self._monitor['id'])
        return self.coordinator.data.get('fleet_summary')

    @property
    def available(self) -> bool:
        summary = self._summary()
        return super().available and isinstance(summary, dict) and summary.get('high_1h') is not None

    @property
    def native_value(self) -> int | None:
        summary = self._summary()
        return summary.get('high_1h') if summary else None

    @property
    def extra_state_attributes(self) -> dict:
        data = dict(self._summary() or {})
        monitor = self._monitor
        data.update({
            'monitor_type': 'service' if monitor and monitor.get('service') else ('host' if monitor else 'fleet'),
            'host': monitor['host'] if monitor else None,
            'service_name': (monitor.get('service') or None) if monitor else None,
            'loki_entry_id': self._entry_id,
            'count_source': 'prometheus',
        })
        return data


class LokiMetricSensor(LokiEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry_id, key, name):
        super().__init__(coordinator, entry_id, key)
        self._attr_name = name

    @property
    def native_value(self):
        return self.coordinator.data.get(self.key) if self.coordinator.data is not None else None


class LokiCustomSensor(LokiEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id, definition: dict):
        super().__init__(coordinator, entry_id, f"custom_{definition['id']}")
        self._attr_name = definition['name']
        self._attr_icon = definition.get(CONF_ICON, 'mdi:chart-line')
        self._attr_native_unit_of_measurement = definition.get(CONF_UNIT)

    @property
    def native_value(self):
        return self.coordinator.data.get(self.key) if self.coordinator.data is not None else None

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None and self.coordinator.data.get(self.key) is not None
