"""Long notes remain readable with the monitor's horizontal navigation."""

import curses
from unittest.mock import Mock

import pytest
from fleetroll.commands.monitor.curses_colors import CursesColors
from fleetroll.commands.monitor.display import MonitorDisplay


@pytest.fixture
def display(monkeypatch):
    monkeypatch.setattr(CursesColors, "_init_curses", lambda self: None)
    monkeypatch.setattr(MonitorDisplay, "_check_log_sizes", lambda self: [])
    return MonitorDisplay(
        Mock(),
        hosts=["host1"],
        host_source="hosts.list",
        latest={},
        latest_ok={},
        tc_data={},
        provider=Mock(load_windows_pools=dict),
        github_refs={},
        notes_data={"host1": "start " + "long note " * 10 + "the end"},
    )


@pytest.mark.parametrize(
    ("right", "left"), [(ord("l"), ord("h")), (curses.KEY_RIGHT, curses.KEY_LEFT)]
)
def test_read_entire_note_and_return_to_columns(display, right, left):
    """Exercise keys, layout and real row rendering, retaining the frozen host."""
    display.curses_mod = curses
    draw = Mock()
    display.row_renderer.safe_addstr = draw
    note = display.notes_data["host1"]
    screens = []
    indicators = []

    def redraw():
        widths = {"host": 5, "healthy": 7, "note": len(note)}
        columns, indicator = display._compute_visible_columns(  # noqa: SLF001
            ["host", "healthy", "note"], widths=widths, usable_width=30
        )
        data = display.row_renderer.compute_row_render_data(
            "host1",
            latest={},
            latest_ok={},
            tc_data={},
            fqdn_suffix=None,
            sha_cache=None,
            github_refs={},
            notes_data=display.notes_data,
        )
        draw.reset_mock()
        display.row_renderer.draw_host_row(
            2,
            render_data=data,
            columns=columns,
            widths=widths,
            color_maps={"sha": {}, "role": {}, "vlt_sha": {}},
            note_offset=display.note_offset,
        )
        screens.append("".join(call.args[2] for call in draw.call_args_list))
        indicators.append(indicator)
        assert screens[-1].startswith("host1 | ")
        assert len(screens[-1]) <= 30
        assert data["values"]["note"] == note

    display.draw_screen = redraw
    redraw()
    assert "start" in screens[-1]
    assert "▶" in indicators[-1]
    display.handle_key(right)
    assert display.col_offset == 1
    assert display.note_offset == 0
    for _ in range(len(note)):
        display.handle_key(right)
        if display.note_offset == display.max_note_offset:
            break
    assert screens[-1].endswith("the end")
    assert "▶" not in indicators[-1]
    last_screen = screens[-1]
    display.handle_key(right)
    assert screens[-1] == last_screen
    for _ in range(len(note)):
        display.handle_key(left)
        if display.note_offset == 0:
            break
        assert display.col_offset == 1
    assert display.col_offset == 1
    display.handle_key(left)
    assert display.col_offset == 0
    assert screens[-1] == screens[0]


@pytest.mark.parametrize("change", ["resize", "shorten", "hide"])
def test_note_offset_clamped_after_layout_change(display, change):
    display.col_offset = 1
    display.note_offset = 70
    widths = {"host": 5, "healthy": 7, "note": 100}
    columns = ["host", "healthy", "note"]
    usable_width = 30
    if change == "resize":
        usable_width = 200
    elif change == "shorten":
        widths["note"] = 4
    else:
        usable_width = 10
    display._compute_visible_columns(columns, widths=widths, usable_width=usable_width)  # noqa: SLF001
    assert display.note_offset == display.max_note_offset == 0
