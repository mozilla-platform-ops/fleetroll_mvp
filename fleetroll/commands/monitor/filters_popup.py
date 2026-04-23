"""Filter picker popup for the host-monitor TUI.

Modal overlay with two tabs — Saved and Recent — for browsing named filters
(from configs/filters/*.yaml) and recent filter history. v1 is read-only;
mutations happen by editing the YAML files directly.
"""

from __future__ import annotations

import contextlib
from curses import error as curses_error
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .named_filters import NamedFilter

TAB_SAVED = "saved"
TAB_RECENT = "recent"

# Size caps — keep the popup dialog-sized even on wide/tall terminals.
MAX_POPUP_WIDTH = 100
MAX_POPUP_HEIGHT = 25
MIN_POPUP_WIDTH = 30
MIN_POPUP_HEIGHT = 6
# Margins subtracted from terminal dimensions before capping.
H_MARGIN = 4
V_MARGIN = 6


@dataclass
class PopupRow:
    """One row shown in the popup. `label` is the left column (name); `query`
    is the right column. For Recent entries, label is empty."""

    label: str
    query: str

    def match_text(self) -> str:
        return f"{self.label} {self.query}".strip()


@dataclass
class TabState:
    cursor: int = 0
    viewport_start: int = 0


@dataclass
class FiltersPopupState:
    """Mutable state for the picker popup. Held on MonitorDisplay while open."""

    active_tab: str = TAB_SAVED
    search: str = ""
    saved_state: TabState = field(default_factory=TabState)
    recent_state: TabState = field(default_factory=TabState)
    flash_msg: str = ""
    flash_expiry: float = 0.0

    def tab_state(self, tab: str | None = None) -> TabState:
        tab = tab or self.active_tab
        return self.saved_state if tab == TAB_SAVED else self.recent_state


def compute_popup_viewport(
    selected: int,
    viewport_start: int,
    visible_rows: int,
    total_rows: int,
) -> int:
    """Clamp viewport_start so that `selected` is on-screen.

    Returns the new viewport_start. Handles the empty case and scrolls by the
    minimum amount needed to keep the cursor visible.
    """
    if visible_rows <= 0 or total_rows <= 0:
        return 0
    max_start = max(total_rows - visible_rows, 0)
    viewport_start = max(0, min(viewport_start, max_start))
    if selected < viewport_start:
        return max(selected, 0)
    if selected >= viewport_start + visible_rows:
        return max(selected - visible_rows + 1, 0)
    return viewport_start


def filter_rows(rows: list[PopupRow], search: str) -> list[PopupRow]:
    """Filter rows by case-insensitive substring match on label+query."""
    if not search:
        return list(rows)
    needle = search.lower()
    return [r for r in rows if needle in r.match_text().lower()]


def find_active_row_index(rows: list[PopupRow], active_query: str) -> int:
    """Return the index of the first row whose query matches, or -1."""
    if not active_query:
        return -1
    for i, row in enumerate(rows):
        if row.query == active_query:
            return i
    return -1


def build_saved_rows(filters: list[NamedFilter]) -> list[PopupRow]:
    return [PopupRow(label=f.name, query=f.query) for f in filters]


def build_recent_rows(history: list[str]) -> list[PopupRow]:
    # history is oldest→newest; show most-recent first.
    return [PopupRow(label="", query=q) for q in reversed(history) if q]


