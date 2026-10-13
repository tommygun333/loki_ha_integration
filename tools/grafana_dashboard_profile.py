"""Extract a Loki viewer card profile and detect severity drift from a Grafana export.

No Grafana credentials or runtime connectivity needed. Does not import dashboards
into Home Assistant and does not execute any Grafana-provided expressions.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWER_PY = ROOT / "custom_components/loki_monitor/viewer.py"


def log_panel_query(dashboard: dict) -> str:
    for panel in dashboard.get("panels", []):
        if panel.get("type") == "logs":
            for target in panel.get("targets", []):
                if expr := target.get("expr", ""):
                    return expr
    raise ValueError("Grafana export has no Logs panel with a LogQL query")


def severity_template(query: str) -> str | None:
    match = re.search(r"\| label_format severity=`(.*?)`(?:\s+\||\s*$)", query, re.S)
    return match.group(1) if match else None


def current_template() -> str:
    tree = ast.parse(VIEWER_PY.read_text(encoding="utf-8"))
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CANONICAL_SEVERITY_TEMPLATE"
            for t in stmt.targets
        ):
            return ast.literal_eval(stmt.value)
    raise ValueError("Current viewer lacks a canonical severity template")


def minutes_from_range(value: str) -> int:
    match = re.fullmatch(r"now-(\d+)(m|h|d)", str(value))
    if not match:
        return 60
    num = int(match.group(1)) * {"m": 1, "h": 60, "d": 1440}[match.group(2)]
    return num if 1 <= num <= 10080 else 60


def profile(dashboard: dict, grafana_url: str = "") -> tuple[str, bool]:
    variables = {
        v.get("name"): v for v in dashboard.get("templating", {}).get("list", [])
    }
    label_map = {}
    for key, default in (("source", "source"), ("service", "service_name"),
                         ("program", "syslog_identifier")):
        query = variables.get(key, {}).get("query", {})
        label = query.get("label", default) if isinstance(query, dict) else default
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", label):
            raise ValueError(f"Invalid indexed label in dashboard for {key}")
        label_map[key] = label
    source_expr = log_panel_query(dashboard)
    template = severity_template(source_expr)
    matches = template is not None and template == current_template()
    lines = [
        f'# Grafana dashboard: {dashboard.get("title", "unnamed")}, version {dashboard.get("version", "unknown")}',
        '# Generated from Grafana JSON; paste into a Home Assistant Manual card.',
        'type: custom:loki-log-viewer-card',
        f'minutes: {minutes_from_range(dashboard.get("time", {}).get("from", "now-1h"))}',
        'page_size: 50',
        'severity: all',
        f'severity_mode: {"canonical" if template is not None else "detected"}',
        'cascading_filters: true',
        'hide_ip_hosts: true',
        'hidden_hosts: []',
        'label_map:',
        *[f'  {key}: {value}' for key,value in label_map.items()],
    ]
    if grafana_url:
        lines.append(f'grafana_url: {json.dumps(grafana_url)}')
    return "\n".join(lines) + "\n", matches


def sync_template(template: str) -> None:
    if len(template) > 5000:
        raise ValueError("Grafana severity template is unreasonably long")
    code = VIEWER_PY.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"(?m)^CANONICAL_SEVERITY_TEMPLATE = .*?$",
        lambda _: "CANONICAL_SEVERITY_TEMPLATE = " + repr(template),
        code,
        count=1,
    )
    if count != 1:
        raise ValueError("Couldn't find canonical severity assignment")
    ast.parse(updated)
    VIEWER_PY.write_text(updated, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dashboard", type=Path, help="Grafana dashboard export JSON")
    parser.add_argument("--grafana-url", default="", help="Optional Grafana base URL")
    parser.add_argument("--sync-severity", action="store_true", help="Update the packaged severity template; requires a new integration release")
    args = parser.parse_args()
    dashboard = json.loads(args.dashboard.read_text(encoding="utf-8"))
    expression = log_panel_query(dashboard)
    template = severity_template(expression)
    if args.sync_severity:
        if template is None:
            parser.error("No canonical severity template found in Logs panel")
        sync_template(template)
    result, matches = profile(dashboard, args.grafana_url)
    print(result)
    if template is not None:
        print("# Severity template matches this release" if matches else
              "# IMPORTANT: Grafana severity template changed; use --sync-severity and publish a new HACS release")
    else:
        print("# NOTE: No canonical severity template in dashboard; legacy detected severity selected")


if __name__ == "__main__":
    main()
