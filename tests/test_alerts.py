"""Indexed, bounded and checkpointed Loki alert evaluation."""
from __future__ import annotations
import pytest
from loki_monitor.alerts import clean_rule, make_rule_query, test_alert_rule as preview, LokiAlertEngine


def test_rule_uses_indexed_severity_and_caps_threshold():
    rule=clean_rule({'name':'Kodi errors','host':'CoreELEC','threshold':3})
    q=make_rule_query(rule)
    assert 'severity=~' in q and 'label_format' not in q
    with pytest.raises(ValueError):clean_rule({'name':'too many','threshold':101})

class FakeAPI:
    def __init__(self,count):self.count=count;self.calls=[]
    async def async_query_streams_ns(self,query,*,start_ns,end_ns,limit):
        self.calls.append((query,limit))
        values=[[str(end_ns-i-1),f'error {i}'] for i in range(min(limit,self.count))]
        return {'resultType':'streams','result':[{'stream':{'host':'CoreELEC'},'values':values}]}

@pytest.mark.asyncio
async def test_preview_only_reads_threshold_entries():
    api=FakeAPI(42)
    rule=clean_rule({'name':'Kodi','threshold':3})
    value=await preview(api,rule)
    assert value==3
    assert api.calls[0][1]==3

@pytest.mark.asyncio
async def test_check_single_bounded_query_without_metric_scans():
    class Store:
        async def async_load(self):return None
        async def async_save(self,data):pass
    class Bus:
        def __init__(self):self.events=[]
        def async_fire(self,*args):self.events.append(args)
    class Hass:
        def __init__(self):self.bus=Bus()
    api=FakeAPI(6);hass=Hass()
    eng=LokiAlertEngine(hass,api,'abc',Store())
    rule={'id':'rule',**clean_rule({'name':'Kodi','threshold':3})}
    await eng.async_initialize([rule]);await eng.async_check([rule])
    assert len(api.calls)==1 and api.calls[0][1]==3
    assert len(hass.bus.events)==1
