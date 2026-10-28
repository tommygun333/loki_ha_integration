"""Scope and input sanitization after removing legacy severity reconstruction."""
import pytest
from loki_monitor.monitoring import clean_monitor,monitor_selector,MAX_MONITORS

def test_clean_valid_monitor():
    assert clean_monitor(' Kodi ',' CoreELEC ',' kodi ')=={'name':'Kodi','host':'CoreELEC','service':'kodi'}

def test_malformed_values_rejected():
    for args in [('bad\n','pve',''),('ok','',''),('x'*129,'pve','')]:
        with pytest.raises(ValueError):clean_monitor(*args)

def test_direct_indexed_label_scope():
    q=monitor_selector({'host':'CoreELEC','service':'kodi'},severity='error')
    assert q=='{host="CoreELEC",service_name="kodi",severity=~"^(emergency|alert|critical|fatal|error)$"}'
    assert monitor_selector({'host':'pve','service':''},severity='warn')=='{host="pve",severity="warning"}'

def test_host_escaping_and_limit():
    q=monitor_selector({'host':'foo",severity="info','service':''})
    assert 'host="foo\\",severity=\\"info"' in q
    assert MAX_MONITORS>=64
