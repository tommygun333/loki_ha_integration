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
    SEVERITY_SOURCE_CANONICAL,
)
from .monitoring import CONF_MONITORS, monitor_selector
from .alerts import CONF_ALERT_RULES
from .viewer import CANONICAL_SEVERITY_TEMPLATE, SEVERITY_PATTERNS
import json
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

    def __init__(self, hass: HomeAssistant, api: LokiClient, options: dict[str, Any], alerts=None) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=options.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)
            ),
        )
        self.api = api
        self.alerts = alerts
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
        elif severity_source == SEVERITY_SOURCE_CANONICAL:
            # Exact host/source/service/program "All" scope from Grafana v1.9.
            scope = ("{" + host_label + '=~".+",source=~".*",'
                     'service_name=~".*",syslog_identifier=~".*"}')
            base = scope + " | label_format severity=`" + CANONICAL_SEVERITY_TEMPLATE + "`"
            error_selector = base + " | severity=~" + json.dumps(SEVERITY_PATTERNS["errors"])
            warn_selector = base + " | severity=~" + json.dumps(SEVERITY_PATTERNS["warnings"])
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
        monitors = self.options.get(CONF_MONITORS, [])
        work = {
            "errors_1h": self.api.async_query(count_query(error_selector, "1h")),
            "errors_24h": self.api.async_query(count_query(error_selector, "24h")),
            "warnings_1h": self.api.async_query(count_query(warn_selector, "1h")),
            "warnings_24h": self.api.async_query(count_query(warn_selector, "24h")),
            "last_error": self.api.async_recent_logs(error_selector, minutes=1440, limit=1),
            "labels": self.api.async_labels(),
            "hosts": self.api.async_label_values(host_label),
        }
        for monitor in monitors:
            for severity in ("error", "warn"):
                scoped = monitor_selector(
                    monitor,
                    host_label=host_label,
                    severity_source=severity_source,
                    level_label=label,
                    severity_values=self.options.get(
                        CONF_ERROR_LEVELS if severity == "error" else CONF_WARN_LEVELS,
                        DEFAULT_ERROR_LEVELS if severity == "error" else DEFAULT_WARN_LEVELS,
                    ),
                    severity=severity,
                )
                plural = "errors" if severity == "error" else "warnings"
                for window in ("1h", "24h"):
                    key = f"monitor_{monitor['id']}_{plural}_{window}"
                    work[key] = self.api.async_query(count_query(scoped, window))

        for item in custom:
            work[f"custom_{item['id']}"] = self.api.async_query(item["query"])

        # Bound concurrent Loki requests when many monitors are configured.
        limit = asyncio.Semaphore(6)

        async def limited(coro):
            async with limit:
                return await coro

        names = list(work)
        outcomes = await asyncio.gather(
            *(limited(coro) for coro in work.values()), return_exceptions=True
        )
        values: dict[str, Any] = {}
        failures: set[str] = set()
        successful = 0
        for name, outcome in zip(names, outcomes, strict=True):
            if isinstance(outcome, LokiAuthError):
                raise ConfigEntryAuthFailed("Loki authentication failed") from outcome
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, BaseException):
                failures.add(name)
                # Avoid logging raw LogQL or exceptions that could disclose log data.
                if name not in self._failing_queries:
                    _LOGGER.warning("Loki query %s failed (%s)", name, type(outcome).__name__)
                    self._failing_queries.add(name)
                values[name] = None
            else:
                successful += 1
                values[name] = outcome
                if name in self._failing_queries:
                    _LOGGER.info("Loki query %s recovered", name)
                    self._failing_queries.discard(name)

        # If all requests failed, this is a connection/API outage rather than
        # a handful of broken LogQL expressions.
        if not successful:
            raise UpdateFailed("Every Loki query failed; check the server connection")

        result: dict[str, Any] = {"failed_queries": sorted(failures)}
        for key in ("errors_1h", "errors_24h", "warnings_1h", "warnings_24h"):
            if key not in failures:
                try:
                    result[key] = extract_number(values[key], empty_is_zero=True)
                except (TypeError, KeyError, ValueError):
                    failures.add(key)
            result.setdefault(key, None)

        if "last_error" not in failures:
            try:
                result["last_error"] = extract_latest_timestamp(values["last_error"])
            except (TypeError, KeyError, ValueError):
                failures.add("last_error")
        result.setdefault("last_error", None)

        labels = values.get("labels")
        hosts = values.get("hosts")
        result["available_labels"] = labels if isinstance(labels, list) else []
        result["severity_label_present"] = (label in labels) if isinstance(labels, list) else None
        result["host_label_present"] = (host_label in labels) if isinstance(labels, list) else None
        result["hosts"] = sorted(set(hosts)) if isinstance(hosts, list) else []
        result["active_hosts"] = (
            len(result["hosts"])
            if isinstance(hosts, list) and result["host_label_present"] is not False
            else None
        )
        if result["active_hosts"] is None:
            failures.add("active_hosts")
        if not isinstance(labels, list):
            failures.update(("severity_label_present", "host_label_present"))

        for monitor in monitors:
            for severity in ("errors", "warnings"):
                for window in ("1h", "24h"):
                    key = f"monitor_{monitor['id']}_{severity}_{window}"
                    if key not in failures:
                        try:
                            result[key] = extract_number(values[key], empty_is_zero=True)
                        except (TypeError, KeyError, ValueError):
                            failures.add(key)
                    result.setdefault(key, None)

        for item in custom:
            key = f"custom_{item['id']}"
            if key not in failures:
                try:
                    result[key] = extract_number(values[key])
                except (TypeError, ValueError, KeyError):
                    failures.add(key)
            result.setdefault(key, None)
        result["failed_queries"] = sorted(failures)
        result["failed_query_count"] = len(failures)
        if self.alerts is not None:
            try:
                result.update(await self.alerts.async_check(self.options.get(CONF_ALERT_RULES, [])))
            except asyncio.CancelledError:
                raise
            except Exception:
                # Alert processing must never disable health and count sensors.
                _LOGGER.exception("Loki alert processor failed")
                result["alert_rule_failures"] = [
                    rule["id"] for rule in self.options.get(CONF_ALERT_RULES, [])
                    if rule.get("enabled", True)
                ]
                result["alerts_triggered"] = 0
        result["alert_rule_failures_count"] = len(result.get("alert_rule_failures", []))
        return result
