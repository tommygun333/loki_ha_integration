"""Bounded LogQL and pagination helpers for the admin-only Loki log viewer.

Pure functions: safe to test without importing Home Assistant.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .logic import flatten_streams, validate_label_name

MINUTE_NS = 60_000_000_000
MAX_WINDOW_MINUTES = 7 * 24 * 60
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_FIELD_LENGTH = 128
MAX_TEXT_LENGTH = 256
MAX_REGEX_LENGTH = 256
MAX_LOGQL_LENGTH = 2048

# Use the same severity interpretation as the existing Grafana dashboard.
SEVERITY_PATTERNS = {
    "all": None,
    "errors": "(?i)(error|critical|fatal)",
    "warnings": "(?i)warn",
    "info": "(?i)info",
    "debug": "(?i)debug",
    "trace": "(?i)trace",
    "unknown": "(?i)unknown",
}


class ViewerInputError(ValueError):
    """An invalid or unreasonably expensive viewer request."""


def _checked_text(value: str, max_length: int) -> str:
    if not isinstance(value, str) or len(value) > max_length or any(c in value for c in "\r\n\0"):
        raise ViewerInputError("Invalid filter value")
    return value.strip()


def build_viewer_query(
    *,
    host_label: str = "host",
    host: str = "",
    source: str = "",
    service: str = "",
    program: str = "",
    severity: str = "all",
    text: str = "",
    regex: str = "",
) -> str:
    """Build a log-stream query from fixed filter fields, not arbitrary LogQL."""
    try:
        host_label = validate_label_name(host_label)
    except ValueError as err:
        raise ViewerInputError("Invalid configured host label") from err
    if severity not in SEVERITY_PATTERNS:
        raise ViewerInputError("Unsupported severity filter")
    fields = {
        host_label: _checked_text(host, MAX_FIELD_LENGTH),
        "source": _checked_text(source, MAX_FIELD_LENGTH),
        "service_name": _checked_text(service, MAX_FIELD_LENGTH),
        "syslog_identifier": _checked_text(program, MAX_FIELD_LENGTH),
    }
    # No empty selector: the dashboard consistently requires a non-empty host.
    matchers = [f'{host_label}={json.dumps(fields[host_label])}' if fields[host_label]
                else f'{host_label}=~".+"']
    for key in ("source", "service_name", "syslog_identifier"):
        value = fields[key]
        if value:
            matchers.append(f'{key}={json.dumps(value)}')
    query = "{" + ",".join(matchers) + "}"
    if pattern := SEVERITY_PATTERNS[severity]:
        query += " | detected_level=~" + json.dumps(pattern)
    if text := _checked_text(text, MAX_TEXT_LENGTH):
        query += " |= " + json.dumps(text)
    if regex := _checked_text(regex, MAX_REGEX_LENGTH):
        try:
            re.compile(regex)
        except re.error as err:
            raise ViewerInputError("Invalid regular expression") from err
        query += " |~ " + json.dumps(regex)
    if len(query) > MAX_LOGQL_LENGTH:
        raise ViewerInputError("Viewer query is too long")
    return query


def get_time_bounds(
    *,
    minutes: int,
    end_ns: str | None = None,
    cursor_ns: str | None = None,
    now_ns: int | None = None,
) -> tuple[int, int, int]:
    """Return the fixed start/end and effective Loki end for pagination.

    The timestamp anchor remains stable when requesting older pages. Restrict
    historical reads to a recent 30-day window and windows to at most 7 days.
    """
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= MAX_WINDOW_MINUTES:
        raise ViewerInputError("Invalid time range")
    now_ns = time.time_ns() if now_ns is None else now_ns
    if end_ns is None:
        end = now_ns
    else:
        if not isinstance(end_ns, str) or not re.fullmatch(r"[0-9]{16,20}", end_ns):
            raise ViewerInputError("Invalid end timestamp")
        end = int(end_ns)
        if not now_ns - (30 * 24 * 60 * MINUTE_NS) <= end <= now_ns + MINUTE_NS:
            raise ViewerInputError("End timestamp outside allowed range")
    start = end - minutes * MINUTE_NS
    effective_end = end
    if cursor_ns is not None:
        if not isinstance(cursor_ns, str) or not re.fullmatch(r"[0-9]{16,20}", cursor_ns):
            raise ViewerInputError("Invalid page cursor")
        effective_end = int(cursor_ns)
        if not start <= effective_end < end:
            raise ViewerInputError("Page cursor outside query range")
    return start, end, effective_end


def page_streams(data: dict[str, Any], limit: int) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Return newest-first rows, older-page cursor and continuation flag."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ViewerInputError("Invalid page size")
    rows = flatten_streams(data, limit + 1)
    has_more = len(rows) > limit
    visible = rows[:limit]
    # Nanosecond exclusive cursor avoids duplicate rows across page boundaries.
    # Samples at an identical ns timestamp across streams may be skipped at the
    # boundary; Loki does not provide a stable per-row opaque cursor.
    cursor = str(int(visible[-1]["timestamp_ns"]) - 1) if has_more and visible else None
    return visible, cursor, has_more
