"""Exercise the actual viewer WS handler paths with lightweight HA API stubs."""

from __future__ import annotations

import functools
import importlib
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest


class FakeConnection:
    def __init__(self, admin=True):
        self.user = SimpleNamespace(is_admin=admin)
        self.responses = []
        self.errors = []

    def send_result(self, id, result):
        self.responses.append((id, result))

    def send_error(self, id, code, message):
        self.errors.append((id, code, message))


class FakeLokiAPI:
    def __init__(self):
        self.requests = []
        self.label_requests = []

    async def async_label_values(self, name, since, *, query=None):
        self.label_requests.append((name, since, query))
        return {"host":["CoreELEC","pve"],"service_name":["kodi"],
                "source":["journal"],"syslog_identifier":["systemd"]}.get(name, [])

    async def async_query_streams_ns(self, query, **kwargs):
        self.requests.append((query,kwargs))
        base=kwargs["end_ns"]
        return {"resultType":"streams","result":[
            {"stream":{"host":"CoreELEC"},"values":[
                [str(base-100),"warn one"],
                [str(base-200),"warn two"],
                [str(base-300),"warn three"],
            ]}
        ]}


@pytest.fixture
def websocket_module(monkeypatch):
    vol=ModuleType("probatio")
    vol.Required=lambda key, **kw:key
    vol.Optional=lambda key, **kw:key
    vol.In=lambda values:values
    vol.Coerce=lambda typ:typ
    vol.Range=lambda **kw:kw
    vol.All=lambda *args:args
    vol.Any=lambda *args:args
    monkeypatch.setitem(sys.modules,"probatio",vol)
    ha = ModuleType("homeassistant")
    ha.__path__=[]
    components=ModuleType("homeassistant.components")
    components.__path__=[]
    ws=ModuleType("homeassistant.components.websocket_api")
    registry=[]

    def websocket_command(schema):
        def apply(func):
            func._ws_command=schema[vol.Required("type")]
            func._ws_schema=schema
            return func
        return apply

    def require_admin(func):
        @functools.wraps(func)
        def apply(hass, connection, msg):
            if not connection.user or not connection.user.is_admin:
                raise PermissionError("Administrator only")
            return func(hass, connection, msg)
        return apply

    ws.websocket_command=websocket_command
    ws.require_admin=require_admin
    ws.async_response=lambda x:x
    ws.ActiveConnection=FakeConnection
    ws.async_register_command=lambda hass, handler: registry.append(handler)
    components.websocket_api=ws
    core=ModuleType("homeassistant.core")
    core.HomeAssistant=type("HomeAssistant",(),{})
    core.callback=lambda f:f
    monkeypatch.setitem(sys.modules,"homeassistant",ha)
    monkeypatch.setitem(sys.modules,"homeassistant.components",components)
    monkeypatch.setitem(sys.modules,"homeassistant.components.websocket_api",ws)
    monkeypatch.setitem(sys.modules,"homeassistant.core",core)
    sys.modules.pop("loki_monitor.websocket",None)
    module=importlib.import_module("loki_monitor.websocket")
    module.async_register_websocket_commands(None)
    assert len(registry)==4
    yield module
    sys.modules.pop("loki_monitor.websocket",None)


def make_hass(n=1):
    apis=[FakeLokiAPI() for _ in range(n)]
    entries=[SimpleNamespace(entry_id=f"e{i}",title=f"Loki {i}",options={},
        runtime_data=SimpleNamespace(api=api)) for i,api in enumerate(apis)]
    hass=SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain:entries))
    return hass, apis


def test_viewer_sources_admin_only(websocket_module):
    hass,_=make_hass(2)
    admin=FakeConnection()
    websocket_module.websocket_sources(hass,admin,{"id":1})
    assert [x["entry_id"] for x in admin.responses[0][1]["sources"]]==["e0","e1"]
    with pytest.raises(PermissionError):
        websocket_module.websocket_sources(hass,FakeConnection(False),{"id":2})


@pytest.mark.asyncio
async def test_viewer_filters_and_query_one_page(websocket_module):
    hass,apis=make_hass()
    conn=FakeConnection()
    await websocket_module.websocket_filters(hass,conn,{"id":1})
    assert conn.responses[0][1]["filters"]["service"]==["kodi"]
    msg={"id":2,"entry_id":"e0","host":"CoreELEC","source":"","service":"kodi",
        "program":"","severity":"warnings","text":"","regex":"","minutes":60,"limit":2}
    await websocket_module.websocket_query_logs(hass,conn,msg)
    resp=conn.responses[1][1]
    assert resp["has_more"]
    assert resp["cursor_ns"]==str(int(resp["end_ns"])-201)
    assert len(resp["logs"])==2
    query,params=apis[0].requests[0]
    assert query == (
        '{host="CoreELEC",service_name="kodi",'
        'severity=~"(?i)^warning$"}'
    )
    assert " | label_format " not in query
    assert " | severity=" not in query
    assert params["limit"]==3
    msg["end_ns"]=resp["end_ns"]
    msg["cursor_ns"]=resp["cursor_ns"]
    await websocket_module.websocket_query_logs(hass,conn,msg)
    assert apis[0].requests[1][1]["end_ns"]==int(resp["cursor_ns"])


