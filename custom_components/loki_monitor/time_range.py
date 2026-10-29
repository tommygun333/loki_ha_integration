"""Validate exact UTC nanosecond windows supplied by the Lovelace frontend.

Browser datetime-local controls are converted to epoch nanoseconds on the
client. Never trust the client to enforce bounds; all API consumers reuse this.
"""

from __future__ import annotations

import time

MAX_RANGE_NS = 7 * 24 * 60 * 60 * 1_000_000_000
MIN_RANGE_NS = 60 * 1_000_000_000
MAX_FUTURE_NS = 60 * 1_000_000_000


class InvalidExactRange(ValueError):
    """Invalid or excessively large requested time interval."""


def validate_exact_range(start: str, end: str) -> tuple[int, int]:
    """Validate explicitly chosen UTC nanoseconds, up to seven days inclusive."""
    if (not isinstance(start, str) or not isinstance(end, str)
            or not start.isascii() or not end.isascii()
            or not start.isdecimal() or not end.isdecimal()
            or len(start) > 20 or len(end) > 20):
        raise InvalidExactRange("Start and end must be epoch nanosecond strings")
    a, b = int(start), int(end)
    if a < 1_000_000_000_000_000 or b <= a:
        raise InvalidExactRange("End must follow start")
    if not MIN_RANGE_NS <= b - a <= MAX_RANGE_NS:
        raise InvalidExactRange("Range must be between 1 minute and 7 days")
    if b > time.time_ns() + MAX_FUTURE_NS:
        raise InvalidExactRange("Range end is in the future")
    # datetime-local uses whole seconds; avoid silently rounding the user's bounds
    if a % 1_000_000_000 or b % 1_000_000_000:
        raise InvalidExactRange("Range must use whole-second precision")
    return a, b


def validate_cursor(cursor_ns: str | None, start: int, end: int) -> int:
    """A cursor must move backward inside the ORIGINAL time window."""
    if cursor_ns is None:
        return end
    if not isinstance(cursor_ns, str) or not cursor_ns.isascii() or not cursor_ns.isdecimal():
        raise InvalidExactRange("Invalid page cursor")
    cursor = int(cursor_ns)
    if not start <= cursor < end:
        raise InvalidExactRange("Page cursor is outside the selected range")
    return cursor
