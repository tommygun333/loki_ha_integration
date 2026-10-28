"""Optional Prometheus reader for low-cardinality Alloy service counters.

Host-only/built-in severity statistics can use this cheap API; log searches,
alert rules, last error and service-scoped monitors remain Loki-backed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

METRIC = 'homelab_log_service_events_total'
COUNTER_JOB = 'homelab-alloy-logs'
COUNTER_LEVELS = ('high', 'warning')


class PrometheusError(Exception):
    """Safe, nonsensitive Prometheus connection/query failure."""


def validate_prometheus_url(url: str) -> str:
    """Accept a server root, never userinfo, query parameters or fragments."""
    url = url.strip().rstrip('/')
    parsed = urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')):
        raise ValueError('Expected a Prometheus base URL, without path or credentials')
    return url


def host_severity_query(window: str) -> str:
    """Same first-observed-sample correction as primary dashboard v2.5.3.

    Compensates for the first sample of a newly appearing counter series, when
    the series did not exist at the beginning of the requested range. As with
    Grafana, resets and sparse scrapes can make the estimate imperfect.
    """
    if window not in ('1h', '24h'):
        raise ValueError('Unsupported counter window')
    selector = (
        METRIC + '{job="' + COUNTER_JOB + '",severity_group=~"high|warning"}'
    )
    minimum = f'min_over_time({selector}[{window}])'
    delta = f'increase({selector}[{window}])'
    per_series = (
        f'(({delta} or (0 * {minimum})) + '
        f'(({minimum} unless {selector} offset {window}) or (0 * {minimum})))'
    )
    return f'sum by (host, severity_group) ({per_series})'


def parse_host_severity_vector(data: Any) -> dict[tuple[str, str], float]:
    """Require valid vectors; never silently convert API failures to zero."""
    if not isinstance(data, dict) or data.get('status') != 'success':
        raise PrometheusError('Prometheus query was unsuccessful')
    block = data.get('data')
    if not isinstance(block, dict) or block.get('resultType') != 'vector':
        raise PrometheusError('Prometheus did not return an instant vector')
    vector = block.get('result')
    if not isinstance(vector, list):
        raise PrometheusError('Invalid Prometheus result')
    result = {}
    for series in vector:
        if not isinstance(series, dict) or not isinstance(series.get('metric'), dict):
            raise PrometheusError('Invalid Prometheus series')
        labels: Mapping[str, str] = series['metric']
        host, level = labels.get('host'), labels.get('severity_group')
        value = series.get('value')
        if not isinstance(host, str) or not host or level not in COUNTER_LEVELS:
            raise PrometheusError('Unexpected Prometheus metric labels')
        if not isinstance(value, list) or len(value) != 2:
            raise PrometheusError('Invalid Prometheus sample')
        try:
            number = float(value[1])
        except (TypeError, ValueError) as err:
            raise PrometheusError('Invalid Prometheus sample value') from err
        import math
        if not math.isfinite(number) or number < -0.001:
            raise PrometheusError('Invalid Prometheus count value')
        result[(host, level)] = max(0.0, number)
    return result


class PrometheusClient:
    def __init__(self, session: ClientSession, base_url: str) -> None:
        self._session = session
        self._url = validate_prometheus_url(base_url)

    async def _query(self, query: str) -> Any:
        try:
            async with self._session.get(
                f'{self._url}/api/v1/query',
                params={'query': query},
                timeout=ClientTimeout(total=25),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise PrometheusError(f'Prometheus returned HTTP {response.status}')
                try:
                    return await response.json(content_type=None)
                except (ValueError, TypeError) as err:
                    raise PrometheusError('Invalid Prometheus JSON') from err
        except asyncio.TimeoutError as err:
            raise PrometheusError('Prometheus request timed out') from err
        except ClientError as err:
            raise PrometheusError('Could not connect to Prometheus') from err

    async def async_test_connection(self) -> None:
        data = await self._query('vector(1)')
        if not isinstance(data, dict) or data.get('status') != 'success':
            raise PrometheusError('Prometheus query test failed')

    async def async_counter_window(self, window: str) -> dict[tuple[str, str], float]:
        return parse_host_severity_vector(await self._query(host_severity_query(window)))
