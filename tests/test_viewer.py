"""Indexed Alloy severity and safe viewer-query construction."""
import pytest
from loki_monitor.viewer import build_viewer_query, build_filter_scopes, ViewerInputError, get_time_bounds


def test_indexed_alloy_high_and_warning_matchers():
    high = build_viewer_query(host='OPNsense.internal',severity='errors')
    assert 'severity=~"(?i)^(emergency|alert|critical|fatal|error)$"' in high
    assert 'label_format' not in high and 'detected_level' not in high
    assert high.startswith('{host="OPNsense.internal",')
    assert 'severity=~"(?i)^warning$"' in build_viewer_query(severity='warnings')
    assert 'severity=~"(?i)^(notice|info|debug|trace)$"' in build_viewer_query(severity='normal')


def test_all_host_selector_always_non_empty():
    q=build_viewer_query()
    assert q=='{host=~".+"}'


def test_exact_multi_host_selection_does_not_inject():
    q=build_viewer_query(host=['pve','foo",severity="debug'],service='kodi',severity='error')
    assert 'host=~' in q and 'service_name="kodi"' in q
    assert 'severity=~"(?i)^error$"' in q
    assert 'label_format' not in q


def test_indexed_severity_only_no_legacy_mode():
    with pytest.raises(ViewerInputError):
        build_viewer_query(severity_mode='canonical')
    with pytest.raises(ViewerInputError):
        build_viewer_query(severity_mode='detected')


def test_text_and_regex_are_bounded():
    q=build_viewer_query(text='failed',regex='(error|warn)')
    assert ' |= "failed"' in q
    assert ' |~ "(error|warn)"' in q
    with pytest.raises(ViewerInputError):build_viewer_query(regex='[')
    with pytest.raises(ViewerInputError):build_viewer_query(text='a'*300)


def test_filter_scopes_is_available():
    scopes=build_filter_scopes(host='CoreELEC',source='docker')
    assert set(scopes)=={'host','source','service','program'}
