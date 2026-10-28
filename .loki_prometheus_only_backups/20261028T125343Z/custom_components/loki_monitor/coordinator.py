"""Shared polling for Loki Monitor entities."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    LokiAuthError, LokiClient, LokiError, LokiHTTPError, LokiQueryError, LokiTimeoutError,
)
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
from .prometheus import PrometheusClient
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

    def __init__(self, hass: HomeAssistant, api: LokiClient, options: dict[str, Any], alerts=None, prometheus: PrometheusClient | None = None) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=options.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)
            ),
        )
        self.api = api
        self.prometheus = prometheus
        self.alerts = alerts
        self.options = options
        self._failing_queries: set[str] = set()
        self._canonical_chunked_keys: set[str] = set()
        # Do not run dozens of historical canonical scans simultaneously.
        self._canonical_chunk_limit = asyncio.Semaphore(2)

    @staticmethod
    def _is_heavy_query_failure(err: BaseException) -> bool:
        """Only split an expensive query if Loki timed out or had a server error."""
        return isinstance(err, LokiTimeoutError) or (
            isinstance(err, LokiHTTPError) and err.status in (500, 502, 503, 504)
        )

    async def _query_canonical_piece(
        self, selector: str, *, hours: int, time_ns: int
    ) -> float:
        """Count one nonoverlapping interval, splitting 2h if necessary."""
        query = count_query(selector, f"{hours * 60}m")
        try:
            async with self._canonical_chunk_limit:
                data = await self.api.async_query_at(query, time_ns=time_ns)
            return float(extract_number(data, empty_is_zero=True))
        except (LokiTimeoutError, LokiHTTPError) as err:
            if hours != 2 or not self._is_heavy_query_failure(err):
                raise
            # A failed 2-hour slice can be processed as two 1-hour slices;
            # neither range overlaps the other or falls outside the 24h window.
            one_hour_ns = 3_600_000_000_000
            earlier = await self._query_canonical_piece(
                selector, hours=1, time_ns=time_ns - one_hour_ns
            )
            later = await self._query_canonical_piece(
                selector, hours=1, time_ns=time_ns
            )
            return earlier + later

    async def _query_canonical_24h(self, selector: str, key: str) -> dict[str, Any]:
        """Try Loki's native 24h count, then fall back to bounded 2h buckets.

        Once a native query has failed due to timeouts/server overload, keep
        this metric on buckets until the integration is reloaded, so every poll
        does not first waste 25 seconds retrying the expensive native query.
        """
        if key not in self._canonical_chunked_keys:
            try:
                return await self.api.async_query(count_query(selector, "24h"))
            except (LokiTimeoutError, LokiHTTPError) as err:
                if not self._is_heavy_query_failure(err):
                    raise
                self._canonical_chunked_keys.add(key)
                _LOGGER.warning(
                    "Loki canonical %s 24h query exceeded server/client limits "
                    "(%s); using bounded historical buckets", key, str(err),
                )

        # A fixed evaluation anchor is essential; each 2h count ends exactly
        # where the previous one begins, covering the same rolling 24h interval.
        end_ns = time.time_ns()
        two_hours_ns = 7_200_000_000_000
        values = await asyncio.gather(*(
            self._query_canonical_piece(selector, hours=2, time_ns=end_ns - index * two_hours_ns)
            for index in range(12)
        ))
        # Return the same Loki-style shape expected by the original coordinator.
        return {"resultType": "scalar", "result": [end_ns / 1e9, str(sum(values))]}

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

        def metric_query(selector: str, key: str, window: str):
            if severity_source == SEVERITY_SOURCE_CANONICAL and window == "24h":
                return self._query_canonical_24h(selector, key)
            return self.api.async_query(count_query(selector, window))

        work = {
            "last_error": self.api.async_recent_logs(error_selector, minutes=1440, limit=1),
            "labels": self.api.async_labels(),
            "hosts": self.api.async_label_values(host_label),
        }
        if self.prometheus is None:
            work.update({
                "errors_1h": metric_query(error_selector, "errors_1h", "1h"),
                "errors_24h": metric_query(error_selector, "errors_24h", "24h"),
                "warnings_1h": metric_query(warn_selector, "warnings_1h", "1h"),
                "warnings_24h": metric_query(warn_selector, "warnings_24h", "24h"),
            })
        else:
            # Two inexpensive PromQL requests replace the four fleet-wide
            # and four per host-only monitor expensive LogQL count scans.
            for window in ("1h", "24h"):
                work[f"prometheus_{window}"] = self.prometheus.async_counter_window(window)
        for monitor in monitors:
            if self.prometheus is not None and not monitor.get("service"):
                continue
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
                    work[key] = metric_query(scoped, key, window)

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
                    # Our LokiError messages contain only generic status/timeout
                    # information; never log raw LogQL, URLs or response bodies.
                    detail = str(outcome) if isinstance(outcome, LokiError) else type(outcome).__name__
                    _LOGGER.warning("Loki query %s failed (%s)", name, detail)
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
        if self.prometheus is not None:
            for window in ("1h", "24h"):
                series = values.get(f"prometheus_{window}")
                names = (f"errors_{window}", f"warnings_{window}")
                host_monitors = [m for m in monitors if not m.get("service")]
                monitor_names = [
                    f"monitor_{m['id']}_{level}_{window}"
                    for m in host_monitors for level in ("errors", "warnings")
                ]
                if not isinstance(series, dict) or f"prometheus_{window}" in failures:
                    failures.update((*names, *monitor_names))
                    continue
                result[names[0]] = sum(v for (host, level), v in series.items() if level == "high")
                result[names[1]] = sum(v for (host, level), v in series.items() if level == "warning")
                for monitor in host_monitors:
                    for level, group in (("errors", "high"), ("warnings", "warning")):
                        key = f"monitor_{monitor['id']}_{level}_{window}"
                        result[key] = series.get((monitor["host"], group), 0.0)
        for key in ("errors_1h", "errors_24h", "warnings_1h", "warnings_24h"):
            if key not in failures and key not in result:
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
                    if key not in failures and key not in result:
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
