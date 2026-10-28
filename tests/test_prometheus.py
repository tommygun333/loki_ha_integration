"""Prometheus-only counter query and parser contracts."""

import pytest
from loki_monitor.prometheus import (
    PrometheusError, service_severity_query, parse_service_severity_vector,
    validate_prometheus_url,
)


def sample(host, service, severity, amount):
    return {
        'metric': {'host': host, 'service_name': service, 'severity_group': severity},
        'value': [1, str(amount)],
    }


def test_queries_match_dashboard_and_preserve_service_names():
    for window in ('1h', '24h'):
        q = service_severity_query(window)
        assert q.startswith('sum by (host, service_name, severity_group)')
        assert 'homelab_log_service_events_total{job="homelab-alloy-logs"}' in q
        assert 'increase(' in q and 'min_over_time(' in q
        assert f'offset {window}' in q and ' unless ' in q
        assert 'first_over_time' not in q
    with pytest.raises(ValueError):
        service_severity_query('10m')


def test_parser_supports_host_and_service_and_all_event_levels():
    vector = {
        'status': 'success',
        'data': {'resultType': 'vector', 'result': [
            sample('OPNsense.internal', 'opnsense-system', 'high', 3),
            sample('OPNsense.internal', 'opnsense-system', 'warning', 1.5),
            sample('pve', 'pvedaemon.service', 'normal', 10),
            sample('pve', 'pvedaemon.service', 'unknown', 2),
        ]},
    }
    assert parse_service_severity_vector(vector) == {
        ('OPNsense.internal', 'opnsense-system', 'high'): 3.0,
        ('OPNsense.internal', 'opnsense-system', 'warning'): 1.5,
        ('pve', 'pvedaemon.service', 'normal'): 10.0,
        ('pve', 'pvedaemon.service', 'unknown'): 2.0,
    }


def test_parser_rejects_failures_not_return_zero():
    bad = [
        {'status': 'error'},
        {'status': 'success', 'data': {'resultType': 'scalar'}},
        {'status': 'success', 'data': {'resultType': 'vector', 'result': []}},
        {'status': 'success', 'data': {'resultType': 'vector', 'result': [sample('pve', 'system', 'high', 'NaN')]}},
        {'status': 'success', 'data': {'resultType': 'vector', 'result': [sample('pve', 'system', 'high', -3)]}},
    ]
    for item in bad:
        with pytest.raises(PrometheusError):
            parse_service_severity_vector(item)


def test_validate_url():
    assert validate_prometheus_url(' http://192.168.1.13:9090/ ') == 'http://192.168.1.13:9090'
    for bad in ('', 'ftp://host', 'http://admin:pass@host',
                'http://host/api/v1/query', 'http://host/?token=secret'):
        with pytest.raises(ValueError):
            validate_prometheus_url(bad)
