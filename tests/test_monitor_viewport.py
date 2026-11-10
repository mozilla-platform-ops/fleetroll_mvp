"""All table columns share one horizontal viewport with a frozen hostname."""

import curses
from unittest.mock import Mock

import pytest
from fleetroll.commands.monitor.curses_colors import CursesColors
from fleetroll.commands.monitor.display import MonitorDisplay
from fleetroll.commands.monitor.viewport import HorizontalViewport


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
    )


@pytest.mark.parametrize("offset", [0, 1, 6, 12, 24, 100])
@pytest.mark.parametrize("screen_width", [0, 3, 8, 9, 12, 25, 100])
def test_clip_colored_spans_at_both_edges(offset, screen_width):
    columns = ["host", "role", "note"]
    widths = {"host": 5, "role": 12, "note": 9}
    viewport = HorizontalViewport.for_columns(
        columns, widths=widths, usable_width=screen_width, offset=offset
    )
    spans = [("host1 | ", 1), ("role-content", 2), (" | ", 0), ("note-text", 3)]
    full = [(char, attr) for text, attr in spans for char in text]
    expected = (
        full[: min(8, screen_width)]
        + full[8 + viewport.offset : 8 + viewport.offset + viewport.width]
    )
    actual = [(" ", 0)] * len(expected)

    def write(row, col, text, attr):
        assert row == 2
        assert 0 <= col < screen_width
        assert col + len(text) <= screen_width
        actual[col : col + len(text)] = [(char, attr) for char in text]

    draw = viewport.wrap(write)
    col = 0
    for text, attr in spans:
        draw(2, col, text, attr)
        col += len(text)
    assert actual == expected
    # Also handle a single span crossing HOST and the scrolling boundary.
    combined = Mock()
    viewport.wrap(combined)(2, 0, "".join(text for text, _ in spans), 7)
    assert "".join(call.args[2] for call in combined.call_args_list) == "".join(
        char for char, _ in expected
    )
    assert all(call.args[3] == 7 for call in combined.call_args_list)


@pytest.mark.parametrize(
    ("right", "left"), [(ord("l"), ord("h")), (curses.KEY_RIGHT, curses.KEY_LEFT)]
)
@pytest.mark.parametrize("screen_width", [10, 25, 140])
def test_keyboard_scrolls_header_and_rows_together(display, right, left, screen_width):
    """Long ROLE and NOTE cells use the same navigation and retain text/colors."""
    display.curses_mod = curses
    role = "role beginning " + "long role " * 3 + "role end"
    note = "note beginning " + "long note " * 3 + "note end"
    data = display.row_renderer.compute_row_render_data(
        "host1",
        latest={"host1": {"ok": True, "observed": {"role": role, "role_present": True}}},
        latest_ok={},
        tc_data={},
        fqdn_suffix=None,
        sha_cache=None,
        github_refs={},
        notes_data={"host1": note},
    )
    columns = ["host", "role", "note"]
    widths = {"host": 5, "role": len(role) + 2, "note": len(note)}
    labels = {"host": "HOST", "role": "ROLE", "note": "NOTE"}

    def capture(viewport=None):
        rows = {}

        def write(row, col, text, attr=0):
            line = rows.setdefault(row, [])
            line.extend([(" ", 0)] * max(col + len(text) - len(line), 0))
            line[col : col + len(text)] = [(char, attr) for char in text]

        display.header_renderer.safe_addstr = write
        display.row_renderer.safe_addstr = write
        display.header_renderer.draw_column_header(
            labels=labels, columns=columns, widths=widths, viewport=viewport
        )
        display.row_renderer.draw_host_row(
            2,
            render_data=data,
            columns=columns,
            widths=widths,
            color_maps={"role": {role: 42}, "sha": {}, "vlt_sha": {}},
            viewport=viewport,
        )
        return rows

    full = capture()
    observed_offsets = []

    def redraw():
        viewport = display._compute_viewport(  # noqa: SLF001
            columns, widths=widths, usable_width=screen_width
        )
        observed_offsets.append(viewport.offset)
        rows = capture(viewport)
        for row in (1, 2):
            expected = (
                full[row][:8]
                + full[row][8 + viewport.offset : 8 + viewport.offset + viewport.width]
            )
            assert rows[row] == expected
        return rows

    display.draw_screen = redraw
    first = redraw()
    assert widths["note"] == len(note)
    while display.col_offset < display.max_col_offset:
        before = display.col_offset
        display.handle_key(right)
        assert 0 < display.col_offset - before <= screen_width - 8 + 3
    last = redraw()
    assert "".join(char for char, _ in last[2]).endswith("note end"[-min(8, screen_width - 8) :])
    display.handle_key(right)
    assert redraw() == last
    while display.col_offset:
        display.handle_key(left)
    assert redraw() == first
    display.handle_key(left)
    assert display.col_offset == 0
    # Every character was reachable, including partial cells wider than the screen.
    width = screen_width - 8
    assert all(
        any(offset <= pos < offset + width for offset in observed_offsets)
        for _, start, end in display._horizontal_viewport.columns  # noqa: SLF001
        for pos in range(start, end)
    )


def test_ordinary_columns_keep_column_jumps():
    columns = ["host", "os", "role", "note"]
    widths = {"host": 5, "os": 2, "role": 7, "note": 30}

    def viewport(offset):
        return HorizontalViewport.for_columns(
            columns, widths=widths, usable_width=20, offset=offset
        )

    assert viewport(0).next_offset == 5  # ROLE starts after OS and its separator.
    assert viewport(5).previous_offset == 0
    assert viewport(5).next_offset == 15  # NOTE starts after ROLE and its separator.
    assert viewport(15).next_offset == 26  # NOTE is wider than the viewport.
    assert viewport(26).previous_offset == 15
    assert viewport(0).indicator == " [▶ 1-2/3]"


@pytest.mark.parametrize("column", ["role", "note", "sha"])
def test_oversized_columns_all_use_overlapping_sections(column):
    viewport = HorizontalViewport.for_columns(
        ["host", column, "data"],
        widths={"host": 5, column: 30, "data": 8},
        usable_width=20,
        offset=0,
    )
    assert viewport.scroll_positions == (0, 11, 22, 29)


@pytest.mark.parametrize("change", ["resize", "shorten", "hidden"])
def test_viewport_clamps_after_layout_change(display, change):
    columns = ["host", "role", "note"]
    widths = {"host": 5, "role": 10, "note": 100}
    display.col_offset = 500
    first = display._compute_viewport(columns, widths=widths, usable_width=25)  # noqa: SLF001
    assert first.offset == first.max_offset > 0
    if change == "resize":
        usable_width = 200
    elif change == "shorten":
        widths["note"] = 4
        usable_width = 25
    else:
        usable_width = 3
    viewport = display._compute_viewport(  # noqa: SLF001
        columns, widths=widths, usable_width=usable_width
    )
    assert viewport.offset == viewport.max_offset == display.col_offset == 0
    assert viewport.indicator == ""
