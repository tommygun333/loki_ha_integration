"""Counter-backend and per-Alloy-collector scrape-health indicators."""
from hashlib import sha256

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from .entity import LokiEntity
from .prometheus import COUNTER_JOB


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities([LokiConnectivity(coordinator, entry.entry_id)])
    # Scrape targets are registered by Prometheus, not by Loki's ``host`` label.
    # Discover once after the initial coordinator refresh, and on later polls
    # when new collectors are deployed; never create one entity per log service.
    seen: set[str] = set()

    def add_collectors() -> None:
        data = (coordinator.data or {}).get('alloy_scrapes')
        if not isinstance(data, dict):
            return
        new = []
        for collector in sorted(data):
            if collector not in seen:
                seen.add(collector)
                new.append(LokiAlloyHealth(coordinator, entry.entry_id, collector))
        if new:
            async_add_entities(new)

    add_collectors()
    entry.async_on_unload(coordinator.async_add_listener(add_collectors))
    # Remove old label-detection diagnostics from this config entry alone.
    from homeassistant.helpers import entity_registry as er
    registry = er.async_get(hass)
    obsolete = {f"{entry.entry_id}_severity_label_present", f"{entry.entry_id}_host_label_present"}
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.domain == 'binary_sensor' and item.platform == 'loki_monitor' and item.unique_id in obsolete:
            registry.async_remove(item.entity_id)


class LokiConnectivity(LokiEntity, BinarySensorEntity):
    _attr_name = 'Counter backend available'
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = 'mdi:connection'

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, 'api_available')

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.coordinator.last_update_success and bool(
            (self.coordinator.data or {}).get('prometheus_available', False))


class LokiAlloyHealth(LokiEntity, BinarySensorEntity):
    """One sensor per distinct Prometheus collector_node, not log-source host."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry_id: str, collector: str):
        # Collector names can contain dots and other entity-id characters.
        # Hash the *exact* label so IDs stay stable and never collide after
        # Home Assistant normalizes entity names.
        key = f"alloy_scrape_{sha256(collector.encode('utf-8')).hexdigest()[:20]}"
        super().__init__(coordinator, entry_id, key)
        self._collector = collector
        self._attr_name = f'{collector} Alloy scrape healthy'

    def _status(self):
        scrapes = (self.coordinator.data or {}).get('alloy_scrapes')
        return scrapes.get(self._collector) if isinstance(scrapes, dict) else None

    @property
    def available(self) -> bool:
        # An absent target, unavailable Prometheus, or failed query is UNKNOWN,
        # never incorrectly interpreted as a failed Alloy scrape.
        return super().available and self._status() is not None

    @property
    def is_on(self) -> bool | None:
        status = self._status()
        return status['up'] if status is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        status = self._status() or {}
        return {
            'collector_node': self._collector,
            'scrape_job': COUNTER_JOB,
            'targets': status.get('targets', []),
            'health_source': 'prometheus_up',
        }
