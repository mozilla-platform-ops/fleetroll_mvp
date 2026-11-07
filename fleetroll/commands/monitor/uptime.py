"""Compact uptime presentation; bands show magnitude, not host health."""

UPTIME_THRESHOLDS = (3600, 6 * 3600, 86400, 7 * 86400)
UPTIME_BARS = ("▎░░", "█░░", "█▌░", "██░", "███")
UPTIME_COLOR_PAIR_START = 100
UPTIME_BLUE_SHADES = (25, 32, 39, 75, 117)


def uptime_band(seconds_value: int) -> int:
    """Return a fixed band, independent of the visible host population."""
    return sum(seconds_value >= threshold for threshold in UPTIME_THRESHOLDS)


def format_uptime(seconds_value: int | None) -> str:
    """Format a three-character duration and three-cell bar in seven columns.

    Use whole units, switching from days to weeks at 100 days and from weeks
    to years at 100 weeks. Labels saturate at 99 years; the bar saturates at
    seven days. Missing observations have neither a bar nor a magnitude.
    """
    if seconds_value is None:
        return "  -    "
    seconds_value = max(seconds_value, 0)
    for unit, size, limit in (
        ("s", 1, 60),
        ("m", 60, 60),
        ("h", 3600, 24),
        ("d", 86400, 100),
        ("w", 7 * 86400, 100),
        ("y", 365 * 86400, None),
    ):
        count = seconds_value // size
        if limit is None or count < limit:
            label = f"{min(count, 99)}{unit}"
            return f"{label:>3} {UPTIME_BARS[uptime_band(seconds_value)]}"
    raise AssertionError("year unit always matches")
