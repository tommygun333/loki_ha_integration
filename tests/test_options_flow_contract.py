"""Check UI menu and form step IDs without requiring a running HA instance."""

import ast
from pathlib import Path


FLOW_FILE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "loki_monitor"
    / "config_flow.py"
)


def test_options_flow_form_step_ids_match_their_handlers():
    """A mismatched step_id raises Home Assistant data_entry_flow.UnknownStep."""
    tree = ast.parse(FLOW_FILE.read_text(encoding="utf-8"))
    options_flow = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LokiOptionsFlow"
    )
    handlers = {
        node.name.removeprefix("async_step_"): node
        for node in options_flow.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name.startswith("async_step_")
    }

    assert {"init", "settings", "add_metric", "edit_metric", "test_metric", "add_monitor", "edit_monitor", "remove_query", "remove_monitor"} <= handlers.keys()

    for step_name, handler in handlers.items():
        for node in ast.walk(handler):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("async_show_form", "async_show_menu")
            ):
                continue
            step_id = next((kw.value for kw in node.keywords if kw.arg == "step_id"), None)
            assert isinstance(step_id, ast.Constant) and isinstance(step_id.value, str)
            assert step_id.value == step_name, (
                f"{handler.name} returns step_id={step_id.value!r}; "
                f"expected {step_name!r}"
            )


def test_options_menu_targets_have_handlers():
    tree = ast.parse(FLOW_FILE.read_text(encoding="utf-8"))
    options_flow = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LokiOptionsFlow"
    )
    handlers = {
        node.name.removeprefix("async_step_")
        for node in options_flow.body
        if isinstance(node, ast.AsyncFunctionDef)
    }
    init = next(
        node for node in options_flow.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_step_init"
    )
    # Inspect both the initial literal menu and any dynamically appended choices.
    targets = set()
    for node in ast.walk(init):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "choices" for t in node.targets
        ) and isinstance(node.value, ast.List):
            targets.update(item.value for item in node.value.elts if isinstance(item, ast.Constant))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append":
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "choices":
                targets.update(a.value for a in node.args if isinstance(a, ast.Constant))
    assert {"settings", "add_metric", "test_metric", "select_metric", "remove_query", "add_monitor", "select_monitor", "remove_monitor"} <= targets
    assert targets <= handlers