@pytest.mark.asyncio
async def test_viewer_denies_nonadmin_and_bad_entry(websocket_module):
    hass,apis=make_hass(2)
    base={"id":8,"host":"","source":"","service":"","program":"",
          "severity":"all","text":"","regex":"","minutes":60,"limit":50}
    with pytest.raises(PermissionError):
        await websocket_module.websocket_query_logs(hass,FakeConnection(False),base)
    conn=FakeConnection()
    await websocket_module.websocket_query_logs(hass,conn,base)
    assert conn.errors[0][1]=="source_not_found"
    await websocket_module.websocket_query_logs(hass,conn,{**base,"entry_id":"e0","regex":"["})
    assert conn.errors[-1][1]=="invalid_filters"
    assert not any(api.requests for api in apis)


@pytest.mark.asyncio
async def test_filtered_suggestions_use_contextual_loki_label_queries(websocket_module):
    hass, apis = make_hass()
    conn = FakeConnection()
    await websocket_module.websocket_filters(hass, conn, {
        "id": 10, "host": "CoreELEC", "source": "journal", "service": "kodi",
        "minutes": 360, "cascading": True,
    })
    requests = apis[0].label_requests
    assert requests == [
        ("host", "360m", '{host=~".+"}'),
        ("source", "360m", '{host="CoreELEC"}'),
        ("service_name", "360m", '{host="CoreELEC",source="journal"}'),
        ("syslog_identifier", "360m", '{host="CoreELEC",source="journal",service_name="kodi"}'),
    ]
    assert conn.responses[0][1]["filters"]["program"] == ["systemd"]


@pytest.mark.asyncio
async def test_filter_schema_invalid_label_is_rejected(websocket_module):
    hass, apis = make_hass()
    conn = FakeConnection()
    await websocket_module.websocket_filters(hass, conn, {"id": 1, "source_label": "host"})
    assert conn.errors[0][1] == "invalid_filters"
    assert not apis[0].label_requests


@pytest.mark.asyncio
async def test_multiselect_websocket_query_and_contextual_suggestions(websocket_module):
    hass, apis = make_hass()
    conn = FakeConnection()
    await websocket_module.websocket_filters(hass, conn, {
        "id": 31, "host": ["CoreELEC", "pve"], "source": ["journal", "docker"],
        "service": ["kodi", "nginx"], "minutes": 60, "cascading": True,
    })
    assert conn.errors == []
    reqs = apis[0].label_requests
    assert reqs[1][2] == '{host=~"^(?:CoreELEC|pve)$"}'
    assert reqs[2][2] == '{host=~"^(?:CoreELEC|pve)$",source=~"^(?:journal|docker)$"}'
    await websocket_module.websocket_query_logs(hass, conn, {
        "id": 32, "entry_id": "e0", "host": ["CoreELEC", "pve"],
        "source": ["journal", "docker"], "service": [], "program": [],
        "severity": "warnings", "text": "", "regex": "", "minutes": 60, "limit": 25,
    })
    assert conn.errors == []
    assert 'host=~"^(?:CoreELEC|pve)$"' in apis[0].requests[0][0]
    assert 'source=~"^(?:journal|docker)$"' in apis[0].requests[0][0]


@pytest.mark.asyncio
async def test_exact_range_pagination_and_prometheus_summary(websocket_module):
    hass, apis = make_hass()
    entry = hass.config_entries.async_entries("loki_monitor")[0]
    entry.options = {"monitors": [{"id":"host-a","name":"CoreELEC","host":"CoreELEC","service":""}]}
    class PrometheusStub:
        def __init__(self): self.calls = []
        async def async_counter_exact_range(self, start_ns, end_ns):
            self.calls.append((start_ns, end_ns))
            return {("CoreELEC","kodi","high"): 3.0,
                    ("CoreELEC","kodi","warning"): 2.0}
    stub = PrometheusStub()
    entry.runtime_data.coordinator=SimpleNamespace(prometheus=stub)
    start="1793199625000000000"
    end="1793202620000000000"
    conn=FakeConnection()
    await websocket_module.websocket_query_logs(hass, conn, {
      "id":20,"entry_id":"e0","host":"CoreELEC","source":"","service":"","program":"",
      "severity":"errors","text":"","regex":"","minutes":60,"limit":2,
      "range_start_ns":start,"range_end_ns":end,
    })
    assert conn.errors == []
    assert apis[0].requests[0][1]["start_ns"] == int(start)
    assert apis[0].requests[0][1]["end_ns"] == int(end)
    assert "severity=~" in apis[0].requests[0][0]
    await websocket_module.websocket_range_counts(hass,conn,{
      "id":21,"entry_id":"e0","range_start_ns":start,"range_end_ns":end,
    })
    assert stub.calls == [(start,end)]
    assert conn.responses[-1][1]["fleet"]["high"] == 3
    assert conn.responses[-1][1]["monitors"][0]["warning"] == 2
    await websocket_module.websocket_range_counts(hass,conn,{
      "id":22,"entry_id":"e0","range_start_ns":start,"range_end_ns":start,
    })
    assert conn.errors[-1][1] == "invalid_range"
