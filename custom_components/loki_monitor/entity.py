"""Common base class for Loki Monitor entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LokiCoordinator


class LokiEntity(CoordinatorEntity[LokiCoordinator]):
    """Entity representing a metric from a single Loki server."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LokiCoordinator, entry_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Grafana Loki",
            manufacturer="Grafana Labs",
            model="Loki",
        )
