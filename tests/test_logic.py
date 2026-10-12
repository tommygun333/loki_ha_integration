"""Tests of LogQL builders and numeric/log response extraction."""

from datetime import UTC, datetime

import pytest

from loki_monitor.logic import (
    count_query,
    detected_level_selector,
    extract_latest_timestamp,
    extract_number,
    flatten_streams,
    level_selector,
    validate_label_name,
)


def test_logql_metric_escapes_literal_values():
    selector = level_selector("level", "error, warn, a.b")
    assert selector == '{level=~"(?i)(error|warn|a\\\\.b)"}'
    assert count_query(selector, "1h") == f"sum(count_over_time({selector}[1h])) or vector(0)"




def test_detected_level_query_matches_grafana_dashboard_v15():
    warn = detected_level_selector("host", "warn")
    assert warn == (
        '{host=~".+",source=~".*",service_name=~".*",syslog_identifier=~".*"}'
        ' | detected_level=~"(?i)warn"'
    )
    assert count_query(warn, "24h") == (
        'sum(count_over_time({host=~".+",source=~".*",service_name=~".*",'
        'syslog_identifier=~".*"} | detected_level=~"(?i)warn"[24h])) or vector(0)'
    )
    error = detected_level_selector("host", "error")
    assert 'detected_level=~"(?i)(error|critical|fatal)"' in error
    assert '{host=~".+"' in detected_level_selector("device", "warn").replace('device=~', 'host=~')
    with pytest.raises(ValueError):
        detected_level_selector("bad-label", "warn")
    with pytest.raises(ValueError):
        detected_level_selector("host", "unknown")


@pytest.mark.parametrize("label", ["-foo", "foo:bar", "3abc", "my.host", ""]) 
def test_bad_label_rejected(label):
    with pytest.raises(ValueError):
        validate_label_name(label)


def test_extract_vector_and_scalar():
    assert extract_number({"resultType": "vector", "result": [{"value": [100, "24"]}]}) == 24
    assert extract_number({"resultType": "scalar", "result": [100, "4.5"]}) == 4.5
    assert extract_number({"resultType": "vector", "result": []}) is None
    assert extract_number({"resultType": "vector", "result": []}, empty_is_zero=True) == 0
    with pytest.raises(ValueError):
        extract_number({"resultType": "vector", "result": [{"value": [1, "1"]}, {"value": [1, "2"]}]})
    with pytest.raises(ValueError):
        extract_number({"resultType": "streams", "result": []})


def test_timestamp_and_flatten_streams():
    data = {"resultType": "streams", "result": [
        {"stream": {"host": "A"}, "values": [["1000000000", "old"], ["3000000000", "latest", {"trace": "a"}]]},
        {"stream": {"host": "B"}, "values": [["2000000000", "middle"]]},
    ]}
    assert extract_latest_timestamp(data) == datetime(1970, 1, 1, 0, 0, 3, tzinfo=UTC)
    result = flatten_streams(data, 2)
    assert len(result) == 2
    assert [row["line"] for row in result] == ["latest", "middle"]
    assert result[0]["metadata"] == {"trace": "a"}
