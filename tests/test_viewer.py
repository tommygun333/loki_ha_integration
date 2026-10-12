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
    assert query == '{host=~".+"} | detected_level=~"(?i)warn"'
    assert build_viewer_query(severity="errors").endswith('detected_level=~"(?i)(error|critical|fatal)"')


def test_exact_filters_do_not_inject_logql():
    host = 'CoreELEC",job=~".*'
    service = 'kodi" | json | line_format "oops'
    query = build_viewer_query(host=host, service=service, text='a" | drop x', regex='404|500')
    assert f'host={json.dumps(host)}' in query
    assert f'service_name={json.dumps(service)}' in query
    assert f'|= {json.dumps("a\" | drop x")}' in query
    assert '|~ "404|500"' in query
    assert query.count('{') == 1 and query.count('}') == 1


@pytest.mark.parametrize("kwargs", [
    {"severity":"ROOT"}, {"host":"a" * 130}, {"text":"b" * 257},
    {"regex":"["}, {"host_label":"host} | json"},
    {"program":"bad\nprogram"},
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
