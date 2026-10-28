"""Structural guardrails for no hidden LogQL severity scans or button pollution."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'custom_components'/'loki_monitor'

def test_no_scheduled_loki_queries_in_default_coordinator():
    src=(ROOT/'coordinator.py').read_text()
    assert 'count_over_time(' not in src
    assert 'async_recent_logs' not in src
    assert 'async_labels()' not in src
    assert 'async_counter_window' in src
    assert 'poll_custom_queries' in src

def test_alerts_use_bounded_streams_only():
    src=(ROOT/'alerts.py').read_text()
    assert 'count_over_time(' not in src
    assert 'async_query_streams_ns' in src

def test_reusable_cards_have_no_poll_timers():
    src=(ROOT/'frontend'/'loki-monitor-cards.js').read_text()
    assert 'loki-log-button-card' in src
    assert 'loki-monitor-overview-card' in src
    assert 'showModal()' in src
    assert 'fire-dom-event' in src
    assert 'setInterval(' not in src

def test_viewer_does_not_regenerate_severity():
    src=(ROOT/'viewer.py').read_text()
    assert 'CANONICAL_SEVERITY_TEMPLATE' not in src
    assert 'label_format severity' not in src
    assert 'severity=~' in src
