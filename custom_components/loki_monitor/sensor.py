"""Home Assistant sensors backed by LogQL queries."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_CUSTOM_QUERIES, CONF_ICON, CONF_UNIT
from .entity import LokiEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up built-in and user-defined Loki metrics."""
    coordinator = entry.runtime_data.coordinator
    standard = {
        "errors_1h": ("Errors (1 hour)", "mdi:alert-circle"),
        "errors_24h": ("Errors (24 hours)", "mdi:alert-circle-outline"),
        "warnings_1h": ("Warnings (1 hour)", "mdi:alert"),
        "warnings_24h": ("Warnings (24 hours)", "mdi:alert-outline"),
        "active_hosts": ("Observed hosts (approximately 1 hour)", "mdi:server-network"),
        "last_error": ("Last error", "mdi:clock-alert-outline"),
    }
    entities = [
        LokiMetricSensor(coordinator, entry.entry_id, key, name, icon)
        for key, (name, icon) in standard.items()
    ]
    entities.extend(
        LokiCustomSensor(coordinator, entry.entry_id, item)
        for item in entry.options.get(CONF_CUSTOM_QUERIES, [])
    )
    async_add_entities(entities)


class LokiMetricSensor(LokiEntity, SensorEntity):
    """Built-in count or timestamp sensor."""

    def __init__(self, coordinator, entry_id, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator, entry_id, key)
        self._attr_name = name
        self._attr_icon = icon
        if key == "last_error":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        """Missing host label must not be represented as zero hosts."""
        return (
            super().available
            and self.coordinator.data is not None
            and (self.key != "active_hosts" or self.coordinator.data.get(self.key) is not None)
        )

    @property
    def native_value(self) -> int | float | datetime | None:
        value = self.coordinator.data.get(self.key)
        if self.key in ("errors_1h", "errors_24h", "warnings_1h", "warnings_24h"):
            return int(value) if value is not None else None
        return value

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.key == "active_hosts":
            return {"hosts": self.coordinator.data.get("hosts", [])}
        return None


class LokiCustomSensor(LokiEntity, SensorEntity):
    """User-created scalar LogQL query sensor."""

    def __init__(self, coordinator, entry_id, definition: dict) -> None:
        key = f"custom_{definition['id']}"
        super().__init__(coordinator, entry_id, key)
        self._attr_name = definition["name"]
        self._attr_icon = definition.get(CONF_ICON, "mdi:chart-line")
        self._attr_native_unit_of_measurement = definition.get(CONF_UNIT)
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.get(self.key) is not None
        )

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get(self.key)
