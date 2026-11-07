"""Behavior of monitor state colors without a terminal."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fleetroll.commands.monitor.curses_colors import CursesColors
from fleetroll.commands.monitor.row_renderer import RowRenderer
from fleetroll.commands.monitor.uptime import format_uptime


@pytest.fixture
def colors(monkeypatch):
    monkeypatch.setattr(CursesColors, "_init_curses", lambda self: None)
    result = CursesColors(None)
    result.color_enabled = True
    result.curses_mod = SimpleNamespace(color_pair=lambda number: number * 100, A_DIM=1, A_BOLD=2)
    return result


@pytest.mark.parametrize(
    ("method", "first", "second"),
    [
        ("last_ok_attr", 300, 1800),
        ("tc_act_attr", 300, 3600),
        ("pp_last_attr", 3600, 21600),
    ],
)
def test_age_boundaries(colors, method, first, second):
    attr = getattr(colors, method)
    assert [attr(value) for value in (first - 1, first, first + 1)] == [400, 500, 500]
    assert [attr(value) for value in (second - 1, second, second + 1)] == [500, 600, 600]
    assert attr(None) == 0


def test_failed_puppet_is_red_even_without_age(colors):
    assert colors.pp_last_attr(None, failed=True) == 600
    assert colors.pp_last_attr(1, failed=True) == 600


@pytest.mark.parametrize(
    ("uptime_s", "expected_attr"),
    [
        (None, 0),
        (0, 10000),
        (3599, 10000),
        (3600, 10100),
        (21600, 10200),
        (86400, 10300),
        (7 * 86400, 10400),
    ],
)
def test_uptime_renders_with_blue_magnitude_color(colors, uptime_s, expected_attr):
    colors.uptime_extended_colors = True
    draw = Mock()
    renderer = RowRenderer(safe_addstr=draw, colors=colors)
    record = {
        "ok": True,
        "ts": "2026-11-07T00:00:00+00:00",
        "observed": {"uptime_s": uptime_s},
    }
    render_data = renderer.compute_row_render_data(
        "host1",
        latest={"host1": record},
        latest_ok={"host1": record},
        tc_data={},
        fqdn_suffix=None,
        sha_cache=None,
        github_refs={},
    )
    renderer.draw_host_row(
        1,
        render_data=render_data,
        columns=["uptime"],
        widths={"uptime": 7},
        color_maps={"sha": {}, "vlt_sha": {}, "role": {}},
    )

    draw.assert_called_once()
    row, col, cell, attr = draw.call_args.args
    assert (row, col, attr) == (1, 0, expected_attr)
    assert cell == format_uptime(uptime_s)


@pytest.mark.parametrize(
    ("uptime_s", "expected_attr"),
    [(None, 0), (0, 801), (3600, 800), (21600, 800), (86400, 802), (7 * 86400, 802)],
)
def test_uptime_basic_terminal_uses_cyan_intensity(colors, uptime_s, expected_attr):
    assert colors.uptime_attr(uptime_s) == expected_attr


@pytest.mark.parametrize("value", [None, "", "unknown", "running"])
def test_unknown_task_state_is_neutral(colors, value):
    assert colors.tc_j_sf_attr(value) == 0


@pytest.mark.parametrize(
    ("method", "value", "expected"),
    [
        ("pp_match_attr", "Y", 400),
        ("pp_match_attr", "N", 500),
        ("pp_match_attr", "-", 0),
        ("pp_match_attr", "unexpected", 0),
        ("ro_health_attr", "Y", 400),
        ("ro_health_attr", "N", 600),
        ("ro_health_attr", "-", 0),
        ("ro_health_attr", "unexpected", 0),
        ("tc_quar_attr", "YES", 600),
        ("tc_quar_attr", "-", 0),
        ("tc_quar_attr", "unexpected", 0),
        ("tc_j_sf_attr", "completed", 400),
        ("tc_j_sf_attr", "exception", 500),
        ("tc_j_sf_attr", "failed", 600),
    ],
)
def test_state_color_policy(colors, method, value, expected):
    assert getattr(colors, method)(value) == expected


def test_disabled_colors_are_neutral(colors):
    colors.color_enabled = False
    assert colors.uptime_attr(7 * 86400) == 0
    assert colors.pp_last_attr(None, failed=True) == 0
    assert colors.tc_j_sf_attr("FAILED") == 0
    assert colors.ro_health_attr("N") == 0
    assert colors.pp_match_attr("Y") == 0
    assert colors.tc_quar_attr("YES") == 0
    assert colors.red_attr() == 0
