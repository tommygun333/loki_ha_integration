"""Count-only Prometheus and explicitly on-demand Loki guarantees."""
import ast
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]/'custom_components'/'loki_monitor'

def test_forms_keep_prometheus_required_and_bulk_hosts():
    text=(BASE/'config_flow.py').read_text()
    assert 'vol.Required(CONF_PROMETHEUS_URL' in text
    assert 'CONF_PROMETHEUS_URL: validate_prometheus_url' in text
    assert 'async def async_step_add_hosts' in text
    assert "poll_custom_queries" in text
    assert "vol.Required(CONF_SEVERITY_SOURCE" not in text

def test_no_automatic_loki_metrics_in_coordinator():
    src=(BASE/'coordinator.py').read_text()
    ast.parse(src)
    assert 'count_query(' not in src
    assert 'async_recent_logs' not in src
    assert 'async_labels()' not in src
    assert "self.prometheus.async_counter_window('1h')" in src
    assert "self.prometheus.async_counter_window('24h')" in src

def test_prometheus_mandatory_at_startup():
    src=(BASE/'__init__.py').read_text()
    assert 'prometheus = PrometheusClient(' in src
    assert 'if entry.options.get(CONF_COUNTER_BACKEND)' not in src
