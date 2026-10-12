"""Connectivity and label-mapping diagnostic binary sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import LokiEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up diagnostic sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            LokiConnectivity(coordinator, entry.entry_id),
            LokiDiagnostic(coordinator, entry.entry_id, "severity_label_present", "Indexed severity label present (optional)"),
            LokiDiagnostic(coordinator, entry.entry_id, "host_label_present", "Host label detected"),
        ]
    )


class LokiConnectivity(LokiEntity, BinarySensorEntity):
    """Stay available and switch off when coordinator reports a failed poll."""

    _attr_name = "API available"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:connection"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "api_available")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class LokiDiagnostic(LokiEntity, BinarySensorEntity):
    """Show whether configured host/severity label names exist in Loki."""

    _attr_icon = "mdi:tag-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry_id: str, key: str, name: str) -> None:
        super().__init__(coordinator, entry_id, key)
        self._attr_name = name

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.key not in self.coordinator.data.get("failed_queries", [])
        )

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get(self.key)
