"""Prevent Home Assistant's frontend ICU translation rendering from breaking."""

import json
from pathlib import Path


COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "loki_monitor"


def _strings(obj, path=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _strings(value, f"{path}.{key}" if path else key)
    elif isinstance(obj, str):
        yield path, obj


def test_custom_integration_translations_have_no_unescaped_curly_braces():
    """Raw LogQL selectors in UI text would be parsed as ICU placeholders."""
    for fname in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT_DIR / fname).read_text())
        for key, value in _strings(data):
            # Home Assistant supports valid ICU placeholders; only the explicit
            # preview result placeholder is allowed, never a raw LogQL selector.
            if key == "options.step.test_metric.description":
                assert value.count("{result}") == 1
                value = value.replace("{result}", "")
            assert "{" not in value and "}" not in value, f"{fname}: {key}"


def test_translations_agree_for_numeric_query_form():
    source = json.loads((COMPONENT_DIR / "strings.json").read_text())
    active = json.loads((COMPONENT_DIR / "translations/en.json").read_text())
    assert source["options"]["step"]["add_metric"] == active["options"]["step"]["add_metric"]


def test_options_severity_mode_translation_present_and_consistent():
    source = json.loads((COMPONENT_DIR / "strings.json").read_text())
    active = json.loads((COMPONENT_DIR / "translations/en.json").read_text())
    assert source["options"]["step"]["settings"] == active["options"]["step"]["settings"]
    assert "severity_source" in active["options"]["step"]["settings"]["data"]


def test_numeric_query_form_uses_fresh_translation_key():
    """Do not reuse old add_query key cached with a malformed ICU description."""
    source = json.loads((COMPONENT_DIR / "strings.json").read_text())
    active = json.loads((COMPONENT_DIR / "translations/en.json").read_text())
    flow = (COMPONENT_DIR / "config_flow.py").read_text()
    for data in (source, active):
        assert "add_metric" in data["options"]["step"]
        assert "add_query" not in data["options"]["step"]
        assert "add_metric" in data["options"]["step"]["init"]["menu_options"]
        assert "add_query" not in data["options"]["step"]["init"]["menu_options"]
    assert "async def async_step_add_metric(" in flow
    assert "async def async_step_add_query(" not in flow
