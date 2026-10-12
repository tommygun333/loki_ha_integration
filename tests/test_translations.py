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
            assert "{" not in value and "}" not in value, f"{fname}: {key}"


def test_translations_agree_for_numeric_query_form():
    source = json.loads((COMPONENT_DIR / "strings.json").read_text())
    active = json.loads((COMPONENT_DIR / "translations/en.json").read_text())
    assert source["options"]["step"]["add_query"] == active["options"]["step"]["add_query"]


def test_options_severity_mode_translation_present_and_consistent():
    source = json.loads((COMPONENT_DIR / "strings.json").read_text())
    active = json.loads((COMPONENT_DIR / "translations/en.json").read_text())
    assert source["options"]["step"]["settings"] == active["options"]["step"]["settings"]
    assert "severity_source" in active["options"]["step"]["settings"]["data"]
