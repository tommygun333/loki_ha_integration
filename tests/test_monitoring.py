"""Validate scoped LogQL, escaping and monitor constraints."""

import pytest

from loki_monitor.monitoring import clean_monitor, monitor_selector


def make(mon=None, **kwargs):
    return monitor_selector(
        mon or {"host": "CoreELEC", "service": "kodi", "name": "Kodi"},
        host_label=kwargs.get("host_label", "host"),
        severity_source=kwargs.get("severity_source", "detected"),
        level_label=kwargs.get("level_label", "level"),
        severity_values=kwargs.get("severity_values", "warn,warning"),
        severity=kwargs.get("severity", "warn"),
    )


def test_detected_scope_host_service():
    assert make() == '{host="CoreELEC",service_name="kodi"} | detected_level=~"(?i)warn"'
    assert make(severity="error").endswith('detected_level=~"(?i)(error|critical|fatal)"')


def test_host_only_no_service_selector():
    assert make({"name": "pve", "host": "pve", "service": ""}) == (
        '{host="pve"} | detected_level=~"(?i)warn"'
    )


def test_untrusted_values_are_quoted_not_executed():
    q = make({"name": "test", "host": 'foo",job="other', "service": 'service" or {job="*'})
    assert r'host="foo\",job=\"other"' in q
    assert r'service_name="service\" or {job=\"*"' in q
    assert q.count("} | detected_level") == 1


def test_custom_host_label_and_indexed_scope():
    assert make(host_label="device", severity_source="indexed", level_label="sev", severity_values="Warning,alert") == (
        '{device="CoreELEC",service_name="kodi",sev=~"(?i)(Warning|alert)"}'
    )


def test_reject_bad_label_names_and_values():
    with pytest.raises(ValueError):
        make(host_label="host-x")
    with pytest.raises(ValueError):
        clean_monitor("bad\nmonitor", "host")
    with pytest.raises(ValueError):
        clean_monitor("name", "")
    with pytest.raises(ValueError):
        clean_monitor("n" * 129, "host")


def test_monitor_cleanup_and_optional_service():
    assert clean_monitor("  PVE  ", " pve ") == {"name":"PVE", "host":"pve", "service":""}
