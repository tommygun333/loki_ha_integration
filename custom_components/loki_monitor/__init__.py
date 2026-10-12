"""Loki Monitor: read-only Home Assistant integration for Grafana Loki."""

from __future__ import annotations

from dataclasses import dataclass

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LokiAuthError, LokiClient, LokiError
from .const import CONF_QUERY, CONF_URL, CONF_VERIFY_SSL, DOMAIN
from .coordinator import LokiCoordinator
from .logic import flatten_streams

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
SERVICE_QUERY_LOGS = "query_logs"


@dataclass
class LokiRuntimeData:
    """Entry-local state, including its authenticated Loki API client."""

    api: LokiClient
    coordinator: LokiCoordinator


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register read-only query action once per integration domain."""

    async def query_logs(call: ServiceCall) -> dict:
        entry_id = call.data.get("entry_id")
        entries = [
            entry for entry in hass.config_entries.async_entries(DOMAIN)
            if getattr(entry, "runtime_data", None) is not None
        ]
        if entry_id:
            entries = [entry for entry in entries if entry.entry_id == entry_id]
        if len(entries) != 1:
            raise HomeAssistantError("Specify a loaded Loki config entry ID")

        # A log may contain passwords, tokens, personal information, etc.
        # Never expose the query action to ordinary HA users by default.
        if call.context.user_id is not None:
            user = await hass.auth.async_get_user(call.context.user_id)
            if not user or not user.is_admin:
                raise HomeAssistantError("Only HA administrators may query raw Loki logs")

        limit = call.data["limit"]
        try:
            result = await entries[0].runtime_data.api.async_recent_logs(
                call.data[CONF_QUERY],
                minutes=call.data["minutes"],
                limit=limit,
            )
            return {"logs": flatten_streams(result, limit), "entry_id": entries[0].entry_id}
        except (LokiError, ValueError, TypeError) as err:
            raise HomeAssistantError("Loki log query failed or did not return log streams") from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_QUERY_LOGS,
        query_logs,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Required(CONF_QUERY): vol.All(str, vol.Length(min=1, max=4096)),
                vol.Optional("minutes", default=60): vol.All(vol.Coerce(int), vol.Range(min=1, max=10080)),
                vol.Optional("limit", default=100): vol.All(vol.Coerce(int), vol.Range(min=1, max=200)),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Connect, validate and set up one Loki server."""
    api = LokiClient(
        async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)),
        entry.data,
    )
    coordinator = LokiCoordinator(hass, api, dict(entry.options))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = LokiRuntimeData(api=api, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload sensors and cancel coordinator updates."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