def draw_filters_popup(
    stdscr,
    curses_mod,
    state: FiltersPopupState,
    *,
    saved_rows: list[PopupRow],
    recent_rows: list[PopupRow],
    color_enabled: bool,
    active_query: str = "",
) -> None:
    """Draw the picker popup centered on-screen.

    Side-effect: updates state.tab_state().viewport_start as needed so the
    cursor stays visible.
    """
    if not curses_mod:
        return

    height, width = stdscr.getmaxyx()
    if height < 10 or width < 30:
        _draw_resize_message(stdscr, curses_mod, color_enabled=color_enabled)
        return

    popup_h = max(min(height - V_MARGIN, MAX_POPUP_HEIGHT), MIN_POPUP_HEIGHT)
    popup_w = max(min(width - H_MARGIN, MAX_POPUP_WIDTH), MIN_POPUP_WIDTH)

    start_y = max((height - popup_h) // 2, 0)
    start_x = max((width - popup_w) // 2, 0)

    try:
        win = curses_mod.newwin(popup_h, popup_w, start_y, start_x)
    except curses_error:
        return

    popup_attr = curses_mod.color_pair(18) if color_enabled else curses_mod.A_REVERSE
    reverse_attr = popup_attr | curses_mod.A_REVERSE
    win.bkgd(" ", popup_attr)
    win.border()

    # Title on top border
    title = " Filters "
    with contextlib.suppress(curses_error):
        win.addstr(0, max((popup_w - len(title)) // 2, 1), title, popup_attr)

    inner_w = popup_w - 4  # 1 border + 1 pad on each side
    inner_left = 2

    # Row 1: tab strip
    tab_row = 1
    tabs = [("Saved", TAB_SAVED), ("Recent", TAB_RECENT)]
    col = inner_left
    for label, tab_id in tabs:
        attr = reverse_attr if state.active_tab == tab_id else popup_attr
        with contextlib.suppress(curses_error):
            win.addstr(tab_row, col, label, attr)
        col += len(label) + 2  # 2-space separator

    # Determine active rows after filter
    all_rows = saved_rows if state.active_tab == TAB_SAVED else recent_rows
    filtered = filter_rows(all_rows, state.search)

    # Rows area: between top (border + tab + blank) and bottom (blank + border)
    # Layout: y=0 border, y=1 tabs, y=2 blank, y=3..popup_h-3 rows, y=popup_h-2 status, y=popup_h-1 border
    rows_top = 3
    rows_bottom = popup_h - 3
    visible_rows = max(rows_bottom - rows_top + 1, 0)

    tab_state = state.tab_state()
    total = len(filtered)
    tab_state.cursor = 0 if total == 0 else min(tab_state.cursor, total - 1)
    if total == 0:
        tab_state.cursor = 0
    tab_state.viewport_start = compute_popup_viewport(
        tab_state.cursor, tab_state.viewport_start, visible_rows, total
    )

    # Compute label column width for alignment
    label_w = 0
    if filtered:
        label_w = max(len(r.label) for r in filtered)
        label_w = min(label_w, max(inner_w // 3, 10))

    for i in range(visible_rows):
        idx = tab_state.viewport_start + i
        y = rows_top + i
        if idx >= total:
            break
        row = filtered[idx]
        is_sel = idx == tab_state.cursor
        is_active = bool(active_query) and row.query == active_query
        cursor_glyph = "›" if is_sel else " "  # noqa: RUF001
        active_glyph = "●" if is_active else " "
        prefix = f"{cursor_glyph}{active_glyph}"
        if label_w > 0:
            label = row.label[:label_w].ljust(label_w)
            text = f"{prefix}{label}  {row.query}"
        else:
            text = f"{prefix}{row.query}"
        text = text[: inner_w - 2]  # leave room for scroll arrow
        text = text.ljust(inner_w - 2)
        attr = reverse_attr if is_sel else popup_attr
        with contextlib.suppress(curses_error):
            win.addstr(y, inner_left, text, attr)

    # Scroll arrows on right edge
    arrow_col = popup_w - 2
    if tab_state.viewport_start > 0:
        with contextlib.suppress(curses_error):
            win.addstr(rows_top, arrow_col, "▲", popup_attr)
    if tab_state.viewport_start + visible_rows < total:
        with contextlib.suppress(curses_error):
            win.addstr(rows_bottom, arrow_col, "▼", popup_attr)

    # Bottom border status: left = find/flash/hint, right = m/n
    import time as _time

    if state.flash_msg and _time.monotonic() < state.flash_expiry:
        status_left = f" {state.flash_msg} "
    elif state.search:
        status_left = f" find: {state.search}_ "
    else:
        status_left = " type to filter · ↑↓ nav · ←→ tabs · ↵ apply · esc close "

    m = total
    n = len(all_rows)
    status_right = f" {m}/{n} "

    bottom_row = popup_h - 1
    with contextlib.suppress(curses_error):
        if status_left:
            win.addstr(bottom_row, 2, status_left, popup_attr)
        win.addstr(
            bottom_row,
            max(popup_w - len(status_right) - 2, 2),
            status_right,
            popup_attr,
        )

    win.noutrefresh()


def _draw_resize_message(stdscr, curses_mod, *, color_enabled: bool) -> None:
    height, width = stdscr.getmaxyx()
    msg = "resize terminal"
    attr = curses_mod.color_pair(18) if color_enabled else curses_mod.A_REVERSE
    try:
        win = curses_mod.newwin(
            3, len(msg) + 4, max(height // 2 - 1, 0), max((width - len(msg) - 4) // 2, 0)
        )
    except curses_error:
        return
    win.bkgd(" ", attr)
    win.border()
    with contextlib.suppress(curses_error):
        win.addstr(1, 2, msg, attr)
    win.noutrefresh()
