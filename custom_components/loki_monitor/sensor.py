"""Home Assistant sensors backed by LogQL queries."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from .const import CONF_CUSTOM_QUERIES, CONF_ICON, CONF_UNIT
from .entity import LokiEntity
from .monitoring import CONF_MONITORS


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
        "failed_query_count": ("Query failures", "mdi:alert-box-outline"),
    }
    entities = [
        LokiMetricSensor(coordinator, entry.entry_id, key, name, icon)
        for key, (name, icon) in standard.items()
    ]
    entities.extend(
        LokiCustomSensor(coordinator, entry.entry_id, item)
        for item in entry.options.get(CONF_CUSTOM_QUERIES, [])
    )
    for definition in entry.options.get(CONF_MONITORS, []):
        for severity in ("errors", "warnings"):
            for window, label in (("1h", "1 hour"), ("24h", "24 hours")):
                entities.append(
                    LokiMonitorSensor(coordinator, entry.entry_id, definition, severity, window, label)
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
        if key == "failed_query_count":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Missing host label must not be represented as zero hosts."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.key not in self.coordinator.data.get("failed_queries", [])
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
        if self.key == "failed_query_count":
            return {"failed_queries": self.coordinator.data.get("failed_queries", [])}
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


class LokiMonitorSensor(LokiEntity, SensorEntity):
    """One host/service monitor count; IDs survive options-flow edits."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id, monitor: dict, severity: str, window: str, label: str):
        key = f"monitor_{monitor['id']}_{severity}_{window}"
        super().__init__(coordinator, entry_id, key)
        self._attr_name = f"{monitor['name']} {severity.title()} ({label})"
        self._attr_icon = "mdi:alert-circle-outline" if severity == "errors" else "mdi:alert-outline"
        self._monitor = monitor

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.get(self.key) is not None
            and self.key not in self.coordinator.data.get("failed_queries", [])
        )

    @property
    def native_value(self) -> int | None:
        value = self.coordinator.data.get(self.key)
        return int(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        return {"host": self._monitor["host"], "service_name": self._monitor.get("service") or None}
