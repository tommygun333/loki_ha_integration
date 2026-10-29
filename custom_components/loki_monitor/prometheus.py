"""Prometheus-only event counter backend for Alloy's per-service counters.

Two grouped instant queries (1h/24h) feed every fleet, host and service count.
Log searches and message-based alerts intentionally remain in Loki.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

from .time_range import validate_exact_range

METRIC = "homelab_log_service_events_total"
COUNTER_JOB = "homelab-alloy-logs"
COUNTER_LEVELS = ("high", "warning", "normal", "unknown")


class PrometheusError(Exception):
    """Safe Prometheus connection/query failure without request details."""


def validate_prometheus_url(url: str) -> str:
    """Require a Prometheus server URL, not the Grafana web UI or API URL."""
    url = url.strip().rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("Expected http(s) Prometheus base URL without path or credentials")
    return url


def service_severity_query(window: str) -> str:
    """First-sample corrected grouped count, matching Grafana v2.5.3.

    Corrects newly appearing counter series when no sample exists at the
    start of the window. It is still an approximation across counter resets,
    stale series, scrape gaps, and lookback boundaries.
    """
    if window not in ("1h", "24h"):
        raise ValueError("Unsupported counter window")
    selector = f'{METRIC}{{job="{COUNTER_JOB}"}}'
    minimum = f"min_over_time({selector}[{window}])"
    delta = f"increase({selector}[{window}])"
    corrected = (
        f"(({delta} or (0 * {minimum})) + "
        f"(({minimum} unless {selector} offset {window}) or (0 * {minimum})))"
    )
    return f"sum by (host, service_name, severity_group) ({corrected})"


def parse_service_severity_vector(data: Any) -> dict[tuple[str, str, str], float]:
    """Validate one Prometheus response; never interpret errors as zero."""
    if not isinstance(data, dict) or data.get("status") != "success":
        raise PrometheusError("Prometheus query was unsuccessful")
    block = data.get("data")
    if not isinstance(block, dict) or block.get("resultType") != "vector":
        raise PrometheusError("Prometheus did not return an instant vector")
    vector = block.get("result")
    if not isinstance(vector, list) or not vector:
        raise PrometheusError("No Alloy service-counter samples in the requested window")
    result: dict[tuple[str, str, str], float] = {}
    for series in vector:
        if not isinstance(series, dict) or not isinstance(series.get("metric"), dict):
            raise PrometheusError("Invalid Prometheus series")
        labels: Mapping[str, str] = series["metric"]
        host, service, level = (
            labels.get("host"), labels.get("service_name", ""),
            labels.get("severity_group"),
        )
        value = series.get("value")
        if (not isinstance(host, str) or not host
                or not isinstance(service, str)
                or level not in COUNTER_LEVELS):
            raise PrometheusError("Unexpected Prometheus service-counter labels")
        if not isinstance(value, list) or len(value) != 2:
            raise PrometheusError("Invalid Prometheus sample")
        try:
            number = float(value[1])
        except (TypeError, ValueError) as err:
            raise PrometheusError("Invalid Prometheus sample value") from err
        if not math.isfinite(number) or number < -0.001:
            raise PrometheusError("Invalid Prometheus count value")
        key = (host, service, level)
        result[key] = result.get(key, 0.0) + max(0.0, number)
    return result


class PrometheusClient:
    """Small unauthenticated Prometheus HTTP API client for a trusted LAN."""

    def __init__(self, session: ClientSession, base_url: str) -> None:
        self._session = session
        self._url = validate_prometheus_url(base_url)

    async def _query(self, query: str, *, at_seconds: int | None = None) -> Any:
        params = {"query": query}
        if at_seconds is not None:
            params["time"] = str(at_seconds)
        try:
            async with self._session.get(
                f"{self._url}/api/v1/query",
                params=params,
                timeout=ClientTimeout(total=25),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise PrometheusError(f"Prometheus returned HTTP {response.status}")
                try:
                    return await response.json(content_type=None)
                except (ValueError, TypeError) as err:
                    raise PrometheusError("Invalid Prometheus JSON") from err
        except asyncio.TimeoutError as err:
            raise PrometheusError("Prometheus request timed out") from err
        except ClientError as err:
            raise PrometheusError("Could not connect to Prometheus") from err

    async def async_test_connection(self) -> None:
        """Verify PromQL is reachable and that the expected Alloy metric exists."""
        data = await self._query(f'count({METRIC}{{job="{COUNTER_JOB}"}})')
        if not isinstance(data, dict) or data.get("status") != "success":
            raise PrometheusError("Prometheus query test failed")
        block = data.get("data", {})
        values = block.get("result", []) if isinstance(block, dict) else []
        try:
            found = (block.get("resultType") == "vector"
                     and len(values) == 1 and float(values[0]["value"][1]) > 0)
        except (TypeError, ValueError, KeyError, IndexError):
            found = False
        if not found:
            raise PrometheusError("Expected Alloy service counters were not found")

    async def async_counter_window(self, window: str) -> dict[tuple[str, str, str], float]:
        return parse_service_severity_vector(await self._query(service_severity_query(window)))

    async def async_counter_exact_range(
        self, start_ns: str, end_ns: str
    ) -> dict[tuple[str, str, str], float]:
        """On-demand historical counts, evaluated at the exact selected end.

        One grouped PromQL instant query. No background polling or per-host calls.
        The same first-observed-sample correction as Grafana v2.5.3 is used.
        """
        start, end = validate_exact_range(start_ns, end_ns)
        seconds = (end - start) // 1_000_000_000
        base = f'{METRIC}{{job="{COUNTER_JOB}"}}'
        window = f'{seconds}s'
        minimum = f'min_over_time({base}[{window}])'
        delta = f'increase({base}[{window}])'
        corrected = (
            f'(({delta} or (0 * {minimum})) + '
            f'(({minimum} unless {base} offset {window}) or (0 * {minimum})))'
        )
        query = f'sum by (host, service_name, severity_group) ({corrected})'
        return parse_service_severity_vector(
            await self._query(query, at_seconds=end // 1_000_000_000)
        )
