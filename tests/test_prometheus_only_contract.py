"""Static regression tests: the count backend and configuration must be explicit."""

import ast
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / 'custom_components' / 'loki_monitor'


def test_prometheus_url_is_in_initial_and_options_forms():
    flow = (BASE / 'config_flow.py').read_text(encoding='utf-8')
    assert 'vol.Required(CONF_PROMETHEUS_URL' in flow
    assert 'CONF_PROMETHEUS_URL: validate_prometheus_url' in flow
    assert 'await PrometheusClient(' in flow
    assert 'COUNTER_BACKEND_PROMETHEUS' not in flow.split('    async def async_step_settings')[1].split('    def _metric_schema')[0]
    assert 'async_step_add_hosts' in flow


def test_no_loki_event_count_in_coordinator():
    code = (BASE / 'coordinator.py').read_text(encoding='utf-8')
    tree = ast.parse(code)
    assert 'count_query(' not in code
    assert 'count_over_time' not in code.replace('count_over_time/rate', '')
    assert 'async_counter_window("1h")' in code
    assert 'async_counter_window("24h")' in code
    assert 'async_recent_logs(' in code
    assert 'self.api.async_query(' in code  # only user-defined custom LogQL


def test_prometheus_mandatory_at_startup():
    text = (BASE / '__init__.py').read_text(encoding='utf-8')
    assert 'if entry.options.get(CONF_COUNTER_BACKEND)' not in text
    assert 'prometheus = PrometheusClient(' in text
