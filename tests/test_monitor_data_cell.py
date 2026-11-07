"""Compact DATA display preserves source identity and freshness semantics."""

from unittest.mock import Mock

import pytest
from fleetroll.commands.monitor import data
from fleetroll.commands.monitor import row_renderer as row_renderer_module
from fleetroll.commands.monitor.curses_colors import CursesColors
from fleetroll.commands.monitor.formatting import (
    compute_columns_and_widths,
    format_data_cell,
    format_monitor_row,
)
from fleetroll.commands.monitor.query import _get_data_seconds
from fleetroll.commands.monitor.row_renderer import RowRenderer


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5m/5m", "5m"),
        ("<1m/<1m", "<1m"),
        ("5m/12m", "5m/12m"),
        ("1h 02m/1h 58m", "1h"),
        ("15h 43m/2d 01h", "15h/2d"),
        ("99d 00h/100d 00h", "99d/14w"),
        ("5m/-", "5m/-"),
        ("-/5m", "-/5m"),
        ("?/5m", "?/5m"),
        ("?/-", "?/-"),
        ("-/-", "-"),
    ],
)
def test_compact_data_labels(raw, expected):
    assert format_data_cell(raw) == expected
    assert len(expected) <= 7


@pytest.fixture
def source_ages(monkeypatch):
    ages = {"audit-ts": 120, "tc-ts": 2700}

    def age(timestamp):
        return ages.get(timestamp)

    monkeypatch.setattr(data, "age_seconds", age)
    monkeypatch.setattr(row_renderer_module, "age_seconds", age)
    return ages


@pytest.mark.parametrize(
    ("audit_age", "tc_age", "expected", "expected_spans"),
    [
        (120, 2700, "2m/45m", [("2m", 400), ("/", 0), ("45m", 600)]),
        (2700, 120, "45m/2m", [("45m", 600), ("/", 0), ("2m", 400)]),
        (300, 720, "5m/12m", [("5m", 500), ("/", 0), ("12m", 500)]),
        (120, 125, "2m", [("2m", 400)]),
        (3600, 7080, "1h", [("1h", 600)]),
        (None, 120, "-/2m", [("-", 0), ("/", 0), ("2m", 400)]),
        (120, None, "2m/-", [("2m", 400), ("/", 0), ("-", 0)]),
        (None, None, "-", [("-", 0)]),
    ],
)
@pytest.mark.parametrize("color_enabled", [True, False])
def test_data_source_colors_and_raw_values(
    monkeypatch, source_ages, audit_age, tc_age, expected, expected_spans, color_enabled
):
    source_ages.update({"audit-ts": audit_age, "tc-ts": tc_age})
    monkeypatch.setattr(CursesColors, "_init_curses", lambda self: None)
    colors = CursesColors(None)
    colors.color_enabled = color_enabled
    colors.curses_mod = Mock(color_pair=lambda number: number * 100)
    draw = Mock()
    renderer = RowRenderer(safe_addstr=draw, colors=colors)
    record = {"ok": True, "ts": "audit-ts", "observed": {}}
    latest = {"host1": record}
    tc_data = {"host1": {"ts": "tc-ts"}}
    render_data = renderer.compute_row_render_data(
        "host1",
        latest=latest,
        latest_ok=latest,
        tc_data=tc_data,
        fqdn_suffix=None,
        sha_cache=None,
        github_refs={},
    )
    _, widths = compute_columns_and_widths(
        hosts=["host1"],
        latest=latest,
        latest_ok=latest,
        tc_data=tc_data,
        max_width=0,
    )
    assert widths["data"] == max(4, len(expected))
    renderer.draw_host_row(
        1,
        render_data=render_data,
        columns=["data", "host"],
        widths={"data": widths["data"], "host": 5},
        color_maps={"sha": {}, "vlt_sha": {}, "role": {}},
    )
    calls = draw.call_args_list
    for call, (text, attr) in zip(calls, expected_spans):
        assert call.args[2].strip() == text
        assert call.args[3] == (attr if color_enabled else 0)
    assert calls[len(expected_spans)].args == (1, widths["data"], " | ", 0)
    assert (
        format_monitor_row(
            "host1",
            record,
            last_ok=record,
            tc_data=tc_data["host1"],
            columns=["data"],
            widths=widths,
        ).strip()
        == expected
    )
    # Queries still compare the original age, including minutes hidden by a whole-hour label.
    known_ages = [age for age in (audit_age, tc_age) if age is not None]
    expected_seconds = max(known_ages) // 60 * 60 if known_ages else None
    assert _get_data_seconds(render_data["values"]) == expected_seconds


def test_failed_audit_without_success_does_not_get_a_fresh_color(monkeypatch, source_ages):
    source_ages.update({"audit-ts": 120, "tc-ts": None})
    draw = Mock()
    colors = Mock(last_ok_attr=lambda age: 400 if age is not None else 0)
    renderer = RowRenderer(safe_addstr=draw, colors=colors)
    render_data = renderer.compute_row_render_data(
        "host1",
        latest={"host1": {"ok": False, "ts": "audit-ts"}},
        latest_ok={},
        tc_data={},
        fqdn_suffix=None,
        sha_cache=None,
        github_refs={},
    )
    renderer.draw_host_row(
        1,
        render_data=render_data,
        columns=["data"],
        widths={"data": 4},
        color_maps={"sha": {}, "vlt_sha": {}, "role": {}},
    )
    draw.assert_called_once_with(1, 0, "-   ", 0)
