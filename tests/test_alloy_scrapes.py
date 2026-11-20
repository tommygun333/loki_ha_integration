"""Alloy health must reflect Prometheus scrapes, not event counts."""

import pytest
from loki_monitor.prometheus import (
    PrometheusError, alloy_scrape_query, parse_alloy_scrape_vector,
)


def response(*series):
    return {'status': 'success', 'data': {'resultType': 'vector', 'result': list(series)}}


def target(collector, instance, up):
    return {'metric': {'job': 'homelab-alloy-logs', 'collector_node': collector,
                       'instance': instance}, 'value': [1, str(up)]}


def test_matches_grafana_dashboard_and_reports_down_targets():
    assert alloy_scrape_query() == 'up{job="homelab-alloy-logs"}'
    result = parse_alloy_scrape_vector(response(
        target('CoreELEC', '192.168.1.17:12345', 0),
        target('ct107', '192.168.1.13:12345', 1),
    ))
    assert result['CoreELEC'] == {
        'up': False, 'targets': [{'instance': '192.168.1.17:12345', 'up': False}],
    }
    assert result['ct107']['up'] is True


def test_multiple_targets_all_must_be_up():
    result = parse_alloy_scrape_vector(response(
        target('multi', '192.168.1.10:12345', 1),
        target('multi', '192.168.1.11:12345', 0),
    ))
    assert result['multi']['up'] is False
    assert len(result['multi']['targets']) == 2


def test_missing_targets_not_reported_as_failed():
    assert parse_alloy_scrape_vector(response()) == {}
    assert parse_alloy_scrape_vector(response(target('CoreELEC', 'ce:12345', 1))).get('absent') is None


def test_rejects_failed_malformed_or_non_boolean_samples():
    samples = [
        {'status': 'error'}, {'status': 'success', 'data': {'resultType': 'scalar'}},
        response({'metric': {'instance': 'bad'}, 'value': [1, 'NaN']}),
        response(target('CoreELEC', 'ce:12345', 2)),
        response({'metric': {'collector_node': 'CoreELEC'}, 'value': [1, '1']}),
    ]
    for item in samples:
        with pytest.raises(PrometheusError):
            parse_alloy_scrape_vector(item)
