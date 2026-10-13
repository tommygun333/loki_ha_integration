"""Security, LogQL, pagination and resource-contract tests for the log viewer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loki_monitor.viewer import (
    ViewerInputError, build_viewer_query, get_time_bounds, page_streams, MINUTE_NS,
)


def test_viewer_matches_dashboard_scope_and_detected_severity():
    query = build_viewer_query(severity="warnings")
    assert query.startswith('{host=~".+"} | label_format severity=`')
    assert query.endswith(' | severity=~"(?i)^warning$"')
    assert 'contains "[emerg]" $line' in query
    assert build_viewer_query(severity="errors").endswith(' | severity=~"(?i)^(emergency|alert|critical|fatal|error)$"')
    assert build_viewer_query(severity="warnings", severity_mode="detected") == '{host=~".+"} | detected_level=~"(?i)warn"'


def test_exact_filters_do_not_inject_logql():
    host = 'CoreELEC",job=~".*'
    service = 'kodi" | json | line_format "oops'
    query = build_viewer_query(host=host, service=service, text='a" | drop x', regex='404|500')
    assert f'host={json.dumps(host)}' in query
    assert f'service_name={json.dumps(service)}' in query
    assert f'|= {json.dumps("a\" | drop x")}' in query
    assert '|~ "404|500"' in query
    assert f'host={json.dumps(host)}' in query


@pytest.mark.parametrize("kwargs", [
    {"severity":"ROOT"}, {"host":"a" * 130}, {"text":"b" * 257},
    {"regex":"["}, {"host_label":"host} | json"},
    {"program":"bad\nprogram"}, {"severity_mode":"invalid"},
    {"service_label":"service} | bad"},
])
def test_viewer_rejects_invalid_inputs(kwargs):
    with pytest.raises(ViewerInputError):
        build_viewer_query(**kwargs)


def test_anchor_and_cursor_validate_ranges():
    now = 1791800000000000000
    start, end, effective = get_time_bounds(minutes=60, now_ns=now)
    assert (start,end,effective) == (now - 60*MINUTE_NS, now, now)
    start2, end2, effective2 = get_time_bounds(
        minutes=60, end_ns=str(end), cursor_ns=str(end - 200), now_ns=now + 1000,
    )
    assert start2 == start and end2 == end and effective2 == end-200
    for kwargs in [
        {"minutes":0},{"minutes":10081}, {"minutes":60,"cursor_ns":str(now+10)},
        {"minutes":60,"cursor_ns":"1 or 1=1"},
        {"minutes":60,"end_ns":str(now-31*24*60*MINUTE_NS)},
    ]:
        with pytest.raises(ViewerInputError):
            get_time_bounds(now_ns=now, **kwargs)


def test_page_newest_first_with_cursor_and_metadata():
    data={"resultType":"streams","result":[
        {"stream":{"host":"h"},"values":[["300","new",{"detected_level":"warn"}],["100","old"]]},
        {"stream":{"host":"x"},"values":[["200","middle"]]},
    ]}
    rows,cursor,more=page_streams(data,2)
    assert [x["line"] for x in rows]==["new","middle"]
    assert rows[0]["metadata"]["detected_level"]=="warn"
    assert (cursor,more)==("199",True)
    assert page_streams(data,5)[1:] == (None,False)


def test_card_no_eval_and_dynamic_logs_are_text_only():
    path=Path(__file__).resolve().parents[1]/"custom_components/loki_monitor/frontend/loki-log-viewer-card.js"
    code=path.read_text()
    assert 'customElements.define("loki-log-viewer-card"' in code
    assert 'loki_monitor/query_logs' in code
    assert 'loki_monitor/sources' in code
    assert 'line.textContent = String(log.line' in code
    assert 'data.textContent = JSON.stringify(' in code
    assert "eval(" not in code


def test_cascading_scopes_follow_grafana_v19_filters():
    from loki_monitor.viewer import build_filter_scopes
    scopes = build_filter_scopes(host="CoreELEC", source="journal", service="kodi")
    assert scopes["host"] == '{host=~".+"}'
    assert scopes["source"] == '{host="CoreELEC"}'
    assert scopes["service"] == '{host="CoreELEC",source="journal"}'
    assert scopes["program"] == '{host="CoreELEC",source="journal",service_name="kodi"}'
    independent = build_filter_scopes(host="CoreELEC", source="journal", cascading=False)
    assert all(scope == '{host=~".+"}' for scope in independent.values())


def test_cascading_scope_escapes_untrusted_values_and_custom_label_map():
    from loki_monitor.viewer import build_filter_scopes
    name = 'x"} | json |~".*'
    scopes = build_filter_scopes(host=name, source="journal", source_label="log_source", service_label="svc")
    assert f'host={json.dumps(name)}' in scopes["source"]
    assert 'log_source="journal"' in scopes["service"]
    assert 'service_name' not in scopes["program"]
    with pytest.raises(ViewerInputError):
        build_filter_scopes(host_label="host", source_label="host")
    assert '{log_source="journal"}' not in scopes["service"]


def test_grafana_v19_severity_exact_template():
    from loki_monitor.viewer import CANONICAL_SEVERITY_TEMPLATE
    from tools.grafana_dashboard_profile import severity_template, log_panel_query
    import os
    dashboard_file = os.environ.get("GRAFANA_V19_FILE")
    if dashboard_file and Path(dashboard_file).is_file():
        dashboard = json.loads(Path(dashboard_file).read_text())
        assert CANONICAL_SEVERITY_TEMPLATE == severity_template(log_panel_query(dashboard))
    assert '[emerg]' in CANONICAL_SEVERITY_TEMPLATE
    assert '.level | lower' in CANONICAL_SEVERITY_TEMPLATE
    assert '.detected_level | lower' in CANONICAL_SEVERITY_TEMPLATE


def test_card_supports_cascading_hidden_hosts_and_label_maps():
    code = (Path(__file__).resolve().parents[1] / "custom_components/loki_monitor/frontend/loki-log-viewer-card.js").read_text()
    assert 'this._cascadingFilters' in code
    assert 'this._hideIpHosts' in code
    assert 'this._hiddenHosts' in code
    assert 'this._labelMap' in code
    assert '_suggestionRevision' in code
    assert 'labels.severity' in code


def test_multiselect_stream_matchers_are_exact_and_escaped():
    """Two label selections are OR, but metacharacters are literal."""
    import re
    host = ["CoreELEC", "pve"]
    service = ["npm/foo.bar", 'x"} | json |~ ".*']
    query = build_viewer_query(host=host, source=["journal", "docker"], service=service)
    assert 'host=~"^(?:CoreELEC|pve)$"' in query
    assert 'source=~"^(?:journal|docker)$"' in query
    expected = "^(?:" + "|".join(re.escape(v) for v in service) + ")$"
    assert f'service_name=~{json.dumps(expected)}' in query
    assert query.count(' | label_format severity=`') == 1
    assert build_viewer_query(host=["CoreELEC"]).startswith('{host="CoreELEC"}')
    assert build_viewer_query(host=[]).startswith('{host=~".+"}')


def test_multiselect_cascade_scopes_and_legacy_scalars():
    from loki_monitor.viewer import build_filter_scopes
    scopes = build_filter_scopes(
        host=["CoreELEC", "pve"], source=["journal", "docker"], service=["kodi", "nginx"]
    )
    assert scopes["source"] == '{host=~"^(?:CoreELEC|pve)$"}'
    assert scopes["service"] == '{host=~"^(?:CoreELEC|pve)$",source=~"^(?:journal|docker)$"}'
    assert scopes["program"] == '{host=~"^(?:CoreELEC|pve)$",source=~"^(?:journal|docker)$",service_name=~"^(?:kodi|nginx)$"}'
    assert all(value == '{host=~".+"}' for value in build_filter_scopes(
        host=["CoreELEC", "pve"], source="journal", cascading=False
    ).values())


@pytest.mark.parametrize("bad", [
    [""], ["x\nfoo"], [123], ["a" * 129], ["x"] * 25, 17, {"arbitrary": "object"},
])
def test_multiselect_input_is_bounded_and_typed(bad):
    with pytest.raises(ViewerInputError):
        build_viewer_query(host=bad)
    with pytest.raises(ViewerInputError):
        from loki_monitor.viewer import build_filter_scopes
        build_filter_scopes(source=bad)


def test_multiselect_deduplicates_values():
    from loki_monitor.viewer import normalize_filter_values
    assert normalize_filter_values(["CoreELEC", "CoreELEC", "pve"]) == ["CoreELEC", "pve"]
    assert normalize_filter_values("CoreELEC") == ["CoreELEC"]
