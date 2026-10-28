"""Static contract for bulk monitor flow; complements runtime options tests."""
import ast
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / 'custom_components' / 'loki_monitor'


def test_bulk_flow_keeps_single_reload_and_multiselect():
    tree = ast.parse((BASE / 'config_flow.py').read_text())
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LokiOptionsFlow')
    bulk = next(n for n in klass.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'async_step_add_hosts')
    calls = [n for n in ast.walk(bulk) if isinstance(n, ast.Call)]
    saves = [n for n in calls if isinstance(n.func, ast.Attribute) and n.func.attr == '_save']
    assert len(saves) == 1
    assert any(any(k.arg == 'multiple' and isinstance(k.value, ast.Constant) and k.value.value is True for k in n.keywords) for n in calls)
    assert 'MAX_MONITORS = 64' in (BASE / 'monitoring.py').read_text()
