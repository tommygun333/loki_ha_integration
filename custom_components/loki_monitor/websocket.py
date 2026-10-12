"""Authenticated WebSocket API for Loki log viewer (read-only)."""

from __future__ import annotations

import asyncio
import logging

import probatio

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .api import LokiAuthError, LokiError, LokiQueryError
from .const import CONF_HOST_LABEL, DEFAULT_HOST_LABEL, DOMAIN
from .viewer import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_WINDOW_MINUTES,
    ViewerInputError,
    build_viewer_query,
    get_time_bounds,
    page_streams,
)

_LOGGER = logging.getLogger(__name__)
_QUERY_LIMIT = asyncio.Semaphore(3)


def _loaded_entries(hass: HomeAssistant):
    return [
        entry for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


def _get_entry(hass: HomeAssistant, entry_id: str | None):
    entries = _loaded_entries(hass)
    if entry_id:
        return next((entry for entry in entries if entry.entry_id == entry_id), None)
    return entries[0] if len(entries) == 1 else None


@websocket_api.websocket_command({probatio.Required("type"): "loki_monitor/sources"})
@websocket_api.require_admin
@callback
def websocket_sources(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """List only loaded Loki entries; never expose connection credentials."""
    entries = _loaded_entries(hass)
    connection.send_result(msg["id"], {
        "sources": [{"entry_id": entry.entry_id, "title": entry.title} for entry in entries]
    })


@websocket_api.websocket_command({
    probatio.Required("type"): "loki_monitor/filters",
    probatio.Optional("entry_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_filters(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Fetch bounded label value suggestions for the filter controls."""
    entry = _get_entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "source_not_found", "Select a loaded Loki server")
        return
    labels = [entry.options.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL), "source", "service_name", "syslog_identifier"]
    try:
        from .logic import validate_label_name
        labels[0] = validate_label_name(labels[0])
    except ValueError:
        connection.send_error(msg["id"], "invalid_host_label", "Invalid configured host label")
        return
    results = await asyncio.gather(
        *(entry.runtime_data.api.async_label_values(label, "24h") for label in labels),
        return_exceptions=True,
    )
    values = {
        key: sorted({value for value in result if isinstance(value, str) and len(value) <= 128})[:150] if isinstance(result, list) else []
        for key, result in zip(("host", "source", "service", "program"), results, strict=True)
    }
    connection.send_result(msg["id"], {"filters": values})


@websocket_api.websocket_command({
    probatio.Required("type"): "loki_monitor/query_logs",
    probatio.Optional("entry_id"): str,
    probatio.Optional("host", default=""): str,
    probatio.Optional("source", default=""): str,
    probatio.Optional("service", default=""): str,
    probatio.Optional("program", default=""): str,
    probatio.Optional("severity", default="all"): str,
    probatio.Optional("text", default=""): str,
    probatio.Optional("regex", default=""): str,
    probatio.Optional("minutes", default=60): int,
    probatio.Optional("limit", default=DEFAULT_PAGE_SIZE): int,
    probatio.Optional("end_ns"): str,
    probatio.Optional("cursor_ns"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_query_logs(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Retrieve one bounded page with Loki credentials kept on the HA backend."""
    entry = _get_entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "source_not_found", "Select a loaded Loki server")
        return
    try:
        if not 1 <= msg["limit"] <= MAX_PAGE_SIZE:
            raise ViewerInputError("Invalid page size")
        query = build_viewer_query(
            host_label=entry.options.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL),
            host=msg["host"], source=msg["source"], service=msg["service"],
            program=msg["program"], severity=msg["severity"],
            text=msg["text"], regex=msg["regex"],
        )
        start, end, effective_end = get_time_bounds(
            minutes=msg["minutes"], end_ns=msg.get("end_ns"), cursor_ns=msg.get("cursor_ns"),
        )
    except ViewerInputError as err:
        connection.send_error(msg["id"], "invalid_filters", str(err))
        return
    try:
        # Protect Loki if multiple card instances refresh at the same time.
        async with _QUERY_LIMIT:
            data = await entry.runtime_data.api.async_query_streams_ns(
                query, start_ns=start, end_ns=effective_end, limit=msg["limit"] + 1
            )
        rows, cursor, has_more = page_streams(data, msg["limit"])
    except LokiAuthError:
        connection.send_error(msg["id"], "loki_auth", "Loki rejected authentication")
        return
    except (LokiQueryError, LokiError, ValueError, TypeError):
        _LOGGER.warning("Loki viewer query failed for entry %s", entry.entry_id)
        connection.send_error(msg["id"], "loki_query_failed", "Loki could not complete the log query")
        return
    connection.send_result(msg["id"], {
        "logs": rows,
        "has_more": has_more,
        "cursor_ns": cursor,
        "end_ns": str(end),
        "start_ns": str(start),
    })


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register exactly once at integration setup (not once per config entry)."""
    websocket_api.async_register_command(hass, websocket_sources)
    websocket_api.async_register_command(hass, websocket_filters)
    websocket_api.async_register_command(hass, websocket_query_logs)
