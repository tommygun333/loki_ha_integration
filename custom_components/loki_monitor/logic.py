"""Pure LogQL and response helpers, independent of Home Assistant."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from typing import Any

LABEL_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validate_label_name(value: str) -> str:
    """Check a Loki label name, before inserting it into LogQL/path."""
    if not LABEL_NAME_PATTERN.fullmatch(value):
        raise ValueError("Invalid Loki label name")
    return value


def level_selector(label: str, values: str) -> str:
    """Build a case-insensitive label matcher from a literal CSV list."""
    label = validate_label_name(label)
    parts = [part.strip() for part in values.split(",") if part.strip()]
    if not parts or len(parts) > 32 or any(len(part) > 64 for part in parts):
        raise ValueError("Provide 1–32 comma-separated severity values")
    pattern = "(?i)(" + "|".join(re.escape(part) for part in parts) + ")"
    return "{" + label + "=~" + json.dumps(pattern) + "}"


def detected_level_selector(host_label: str, severity: str) -> str:
    """Match Grafana dashboard v1.5 using Loki detected_level metadata.

    The dashboard's All filters constrain host to a nonempty value. Other
    selectors (source, service_name and syslog_identifier) match any value,
    including absent labels. Keep them for exact dashboard parity.
    """
    host_label = validate_label_name(host_label)
    patterns = {
        "error": "(?i)(error|critical|fatal)",
        "warn": "(?i)warn",
    }
    if severity not in patterns:
        raise ValueError("Unsupported detected severity")
    stream = (
        "{" + host_label + '=~".+",source=~".*",'
        'service_name=~".*",syslog_identifier=~".*"}'
    )
    return f'{stream} | detected_level=~{json.dumps(patterns[severity])}'


def count_query(selector: str, window: str) -> str:
    """Create a single instant-vector count with a zero fallback."""
    if window not in ("1h", "24h") and not (
        window.endswith("m") and window[:-1].isdigit() and 1 <= int(window[:-1]) <= 1440
    ):
        raise ValueError("Unsupported count window")
    return f"sum(count_over_time({selector}[{window}])) or vector(0)"


def extract_number(data: dict[str, Any], *, empty_is_zero: bool = False) -> float | None:
    """Extract a scalar or a single-series numeric instant result."""
    kind = data.get("resultType")
    result = data.get("result")
    if kind == "scalar":
        sample = result
    elif kind == "vector" and isinstance(result, list):
        if not result:
            return 0.0 if empty_is_zero else None
        if len(result) != 1:
            raise ValueError("LogQL returned multiple series; aggregate to one with sum()")
        sample = result[0].get("value")
    else:
        raise ValueError("LogQL query must return one metric value, not log streams")
    if not isinstance(sample, (list, tuple)) or len(sample) < 2:
        raise ValueError("Invalid Loki numeric sample")
    number = float(sample[1])
    if not math.isfinite(number):
        return None
    return number


def extract_latest_timestamp(data: dict[str, Any]) -> datetime | None:
    """Read the newest timestamp in a Loki streams response."""
    if data.get("resultType") != "streams":
        raise ValueError("Expected log streams")
    newest = max(
        (int(sample[0]) for stream in data.get("result", []) for sample in stream.get("values", [])),
        default=None,
    )
    return datetime.fromtimestamp(newest / 1_000_000_000, tz=UTC) if newest else None


def flatten_streams(data: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Normalize Loki streams to newest-first bounded JSON-friendly rows."""
    if data.get("resultType") != "streams":
        raise ValueError("LogQL query must return log streams")
    rows = []
    for stream in data.get("result", []):
        labels = stream.get("stream", {})
        for sample in stream.get("values", []):
            if len(sample) < 2:
                continue
            rows.append(
                {
                    "timestamp_ns": str(sample[0]),
                    "labels": labels,
                    "line": str(sample[1])[:8192],
                    **({"metadata": sample[2]} if len(sample) > 2 and isinstance(sample[2], dict) else {}),
                }
            )
    rows.sort(key=lambda item: int(item["timestamp_ns"]), reverse=True)
    return rows[:limit]
