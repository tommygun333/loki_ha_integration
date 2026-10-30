"""Regression for missing attributes in unrelated Home Assistant sensors."""
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, StrictUndefined

TEMPLATE = (Path(__file__).parents[1] / 'examples' / 'fleet_summary_safe_template.jinja').read_text()


def test_handles_sensors_without_monitor_attributes():
    # Need a callable states() AND states.sensor, like Home Assistant.
    class HAStates:
        def __init__(self, sensor): self.sensor = sensor
        def __call__(self, entity):
            return {'sensor.grafana_loki_query_failures':'2',
                    'sensor.grafana_loki_alert_rule_failures':'0'}.get(entity,'unknown')
    env = Environment(undefined=StrictUndefined)
    tpl = env.from_string(TEMPLATE)
    sensors = [
        SimpleNamespace(attributes={'monitor_type':'host','loki_entry_id':'abc'}),
        SimpleNamespace(attributes={'monitor_type':'host','loki_entry_id':'abc'}),
        SimpleNamespace(attributes={'monitor_type':'host','loki_entry_id':'different'}),
        SimpleNamespace(attributes={'monitor_type':'service','loki_entry_id':'abc'}),
        SimpleNamespace(attributes={'monitor_type':'host'}),
        SimpleNamespace(attributes={'other':'value'}),
        SimpleNamespace(attributes={}),
    ]
    states = HAStates(sensors)
    def attr(entity, name):
        assert entity == 'sensor.server_grafana_loki_fleet_log_summary'
        return {'loki_entry_id':'abc','total_1h':1234}.get(name)
    result = tpl.render(states=states, state_attr=attr)
    assert '2 hosts monitored' in result
    assert '1,234 logs (1h)' in result
    assert 'Query failures 2' in result
    assert 'Alert-rule failures 0' in result
    assert 'ReadOnlyDict' not in result


def test_missing_entry_gracefully_returns_zero():
    env = Environment(undefined=StrictUndefined)
    tpl = env.from_string(TEMPLATE)
    class HAStates:
        sensor = [SimpleNamespace(attributes={})]
        def __call__(self, entity): return 'unavailable'
    result = tpl.render(states=HAStates(), state_attr=lambda entity, key: None)
    assert '0 hosts monitored' in result
    assert '0 logs (1h)' in result
