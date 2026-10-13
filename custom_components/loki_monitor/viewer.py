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
MAX_FILTER_SELECTIONS = 24
MAX_TEXT_LENGTH = 256
MAX_REGEX_LENGTH = 256
MAX_LOGQL_LENGTH = 8192

# Imported verbatim from the Homelab Logs / Loki v1.9 Logs panel.
# Indexed level takes priority, then detected_level, then line markers.
# This preserves [emerg]/[fatal] classification as seen in Grafana.
CANONICAL_SEVERITY_TEMPLATE = '{{ $line := __line__ | lower }}{{ $idxlvl := .level | lower }}{{ $dlev := .detected_level | lower }}{{ if and $idxlvl (ne $idxlvl "unknown") }}{{ if or (eq $idxlvl "emerg") (eq $idxlvl "emergency") (eq $idxlvl "0") }}emergency{{ else if or (eq $idxlvl "alert") (eq $idxlvl "1") }}alert{{ else if or (eq $idxlvl "crit") (eq $idxlvl "critical") (eq $idxlvl "2") }}critical{{ else if eq $idxlvl "fatal" }}fatal{{ else if or (eq $idxlvl "err") (eq $idxlvl "eror") (eq $idxlvl "error") (eq $idxlvl "3") }}error{{ else if or (eq $idxlvl "warn") (eq $idxlvl "warning") (eq $idxlvl "4") }}warning{{ else if or (eq $idxlvl "notice") (eq $idxlvl "5") }}notice{{ else if or (eq $idxlvl "info") (eq $idxlvl "information") (eq $idxlvl "informational") (eq $idxlvl "6") }}info{{ else if or (eq $idxlvl "debug") (eq $idxlvl "dbug") (eq $idxlvl "7") }}debug{{ else if eq $idxlvl "trace" }}trace{{ else }}{{ $idxlvl }}{{ end }}{{ else if and $dlev (ne $dlev "unknown") }}{{ if or (eq $dlev "emerg") (eq $dlev "emergency") (eq $dlev "0") }}emergency{{ else if or (eq $dlev "alert") (eq $dlev "1") }}alert{{ else if or (eq $dlev "crit") (eq $dlev "critical") (eq $dlev "2") }}critical{{ else if eq $dlev "fatal" }}fatal{{ else if or (eq $dlev "err") (eq $dlev "eror") (eq $dlev "error") (eq $dlev "3") }}error{{ else if or (eq $dlev "warn") (eq $dlev "warning") (eq $dlev "4") }}warning{{ else if or (eq $dlev "notice") (eq $dlev "5") }}notice{{ else if or (eq $dlev "info") (eq $dlev "information") (eq $dlev "informational") (eq $dlev "6") }}info{{ else if or (eq $dlev "debug") (eq $dlev "dbug") (eq $dlev "7") }}debug{{ else if eq $dlev "trace" }}trace{{ else }}{{ $dlev }}{{ end }}{{ else }}{{ if contains "[emerg]" $line }}emergency{{ else if contains "[emergency]" $line }}emergency{{ else if contains "[alert]" $line }}alert{{ else if contains "[critical]" $line }}critical{{ else if contains "[crit]" $line }}critical{{ else if contains "[fatal]" $line }}fatal{{ else if contains "[error]" $line }}error{{ else if contains "[err]" $line }}error{{ else if contains "[warning]" $line }}warning{{ else if contains "[warn]" $line }}warning{{ else if contains "[notice]" $line }}notice{{ else if contains "[info]" $line }}info{{ else if contains "[debug]" $line }}debug{{ else if contains "[trace]" $line }}trace{{ else if contains " fatal <" $line }}fatal{{ else if contains " error <" $line }}error{{ else if contains " warning <" $line }}warning{{ else if contains " warn <" $line }}warning{{ else if contains " notice <" $line }}notice{{ else if contains " info <" $line }}info{{ else if contains " debug <" $line }}debug{{ else }}unknown{{ end }}{{ end }}'

SEVERITY_PATTERNS = {
    "all": None,
    "errors": "(?i)^(emergency|alert|critical|fatal|error)$",
    "warnings": "(?i)^warning$",
    "emergency": "(?i)^emergency$",
    "alert": "(?i)^alert$",
    "critical": "(?i)^critical$",
    "fatal": "(?i)^fatal$",
    "error": "(?i)^error$",
    "warning": "(?i)^warning$",
    "notice": "(?i)^notice$",
    "info": "(?i)^info$",
    "debug": "(?i)^debug$",
    "trace": "(?i)^trace$",
    "unknown": "(?i)^unknown$",
}
SEVERITY_MODES = ("canonical", "detected")
DEFAULT_LABEL_MAP = {"source": "source", "service": "service_name", "program": "syslog_identifier"}
FILTER_KEYS = ("host", "source", "service", "program")


class ViewerInputError(ValueError):
    """An invalid or unreasonably expensive viewer request."""


def _checked_text(value: str, max_length: int) -> str:
    if not isinstance(value, str) or len(value) > max_length or any(c in value for c in "\r\n\0"):
        raise ViewerInputError("Invalid filter value")
    return value.strip()


