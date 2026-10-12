"""Shared polling for Loki Monitor entities."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LokiAuthError, LokiClient, LokiError, LokiQueryError
from .const import (
    CONF_CUSTOM_QUERIES,
    CONF_ERROR_LEVELS,
    CONF_HOST_LABEL,
    CONF_LEVEL_LABEL,
    CONF_POLL_SECONDS,
    CONF_SEVERITY_SOURCE,
    CONF_WARN_LEVELS,
    DEFAULT_ERROR_LEVELS,
    DEFAULT_HOST_LABEL,
    DEFAULT_LEVEL_LABEL,
    DEFAULT_POLL_SECONDS,
    DEFAULT_SEVERITY_SOURCE,
    DEFAULT_WARN_LEVELS,
    DOMAIN,
    SEVERITY_SOURCE_DETECTED,
    SEVERITY_SOURCE_INDEXED,
)
from .logic import (
    count_query,
    detected_level_selector,
    extract_latest_timestamp,
    extract_number,
    level_selector,
)

_LOGGER = logging.getLogger(__name__)


class LokiCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Query summary counts, newest error, labels and user metrics."""

    def __init__(self, hass: HomeAssistant, api: LokiClient, options: dict[str, Any]) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=options.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)
            ),
        )
        self.api = api
        self.options = options
        self._failing_queries: set[str] = set()

    async def _async_update_data(self) -> dict[str, Any]:
        label = self.options.get(CONF_LEVEL_LABEL, DEFAULT_LEVEL_LABEL)
        host_label = self.options.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL)
        severity_source = self.options.get(CONF_SEVERITY_SOURCE, DEFAULT_SEVERITY_SOURCE)
        if severity_source == SEVERITY_SOURCE_DETECTED:
            # Same severity classification, source scope and ranges as the
            # Grafana dashboard's built-in Errors/Warnings stat panels.
            error_selector = detected_level_selector(host_label, "error")
            warn_selector = detected_level_selector(host_label, "warn")
        elif severity_source == SEVERITY_SOURCE_INDEXED:
            error_selector = level_selector(
                label, self.options.get(CONF_ERROR_LEVELS, DEFAULT_ERROR_LEVELS)
            )
            warn_selector = level_selector(
                label, self.options.get(CONF_WARN_LEVELS, DEFAULT_WARN_LEVELS)
            )
        else:
            raise UpdateFailed("Unknown Loki severity source setting")

        custom = self.options.get(CONF_CUSTOM_QUERIES, [])
        work = {
            "errors_1h": self.api.async_query(count_query(error_selector, "1h")),
            "errors_24h": self.api.async_query(count_query(error_selector, "24h")),
            "warnings_1h": self.api.async_query(count_query(warn_selector, "1h")),
            "warnings_24h": self.api.async_query(count_query(warn_selector, "24h")),
            "last_error": self.api.async_recent_logs(error_selector, minutes=1440, limit=1),
            "labels": self.api.async_labels(),
            "hosts": self.api.async_label_values(host_label),
        }
        # Custom metrics must not take down the default sensors if their LogQL is invalid.
        for item in custom:
            work[f"custom_{item['id']}"] = self.api.async_query(item["query"])

        names = list(work)
        outcomes = await asyncio.gather(*work.values(), return_exceptions=True)
        values: dict[str, Any] = {}
        for name, outcome in zip(names, outcomes, strict=True):
            if isinstance(outcome, LokiAuthError):
                raise ConfigEntryAuthFailed("Loki authentication failed") from outcome
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, Exception):
                if name.startswith("custom_"):
                    if name not in self._failing_queries:
                        _LOGGER.warning("Custom Loki sensor %s failed: %s", name, type(outcome).__name__)
                        self._failing_queries.add(name)
                    values[name] = None
                    continue
                if isinstance(outcome, LokiError):
                    raise UpdateFailed(f"Loki query {name} failed: {outcome}") from outcome
                raise UpdateFailed(f"Loki query {name} failed") from outcome
            values[name] = outcome

        try:
            result: dict[str, Any] = {
                key: extract_number(values[key], empty_is_zero=True)
                for key in ("errors_1h", "errors_24h", "warnings_1h", "warnings_24h")
            }
            result["last_error"] = extract_latest_timestamp(values["last_error"])
            result["available_labels"] = values["labels"]
            result["severity_label_present"] = label in values["labels"]
            result["host_label_present"] = host_label in values["labels"]
            result["hosts"] = sorted(set(values["hosts"]))
            result["active_hosts"] = len(result["hosts"]) if result["host_label_present"] else None
        except (TypeError, KeyError, ValueError) as err:
            raise UpdateFailed("Invalid Loki response content") from err

        for item in custom:
            name = f"custom_{item['id']}"
            try:
                result[name] = (
                    extract_number(values[name]) if values[name] is not None else None
                )
                if values[name] is not None and name in self._failing_queries:
                    _LOGGER.info("Custom Loki sensor %s recovered", item["name"])
                    self._failing_queries.discard(name)
            except (TypeError, ValueError, KeyError):
                result[name] = None
                if name not in self._failing_queries:
                    _LOGGER.warning("Custom Loki sensor %s did not return one numeric value", item["name"])
                    self._failing_queries.add(name)
        return result
