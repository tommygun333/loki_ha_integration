"""Exercise options-flow navigation with lightweight Home Assistant stubs.

These tests catch the UnknownStep class of bug without a full HA installation.
"""

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


class FakeSelector:
    def __init__(self, config=None, **kwargs):
        self.config = config

    def __call__(self, value):
        return value


class FakeSelectorConfig:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs


class FakeOptionsFlow:
    def __init__(self):
        self.config_entry = SimpleNamespace(data={"url":"http://loki:3100"}, options={})
        self.hass = SimpleNamespace()

    def async_show_form(self, **kwargs):
        return {"type":"form", **kwargs}

    def async_show_menu(self, **kwargs):
        return {"type":"menu", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type":"create_entry", **kwargs}


class FakeConfigFlow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()


@pytest.fixture
def flow(monkeypatch):
    vol = ModuleType("voluptuous")
    vol.Required = lambda key, **kwargs: key
    vol.Optional = lambda key, **kwargs: key
    vol.Schema = lambda schema: schema
    vol.In = lambda options: options
    vol.All = lambda *args: args
    vol.Length = lambda **kwargs: kwargs
    vol.Range = lambda **kwargs: kwargs
    vol.Coerce = lambda typ: typ
    monkeypatch.setitem(sys.modules, "voluptuous", vol)
    ha = ModuleType("homeassistant")
    ha.__path__ = []
    entries = ModuleType("homeassistant.config_entries")
    entries.ConfigFlow = FakeConfigFlow
    entries.OptionsFlowWithReload = FakeOptionsFlow
    entries.ConfigEntry = type("ConfigEntry", (), {})
    entries.OptionsFlow = FakeOptionsFlow
    entries.ConfigFlowResult = dict
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    clients = ModuleType("homeassistant.helpers.aiohttp_client")
    clients.async_get_clientsession = lambda *args, **kwargs: None
    selector = ModuleType("homeassistant.helpers.selector")
    selector.TextSelector = FakeSelector
    selector.TextSelectorConfig = FakeSelectorConfig
    selector.TextSelectorType = SimpleNamespace(PASSWORD="password")
    selector.SelectSelector = FakeSelector
    selector.SelectSelectorConfig = FakeSelectorConfig
    selector.SelectSelectorMode = SimpleNamespace(DROPDOWN="dropdown")
    for name, mod in [
        ("homeassistant", ha), ("homeassistant.config_entries", entries),
        ("homeassistant.helpers", helpers),
        ("homeassistant.helpers.aiohttp_client", clients),
        ("homeassistant.helpers.selector", selector),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)
    ha.config_entries = entries
    helpers.selector = selector
    sys.modules.pop("loki_monitor.config_flow", None)
    module = importlib.import_module("loki_monitor.config_flow")

    class FakeClient:
        def __init__(self, *args):
            pass

        async def async_query(self, query):
            if query == "invalid":
                raise module.LokiQueryError("bad query")
            return {"resultType":"vector", "result":[{"value":[100,"42"]}]}

        async def async_query_streams_ns(self, query, *, start_ns, end_ns, limit):
            return {"resultType":"streams", "result":[{"stream":{"host":"CoreELEC"},
                "values":[[str(end_ns-i-1),f"log {i}"] for i in range(limit)]}]}

        async def async_label_values(self, label, since="1h"):
            return ["CoreELEC", "pve"] if label == "host" else ["kodi", "nginx"]

    monkeypatch.setattr(module, "LokiClient", FakeClient)
    yield module.LokiOptionsFlow()
    sys.modules.pop("loki_monitor.config_flow", None)


@pytest.mark.asyncio
async def test_menu_and_add_metric_route(flow):
    menu = await flow.async_step_init()
    assert "add_metric" in menu["menu_options"]
    form = await flow.async_step_add_metric()
    assert form["step_id"] == "add_metric"
    result = await flow.async_step_add_metric({"query_name":"Test", "query":"vector(42)"})
    assert result["type"] == "create_entry"
    assert result["data"]["custom_queries"][0]["name"] == "Test"


@pytest.mark.asyncio
async def test_metric_edit_retains_id_and_query_preview(flow):
    flow.config_entry.options = {
        "custom_queries": [{"id":"stable-id","name":"Old","query":"vector(42)"}]
    }
    selected = await flow.async_step_select_metric({"query_id":"stable-id"})
    assert selected["step_id"] == "edit_metric"
    result = await flow.async_step_edit_metric({"query_name":"New", "query":"vector(43)"})
    metric = result["data"]["custom_queries"][0]
    assert metric["id"] == "stable-id"
    assert metric["name"] == "New"
    preview = await flow.async_step_test_metric({"query":"vector(42)"})
    assert preview["step_id"] == "test_metric"
    assert preview["description_placeholders"]["result"] == "42"
    invalid = await flow.async_step_test_metric({"query":"invalid"})
    assert invalid["errors"]["base"] == "invalid_query"


@pytest.mark.asyncio
async def test_monitor_add_edit_remove_and_options_preservation(flow):
    flow.config_entry.options = {"poll_seconds": 300}
    form = await flow.async_step_add_monitor()
    assert form["step_id"] == "add_monitor"
    result = await flow.async_step_add_monitor({
        "monitor_name":"CoreELEC Kodi", "monitor_host":"CoreELEC", "monitor_service":"kodi"
    })
    assert result["type"] == "create_entry"
    flow.config_entry.options = result["data"]
    assert flow.config_entry.options["poll_seconds"] == 300
    monitor = flow.config_entry.options["monitors"][0]
    selected = await flow.async_step_select_monitor({"monitor_id":monitor["id"]})
    assert selected["step_id"] == "edit_monitor"
    edited = await flow.async_step_edit_monitor({
        "monitor_name":"Renamed", "monitor_host":"pve", "monitor_service":"nginx"
    })
    updated_monitor = edited["data"]["monitors"][0]
    assert updated_monitor["id"] == monitor["id"]
    assert updated_monitor["host"] == "pve"
    flow.config_entry.options = edited["data"]
    removed = await flow.async_step_remove_monitor({"monitor_id":monitor["id"]})
    assert removed["data"]["monitors"] == []

@pytest.mark.asyncio
async def test_alert_forms_add_edit_preview_remove_preserve_rules(flow):
    from loki_monitor.alerts import clean_rule
    menu = await flow.async_step_init()
    assert "add_alert" in menu["menu_options"]
    assert "test_alert" in menu["menu_options"]
    assert (await flow.async_step_add_alert())["step_id"] == "add_alert"
    base = {"name":"CoreELEC crash", "host":"CoreELEC", "severity":"errors",
            "threshold":3,"window_minutes":5,"cooldown_minutes":15}
    created = await flow.async_step_add_alert(base)
    assert created["type"] == "create_entry"
    rule = created["data"]["alert_rules"][0]
    assert rule["host"] == "CoreELEC"
    assert rule["threshold"] == 3
    flow.config_entry.options = created["data"]
    selected = await flow.async_step_select_alert({"rule_id":rule["id"]})
    assert selected["step_id"] == "edit_alert"
    edited = await flow.async_step_edit_alert({**base,"name":"Renamed","threshold":4})
    assert edited["data"]["alert_rules"][0]["id"] == rule["id"]
    assert edited["data"]["alert_rules"][0]["threshold"] == 4
    tested = await flow.async_step_test_alert(base)
    assert tested["step_id"] == "test_alert"
    assert tested["description_placeholders"]["count"] == "3"
    invalid = await flow.async_step_add_alert({**base,"regex":"["})
    assert invalid["errors"]["base"] == "invalid_alert"
    flow.config_entry.options = edited["data"]
    removed = await flow.async_step_remove_alert({"rule_id":rule["id"]})
    assert removed["data"]["alert_rules"] == []


@pytest.mark.asyncio
async def test_bulk_hosts_one_commit_preserves_existing(flow):
    flow.config_entry.options = {
        "poll_seconds": 300,
        "monitors": [{"id": "existing-id", "name": "Existing CoreELEC", "host": "CoreELEC", "service": ""}],
    }
    form = await flow.async_step_add_hosts()
    assert form["step_id"] == "add_hosts"
    result = await flow.async_step_add_hosts({"monitor_hosts": ["pve"]})
    assert result["type"] == "create_entry"
    monitors = result["data"]["monitors"]
    assert len(monitors) == 2
    assert monitors[0]["id"] == "existing-id"
    assert monitors[1]["host"] == "pve"
    assert monitors[1]["service"] == ""
    assert result["data"]["poll_seconds"] == 300

@pytest.mark.asyncio
async def test_bulk_hosts_rejects_unknown_and_duplicates(flow):
    invalid = await flow.async_step_add_hosts({"monitor_hosts": ["not-an-existing-host"]})
    assert invalid["errors"]["base"] == "invalid_monitor"
    valid = await flow.async_step_add_hosts({"monitor_hosts": ["CoreELEC", "pve"]})
    assert len(valid["data"]["monitors"]) == 2
    flow.config_entry.options = valid["data"]
    repeat = await flow.async_step_add_hosts({"monitor_hosts": ["pve"]})
    assert repeat["errors"]["base"] == "invalid_monitor"
