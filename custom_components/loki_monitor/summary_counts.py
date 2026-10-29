"""Pure Prometheus severity aggregation shared by scheduled and ad-hoc views."""

SEVERITY_GROUPS = ('high', 'warning', 'normal', 'unknown')


def summarize(series: dict[tuple[str, str, str], float], *, host: str = '', service: str = '') -> dict[str, int]:
    """Produce one compact severity summary from grouped Alloy counter series.

    Missing series inside a successful Prometheus response count as zero.
    """
    result: dict[str, int] = {}
    for level in SEVERITY_GROUPS:
        total = sum(v for (h, s, group), v in series.items()
                    if group == level and (not host or h == host) and (not service or s == service))
        result[level] = max(0, round(total))
    result['total'] = sum(result.values())
    return result
