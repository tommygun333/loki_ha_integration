"""Prometheus count summaries; Loki is reserved for requested searches and rules."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LokiAuthError, LokiClient, LokiError
from .alerts import CONF_ALERT_RULES
from .const import CONF_CUSTOM_QUERIES, CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS, DOMAIN
from .logic import extract_number
from .monitoring import CONF_MONITORS
from .prometheus import PrometheusClient, PrometheusError

_LOGGER = logging.getLogger(__name__)
SEVERITY_GROUPS = ('high', 'warning', 'normal', 'unknown')
WINDOWS = ('1h', '24h')


def summarize(series: dict[tuple[str, str, str], float], *, host: str = '', service: str = '') -> dict[str, int]:
    """All dimensions of a host/service summary from one grouped Prometheus vector.

    Missing series in an otherwise successful Prometheus response count as zero.
    Do not call this if the Prometheus request failed.
    """
    result: dict[str, int] = {}
    for level in SEVERITY_GROUPS:
        total = sum(v for (h, s, group), v in series.items()
                    if group == level and (not host or h == host) and (not service or s == service))
        result[level] = max(0, round(total))
    result['total'] = sum(result.values())
    return result


class LokiCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Exactly two automatic count queries. No scheduled Loki queries by default."""

    def __init__(self, hass: HomeAssistant, api: LokiClient, options: dict[str, Any],
                 alerts=None, prometheus: PrometheusClient | None = None) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN,
                         update_interval=timedelta(seconds=options.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)))
        if prometheus is None:
            raise ValueError('A Prometheus client is required')
        self.api = api
        self.prometheus = prometheus
        self.alerts = alerts
        self.options = options
        self._failing_queries: set[str] = set()

    async def _async_update_data(self) -> dict[str, Any]:
        monitored = self.options.get(CONF_MONITORS, [])
        custom = self.options.get(CONF_CUSTOM_QUERIES, []) if self.options.get('poll_custom_queries', False) else []
        work = {
            'prometheus_1h': self.prometheus.async_counter_window('1h'),
            'prometheus_24h': self.prometheus.async_counter_window('24h'),
        }
        # User-authored LogQL is a deliberate opt-in, never a hidden background poll.
        for item in custom:
            work[f"custom_{item['id']}"] = self.api.async_query(item['query'])
        semaphore = asyncio.Semaphore(4)

        async def limited(coro):
            async with semaphore:
                return await coro

        names = list(work)
        responses = await asyncio.gather(*(limited(c) for c in work.values()), return_exceptions=True)
        values: dict[str, Any] = {}
        failed: set[str] = set()
        for name, response in zip(names, responses, strict=True):
            if isinstance(response, asyncio.CancelledError):
                raise response
            if isinstance(response, LokiAuthError):
                raise ConfigEntryAuthFailed('Loki authentication failed') from response
            if isinstance(response, BaseException):
                failed.add(name)
                values[name] = None
                if name not in self._failing_queries:
                    detail = str(response) if isinstance(response, (LokiError, PrometheusError)) else type(response).__name__
                    _LOGGER.warning('Log monitor query %s failed (%s)', name, detail)
                    self._failing_queries.add(name)
            else:
                values[name] = response
                if name in self._failing_queries:
                    _LOGGER.info('Log monitor query %s recovered', name)
                    self._failing_queries.discard(name)

        # Both required counter queries failed: never publish stale counts as zero.
        if all(f'prometheus_{w}' in failed for w in WINDOWS):
            raise UpdateFailed('Prometheus count queries failed')

        results: dict[str, Any] = {
            'fleet_summary': {}, 'monitor_summaries': {m['id']: {} for m in monitored},
            'prometheus_available': not any(f'prometheus_{w}' in failed for w in WINDOWS),
        }
        for window in WINDOWS:
            series = values.get(f'prometheus_{window}')
            if not isinstance(series, dict):
                for key in (*SEVERITY_GROUPS, 'total'):
                    results['fleet_summary'][f'{key}_{window}'] = None
                    for m in monitored:
                        results['monitor_summaries'][m['id']][f'{key}_{window}'] = None
                continue
            for key, value in summarize(series).items():
                results['fleet_summary'][f'{key}_{window}'] = value
            for monitor in monitored:
                for key, value in summarize(series, host=monitor['host'], service=monitor.get('service', '')).items():
                    results['monitor_summaries'][monitor['id']][f'{key}_{window}'] = value

        for item in self.options.get(CONF_CUSTOM_QUERIES, []):
            key = f"custom_{item['id']}"
            if item not in custom:
                results[key] = None
                continue
            if key in failed:
                results[key] = None
                continue
            try:
                results[key] = extract_number(values[key])
            except (TypeError, KeyError, ValueError):
                results[key] = None
                failed.add(key)

        if self.alerts is not None:
            try:
                results.update(await self.alerts.async_check(self.options.get(CONF_ALERT_RULES, [])))
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception('Loki alert processor failed')
                results['alert_rule_failures'] = [r['id'] for r in self.options.get(CONF_ALERT_RULES, []) if r.get('enabled', True)]
                results['alerts_triggered'] = 0
        results['alert_rule_failures_count'] = len(results.get('alert_rule_failures', []))
        results['failed_queries'] = sorted(failed)
        results['failed_query_count'] = len(failed)
        return results
