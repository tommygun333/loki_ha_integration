"""Pure, validated LogQL builders for user-defined host/service monitors."""

from __future__ import annotations

import json

from .logic import level_selector, validate_label_name
from .const import SEVERITY_SOURCE_DETECTED, SEVERITY_SOURCE_INDEXED

MAX_MONITORS = 12
CONF_MONITORS = "monitors"
CONF_MONITOR_ID = "monitor_id"
CONF_MONITOR_NAME = "monitor_name"
CONF_MONITOR_HOST = "monitor_host"
CONF_MONITOR_SERVICE = "monitor_service"
SERVICE_LABEL = "service_name"


def clean_monitor(name: str, host: str, service: str = "") -> dict[str, str]:
    """Sanitize user input without damaging legitimate Loki label values."""
    name, host, service = name.strip(), host.strip(), service.strip()
    if not name or not host or any(len(value) > 128 for value in (name, host, service)):
        raise ValueError("Monitor name and host are required and must be at most 128 characters")
    if any("\n" in value or "\r" in value for value in (name, host, service)):
        raise ValueError("Monitor values must not contain newlines")
    return {"name": name, "host": host, "service": service}


def monitor_selector(
    monitor: dict[str, str],
    *,
    host_label: str,
    severity_source: str,
    level_label: str,
    severity_values: str,
    severity: str,
) -> str:
    """Scope counts to exactly one host and optional service, with safe quoting."""
    host_label = validate_label_name(host_label)
    host = monitor["host"]
    service = monitor.get("service", "")
    stream = [f"{host_label}={json.dumps(host)}"]
    if service:
        stream.append(f"{SERVICE_LABEL}={json.dumps(service)}")
    if severity_source == SEVERITY_SOURCE_DETECTED:
        # Preserve Loki detected_level classification used in the dashboard.
        expression = {"error": "(?i)(error|critical|fatal)", "warn": "(?i)warn"}[severity]
        return "{" + ",".join(stream) + "} | detected_level=~" + json.dumps(expression)
    if severity_source == SEVERITY_SOURCE_INDEXED:
        # Reuse existing validated literal CSV severity patterns.
        level = level_selector(level_label, severity_values)
        return "{" + ",".join(stream) + "," + level[1:-1] + "}"
    raise ValueError("Unknown severity source")
