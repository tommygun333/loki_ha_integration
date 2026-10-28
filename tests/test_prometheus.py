"""Pure counter-query tests; no HA or Prometheus server needed."""

import pytest
from loki_monitor.prometheus import (
    PrometheusError, host_severity_query, parse_host_severity_vector,
    validate_prometheus_url,
)


def test_supported_query_matches_dashboard_and_groups():
    query = host_severity_query('1h')
    assert query.startswith('sum by (host, severity_group)')
    assert 'homelab_log_service_events_total' in query
    assert 'min_over_time' in query
    assert 'increase(' in query
    assert ' unless ' in query
    assert 'offset 1h' in query
    assert 'first_over_time' not in query
    assert 'offset 24h' in host_severity_query('24h')
    with pytest.raises(ValueError):
        host_severity_query('10m')


def test_parse_result_and_reject_bad_data():
    data = { 'status': 'success', 'data': {'resultType': 'vector', 'result': [
        {'metric': {'host': 'OPNsense.internal', 'severity_group': 'warning'}, 'value': [1, '3.5']},
        {'metric': {'host': 'proxmox', 'severity_group': 'high'}, 'value': [1, '2']},
    ]}}
    assert parse_host_severity_vector(data) == {
        ('OPNsense.internal', 'warning'): 3.5, ('proxmox', 'high'): 2.0,
    }
    with pytest.raises(PrometheusError):
        parse_host_severity_vector({'status': 'error'})
    with pytest.raises(PrometheusError):
        parse_host_severity_vector({'status': 'success','data': {'resultType':'scalar'}})


def test_url_validation():
    assert validate_prometheus_url(' http://192.168.1.13:9090/ ') == 'http://192.168.1.13:9090'
    for bad in ('ftp://host', 'http://admin:pass@host', 'http://host/api/v1/query',
                'http://host/?token=secret'):
        with pytest.raises(ValueError):
            validate_prometheus_url(bad)
