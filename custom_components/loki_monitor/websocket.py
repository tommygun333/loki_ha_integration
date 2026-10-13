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
    build_filter_scopes,
    DEFAULT_LABEL_MAP,
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
    probatio.Optional("host", default=""): str,
    probatio.Optional("source", default=""): str,
    probatio.Optional("service", default=""): str,
    probatio.Optional("program", default=""): str,
    probatio.Optional("minutes", default=60): int,
    probatio.Optional("cascading", default=True): bool,
    probatio.Optional("source_label", default="source"): str,
    probatio.Optional("service_label", default="service_name"): str,
    probatio.Optional("program_label", default="syslog_identifier"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_filters(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Fetch bounded label value suggestions for the filter controls."""
    entry = _get_entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "source_not_found", "Select a loaded Loki server")
        return
    try:
        minutes = msg.get("minutes", 60)
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= MAX_WINDOW_MINUTES:
            raise ViewerInputError("Invalid filter time range")
        mappings = {
            "host": entry.options.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL),
            "source": msg.get("source_label", "source"),
            "service": msg.get("service_label", "service_name"),
            "program": msg.get("program_label", "syslog_identifier"),
        }
        scopes = build_filter_scopes(
            host_label=mappings["host"],
            host=msg.get("host", ""), source=msg.get("source", ""),
            service=msg.get("service", ""), program=msg.get("program", ""),
            source_label=mappings["source"], service_label=mappings["service"],
            program_label=mappings["program"], cascading=msg.get("cascading", True),
        )
    except ViewerInputError as err:
        connection.send_error(msg["id"], "invalid_filters", str(err))
        return
    try:
        async with _QUERY_LIMIT:
            results = await asyncio.gather(*(
                entry.runtime_data.api.async_label_values(
                    mappings[key], f"{minutes}m", query=scopes[key]
                ) for key in ("host", "source", "service", "program")
            ), return_exceptions=True)
    except (LokiAuthError, LokiError):
        results = [[], [], [], []]
    values = {
        key: sorted({value for value in result if isinstance(value, str) and 0 < len(value) <= 128})[:250]
        if isinstance(result, list) else []
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
    probatio.Optional("severity_mode", default="canonical"): str,
    probatio.Optional("source_label", default="source"): str,
    probatio.Optional("service_label", default="service_name"): str,
    probatio.Optional("program_label", default="syslog_identifier"): str,
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
            severity_mode=msg.get("severity_mode", "canonical"),
            source_label=msg.get("source_label", "source"),
            service_label=msg.get("service_label", "service_name"),
            program_label=msg.get("program_label", "syslog_identifier"),
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
