"""Validate repository metadata and HA config schema conventions."""

import ast
import json
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "loki_monitor"


def test_manifest_key_order():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_config_entry_only_schema_defined():
    tree = ast.parse((COMPONENT / "__init__.py").read_text())
    schemas = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "CONFIG_SCHEMA" for t in node.targets)
    ]
    assert len(schemas) == 1
    schema = schemas[0].value
    assert isinstance(schema, ast.Call)
    assert ast.unparse(schema.func) == "cv.config_entry_only_config_schema"
    assert [ast.unparse(arg) for arg in schema.args] == ["DOMAIN"]
