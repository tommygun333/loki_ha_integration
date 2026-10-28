"""Minimal integration availability indicator. No Loki label polling."""
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .entity import LokiEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([LokiConnectivity(entry.runtime_data.coordinator, entry.entry_id)])
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
