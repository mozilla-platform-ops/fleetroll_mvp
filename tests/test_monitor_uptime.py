"""Compact uptime labels, fixed magnitude bands, and table layout."""

import pytest
from fleetroll.commands.monitor import build_row_values, parse_duration
from fleetroll.commands.monitor.formatting import compute_columns_and_widths, format_monitor_row
from fleetroll.commands.monitor.uptime import format_uptime


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, "  -    "),
        (-1, " 0s ▎░░"),
        (0, " 0s ▎░░"),
        (59, "59s ▎░░"),
        (60, " 1m ▎░░"),
        (3599, "59m ▎░░"),
        (3600, " 1h █░░"),
        (6 * 3600 - 1, " 5h █░░"),
        (6 * 3600, " 6h █▌░"),
        (15 * 3600 + 43 * 60, "15h █▌░"),
        (86400 - 1, "23h █▌░"),
        (86400, " 1d ██░"),
        (7 * 86400 - 1, " 6d ██░"),
        (7 * 86400, " 7d ███"),
        (99 * 86400, "99d ███"),
        (100 * 86400, "14w ███"),
        (700 * 86400, " 1y ███"),
        (100 * 365 * 86400, "99y ███"),
    ],
)
def test_compact_uptime(seconds, expected):
    assert format_uptime(seconds) == expected
    assert len(expected) == 7


def test_table_uptime_is_compact_without_changing_filter_values():
    record = {
        "ok": True,
        "ts": "2026-11-07T00:00:00+00:00",
        "observed": {"uptime_s": 15 * 3600 + 43 * 60},
    }
    values = build_row_values("host1", record, last_ok=record)
    assert values["uptime"] == "15h 43m"
    assert parse_duration(values["uptime"]) == 15 * 3600 + 43 * 60
    _, widths = compute_columns_and_widths(
        hosts=["host1"],
        latest={"host1": record},
        latest_ok={"host1": record},
        max_width=0,
    )
    assert widths["uptime"] == 7
    assert (
        format_monitor_row(
            "host1",
            record,
            last_ok=record,
            columns=["uptime"],
            widths=widths,
        )
        == "15h █▌░"
    )