def normalize_filter_values(value: str | list[str]) -> list[str]:
    """Accept old scalar filters and new multi-select arrays without coercion.

    Every selection is an exact Loki label value; no regex syntax is accepted
    from the caller. The total number of selected values is bounded.
    """
    if isinstance(value, str):
        value = [value] if value.strip() else []
    if not isinstance(value, list) or len(value) > MAX_FILTER_SELECTIONS:
        raise ViewerInputError("Invalid number of filter selections")
    results = []
    for item in value:
        checked = _checked_text(item, MAX_FIELD_LENGTH)
        if not checked:
            raise ViewerInputError("Empty filter selection")
        if checked not in results:
            results.append(checked)
    return results


def label_matcher(label: str, selection: list[str], *, require_host: bool = False) -> str | None:
    """Produce exact equality for one value, escaped anchored regex for many."""
    if not selection:
        return f'{label}=~".+"' if require_host else None
    if len(selection) == 1:
        return f'{label}={json.dumps(selection[0])}'
    # Loki RE2 label regexes are fully anchored; anchor explicitly for clarity.
    pattern = "^(?:" + "|".join(re.escape(item) for item in selection) + ")$"
    return f"{label}=~{json.dumps(pattern)}"


def build_viewer_query(
    *,
    host_label: str = "host",
    host: str | list[str] = "",
    source: str | list[str] = "",
    service: str | list[str] = "",
    program: str | list[str] = "",
    severity: str = "all",
    text: str = "",
    regex: str = "",
    severity_mode: str = "canonical",
    source_label: str = "source",
    service_label: str = "service_name",
    program_label: str = "syslog_identifier",
) -> str:
    """Build a log-stream query from fixed filter fields, not arbitrary LogQL."""
    try:
        host_label = validate_label_name(host_label)
    except ValueError as err:
        raise ViewerInputError("Invalid configured host label") from err
    if severity not in SEVERITY_PATTERNS:
        raise ViewerInputError("Unsupported severity filter")
    if severity_mode not in SEVERITY_MODES:
        raise ViewerInputError("Unsupported severity mode")
    try:
        source_label, service_label, program_label = (
            validate_label_name(label) for label in (source_label, service_label, program_label)
        )
    except ValueError as err:
        raise ViewerInputError("Invalid configured label name") from err
    if len({host_label, source_label, service_label, program_label}) != 4:
        raise ViewerInputError("Filter labels must be distinct")
    fields = {
        host_label: normalize_filter_values(host),
        source_label: normalize_filter_values(source),
        service_label: normalize_filter_values(service),
        program_label: normalize_filter_values(program),
    }
    # No empty selector: the dashboard consistently requires a non-empty host.
    matchers = [label_matcher(host_label, fields[host_label], require_host=True)]
    for key in (source_label, service_label, program_label):
        if matcher := label_matcher(key, fields[key]):
            matchers.append(matcher)
    query = "{" + ",".join(matchers) + "}"
    if severity_mode == "canonical":
        query += " | label_format severity=`" + CANONICAL_SEVERITY_TEMPLATE + "`"
        if pattern := SEVERITY_PATTERNS[severity]:
            query += " | severity=~" + json.dumps(pattern)
    elif pattern := SEVERITY_PATTERNS[severity]:
        # Backward-compatible, less precise indexed/detected classification.
        if severity == "warnings":
            pattern = "(?i)warn"
        elif severity == "errors":
            pattern = "(?i)(error|critical|fatal)"
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


def build_filter_scopes(
    *,
    host_label: str = "host",
    host: str | list[str] = "",
    source: str | list[str] = "",
    service: str | list[str] = "",
    program: str | list[str] = "",
    source_label: str = "source",
    service_label: str = "service_name",
    program_label: str = "syslog_identifier",
    cascading: bool = True,
) -> dict[str, str]:
    """Grafana-style context: Host -> Source -> Service -> Program.

    The returned selectors are passed to Loki's label values API using its
    supported `query` argument, never an unscoped global value list.
    """
    if not isinstance(cascading, bool):
        raise ViewerInputError("Invalid cascade setting")
    try:
        labels = [validate_label_name(item) for item in
                  (host_label, source_label, service_label, program_label)]
    except ValueError as err:
        raise ViewerInputError("Invalid configured label name") from err
    if len(set(labels)) != 4:
        raise ViewerInputError("Filter labels must be distinct")
    selections = [normalize_filter_values(item) for item in
                  (host, source, service, program)]
    scopes = {}
    for index, key in enumerate(FILTER_KEYS):
        prior = list(zip(labels[:index], selections[:index], strict=True)) if cascading else []
        matches = [label_matcher(labels[0], [], require_host=True)]
        for label, selection in prior:
            matcher = label_matcher(label, selection)
            if matcher:
                if label == labels[0]:
                    matches[0] = matcher
                else:
                    matches.append(matcher)
        scopes[key] = "{" + ",".join(matches) + "}"
        if len(scopes[key]) > MAX_LOGQL_LENGTH:
            raise ViewerInputError("Filter scope is too long")
    return scopes


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
