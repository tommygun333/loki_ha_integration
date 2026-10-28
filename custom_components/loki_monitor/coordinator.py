"""Shared polling: Prometheus for all log-event counters, Loki for log detail."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LokiAuthError, LokiClient, LokiError
from .alerts import CONF_ALERT_RULES
from .const import (
    CONF_CUSTOM_QUERIES, CONF_ERROR_LEVELS, CONF_HOST_LABEL, CONF_LEVEL_LABEL,
    CONF_POLL_SECONDS, CONF_SEVERITY_SOURCE, DEFAULT_ERROR_LEVELS,
    DEFAULT_HOST_LABEL, DEFAULT_LEVEL_LABEL, DEFAULT_POLL_SECONDS,
    DEFAULT_SEVERITY_SOURCE, DOMAIN, SEVERITY_SOURCE_CANONICAL,
    SEVERITY_SOURCE_DETECTED, SEVERITY_SOURCE_INDEXED,
)
from .logic import (
    detected_level_selector, extract_latest_timestamp, extract_number, level_selector,
)
from .monitoring import CONF_MONITORS
from .prometheus import PrometheusClient, PrometheusError
from .viewer import CANONICAL_SEVERITY_TEMPLATE, SEVERITY_PATTERNS

_LOGGER = logging.getLogger(__name__)


class LokiCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Collect two Prometheus windows and Loki-only diagnostic data."""

    def __init__(
        self, hass: HomeAssistant, api: LokiClient, options: dict[str, Any],
        alerts=None, prometheus: PrometheusClient | None = None,
    ) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=options.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)),
        )
        if prometheus is None:
            raise ValueError("A Prometheus client is required for log counters")
        self.api = api
        self.prometheus = prometheus
        self.alerts = alerts
        self.options = options
        self._failing_queries: set[str] = set()

    def _error_selector(self) -> str:
        """Only for retrieving latest raw error; NEVER for counting events."""
        host_label = self.options.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL)
        mode = self.options.get(CONF_SEVERITY_SOURCE, DEFAULT_SEVERITY_SOURCE)
        if mode == SEVERITY_SOURCE_DETECTED:
            return detected_level_selector(host_label, "error")
        if mode == SEVERITY_SOURCE_CANONICAL:
            scope = ("{" + host_label + '=~".+",source=~".*",'
                     'service_name=~".*",syslog_identifier=~".*"}')
            return (scope + " | label_format severity=`" + CANONICAL_SEVERITY_TEMPLATE + "`"
                    + " | severity=~" + json.dumps(SEVERITY_PATTERNS["errors"]))
        if mode == SEVERITY_SOURCE_INDEXED:
            return level_selector(
                self.options.get(CONF_LEVEL_LABEL, DEFAULT_LEVEL_LABEL),
                self.options.get(CONF_ERROR_LEVELS, DEFAULT_ERROR_LEVELS),
            )
        raise UpdateFailed("Unknown Loki severity classification mode")

    async def _async_update_data(self) -> dict[str, Any]:
        custom = self.options.get(CONF_CUSTOM_QUERIES, [])
        monitors = self.options.get(CONF_MONITORS, [])
        error_selector = self._error_selector()

        # No LogQL count_over_time/rate calls are used for built-in, host or
        # service monitor count sensors. Prometheus failures never fall back.
        work = {
            "prometheus_1h": self.prometheus.async_counter_window("1h"),
            "prometheus_24h": self.prometheus.async_counter_window("24h"),
            "last_error": self.api.async_recent_logs(error_selector, minutes=1440, limit=1),
            "labels": self.api.async_labels(),
        }
        for item in custom:
            work[f"custom_{item['id']}"] = self.api.async_query(item["query"])

        async def bounded(coro):
            async with semaphore:
                return await coro

        semaphore = asyncio.Semaphore(6)
        names = list(work)
        outcomes = await asyncio.gather(
            *(bounded(coro) for coro in work.values()), return_exceptions=True,
        )
        values: dict[str, Any] = {}
        failures: set[str] = set()
        success = 0
        for name, outcome in zip(names, outcomes, strict=True):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, LokiAuthError):
                raise ConfigEntryAuthFailed("Loki authorization failed") from outcome
            if isinstance(outcome, BaseException):
                failures.add(name)
                if name not in self._failing_queries:
                    detail = str(outcome) if isinstance(outcome, (LokiError, PrometheusError)) else type(outcome).__name__
                    _LOGGER.warning("Log data query %s failed (%s)", name, detail)
                    self._failing_queries.add(name)
                values[name] = None
            else:
                values[name] = outcome
                success += 1
                if name in self._failing_queries:
                    _LOGGER.info("Log data query %s recovered", name)
                    self._failing_queries.discard(name)

        if not success:
            raise UpdateFailed("Loki and Prometheus requests all failed")

        result: dict[str, Any] = {"failed_queries": []}
        # Use exact metric label combinations, never the Loki label mappings.
        for window in ("1h", "24h"):
            series = values.get(f"prometheus_{window}")
            builtin = (f"errors_{window}", f"warnings_{window}")
            per_monitor = [
                f"monitor_{monitor['id']}_{severity}_{window}"
                for monitor in monitors for severity in ("errors", "warnings")
            ]
            if f"prometheus_{window}" in failures or not isinstance(series, dict):
                failures.update((*builtin, *per_monitor))
                for name in (*builtin, *per_monitor):
                    result[name] = None
                continue

            result[builtin[0]] = sum(v for (_host, _service, level), v in series.items() if level == "high")
            result[builtin[1]] = sum(v for (_host, _service, level), v in series.items() if level == "warning")
            for monitor in monitors:
                host, service = monitor["host"], monitor.get("service", "")
                for name, severity in (("errors", "high"), ("warnings", "warning")):
                    key = f"monitor_{monitor['id']}_{name}_{window}"
                    result[key] = sum(
                        value for (sample_host, sample_service, group), value in series.items()
                        if sample_host == host and group == severity
                        and (not service or service == sample_service)
                    )

            if window == "1h":
                active_hosts = sorted({
                    host for (host, _service, _group), value in series.items() if value > 0
                })
                result["hosts"] = active_hosts
                result["active_hosts"] = len(active_hosts)

        # A missing 1h counter response must not produce a misleading zero.
        if "active_hosts" not in result:
            result["active_hosts"] = None
            result["hosts"] = []
            failures.add("active_hosts")

        if "last_error" not in failures:
            try:
                result["last_error"] = extract_latest_timestamp(values["last_error"])
            except (KeyError, TypeError, ValueError):
                failures.add("last_error")
        result.setdefault("last_error", None)

        labels = values.get("labels")
        level_label = self.options.get(CONF_LEVEL_LABEL, DEFAULT_LEVEL_LABEL)
        host_label = self.options.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL)
        result["available_labels"] = labels if isinstance(labels, list) else []
        result["severity_label_present"] = level_label in labels if isinstance(labels, list) else None
        result["host_label_present"] = host_label in labels if isinstance(labels, list) else None

        for item in custom:
            key = f"custom_{item['id']}"
            if key not in failures:
                try:
                    result[key] = extract_number(values[key])
                except (TypeError, KeyError, ValueError):
                    failures.add(key)
            result.setdefault(key, None)

        if self.alerts is not None:
            try:
                result.update(await self.alerts.async_check(self.options.get(CONF_ALERT_RULES, [])))
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Loki alert processor failed")
                result["alert_rule_failures"] = [
                    rule["id"] for rule in self.options.get(CONF_ALERT_RULES, [])
                    if rule.get("enabled", True)
                ]
                result["alerts_triggered"] = 0
        result["alert_rule_failures_count"] = len(result.get("alert_rule_failures", []))
        result["failed_queries"] = sorted(failures)
        result["failed_query_count"] = len(failures)
        return result
