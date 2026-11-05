"""Behavior of monitor state colors without a terminal."""

from types import SimpleNamespace

import pytest
from fleetroll.commands.monitor.curses_colors import CursesColors


@pytest.fixture
def colors(monkeypatch):
    monkeypatch.setattr(CursesColors, "_init_curses", lambda self: None)
    result = CursesColors(None)
    result.color_enabled = True
    result.curses_mod = SimpleNamespace(color_pair=lambda number: number * 100)
    return result


@pytest.mark.parametrize(
    ("method", "first", "second"),
    [
        ("uptime_attr", 3600, 21600),
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


@pytest.mark.parametrize("value", [None, "", "unknown", "running"])
def test_unknown_task_state_is_neutral(colors, value):
    assert colors.tc_j_sf_attr(value) == 0


def test_disabled_colors_are_neutral(colors):
    colors.color_enabled = False
    assert colors.uptime_attr(21600) == 0
    assert colors.pp_last_attr(None, failed=True) == 0
    assert colors.tc_j_sf_attr("FAILED") == 0
    assert colors.ro_health_attr("N") == 0
