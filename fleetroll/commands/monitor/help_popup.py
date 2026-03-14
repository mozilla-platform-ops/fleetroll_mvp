"""Help popup rendering for the monitor display."""

from __future__ import annotations

import contextlib
from curses import error as curses_error
from importlib.metadata import version as get_version

from .types import COLUMN_GUIDE_TEXT, FLEETROLL_MASCOT, HELP_COLUMNS, HELP_KEYBINDINGS


def draw_help_popup(stdscr, curses_mod, *, color_enabled: bool) -> None:
    """Draw a centered help popup with the column guide.

    Args:
        stdscr: The curses screen object
        curses_mod: The curses module (for creating new windows)
        color_enabled: Whether color is available
    """
    if not curses_mod:
        return

    height, width = stdscr.getmaxyx()

    try:
        ver = get_version("fleetroll")
    except Exception:
        ver = "?"
    version_line = f"fleetroll v{ver}"
    mascot_with_version = FLEETROLL_MASCOT + [version_line]
    mascot_line_count = len(mascot_with_version)

    h_pad = 2
    v_pad = 1

    left_lines = HELP_KEYBINDINGS.split("\n")
    right_lines = HELP_COLUMNS.split("\n")
    left_width = max(len(ln) for ln in left_lines)
    right_width = max(len(ln) for ln in right_lines)

    # Compute two-column layout dimensions
    two_col_content_width = left_width + 3 + right_width  # 3 = " │ "
    mascot_content_width = max(len(ln) for ln in mascot_with_version)
    content_width = max(two_col_content_width, mascot_content_width)
    required_popup_width = content_width + h_pad * 2

    use_two_col = (required_popup_width + 2) <= width  # +2 for borders

    # Initialize layout variables (resolved fully in branches below; pre-init for type checker)
    header_lines: list[str] = []
    col_rows: int = 0
    all_lines: list[str] = []

    if use_two_col:
        popup_width = required_popup_width
        header_lines = mascot_with_version + [""]
        col_rows = max(len(left_lines), len(right_lines))
        popup_height = len(header_lines) + col_rows + v_pad * 2
    else:
        # Single-column fallback
        all_lines = mascot_with_version + [""] + COLUMN_GUIDE_TEXT.strip().split("\n")
        content_width = max(len(line) for line in all_lines)
        popup_width = content_width + h_pad * 2
        popup_height = len(all_lines) + v_pad * 2

    # Center the popup
    start_y = max((height - popup_height) // 2, 0)
    start_x = max((width - popup_width) // 2, 0)

    # Clip to screen (leave room for border)
    if start_y + popup_height + 2 > height:
        popup_height = max(height - start_y - 2, 1)
    if start_x + popup_width + 2 > width:
        popup_width = max(width - start_x - 2, 10)

    # Create a window for the popup with border
    try:
        popup_win = curses_mod.newwin(popup_height + 2, popup_width + 2, start_y, start_x)
    except curses_error:
        return

    # Get attributes for popup (black on white - pair 18)
    popup_attr = curses_mod.color_pair(18) if color_enabled else curses_mod.A_REVERSE
    # Yellow text on white background (color pair 17)
    mascot_attr = curses_mod.color_pair(17) if color_enabled else curses_mod.A_REVERSE

    # Fill background and draw border using curses built-in
    popup_win.bkgd(" ", popup_attr)
    popup_win.border()

    current_row = v_pad + 1  # Start after border and top padding

    if use_two_col:
        # Draw mascot + version header, centered over full content width
        for i, line in enumerate(header_lines):
            if current_row >= popup_height + 1:
                break
            padding_needed = content_width - len(line)
            left_p = padding_needed // 2
            right_p = padding_needed - left_p
            if i < mascot_line_count:
                padded = " " * h_pad + " " * left_p + line + " " * right_p + " " * h_pad
            else:
                padded = " " * h_pad + " " * content_width + " " * h_pad
            if len(padded) > popup_width:
                padded = padded[:popup_width]
            try:
                attr = mascot_attr if i < mascot_line_count else popup_attr
                popup_win.addstr(current_row, 1, padded, attr)
            except curses_error:
                pass
            current_row += 1

        # Draw two-column body with separator
        for i in range(col_rows):
            if current_row >= popup_height + 1:
                break
            left = left_lines[i] if i < len(left_lines) else ""
            right = right_lines[i] if i < len(right_lines) else ""
            padded = (
                " " * h_pad
                + left.ljust(left_width)
                + " │ "
                + right.ljust(right_width)
                + " " * h_pad
            )
            if len(padded) > popup_width:
                padded = padded[:popup_width]
            with contextlib.suppress(curses_error):
                popup_win.addstr(current_row, 1, padded, popup_attr)
            current_row += 1
    else:
        # Single-column layout (original)
        max_content_rows = popup_height - (v_pad * 2)
        for i, line in enumerate(all_lines[:max_content_rows]):
            if current_row >= popup_height + 1:
                break

            # Center mascot lines, left-align others
            if i < mascot_line_count:
                padding_needed = content_width - len(line)
                left_pad = padding_needed // 2
                right_pad = padding_needed - left_pad
                padded = " " * h_pad + " " * left_pad + line + " " * right_pad + " " * h_pad
            else:
                padded = " " * h_pad + line.ljust(content_width) + " " * h_pad

            # Clip if too long
            if len(padded) > popup_width:
                padded = padded[:popup_width]

            # Draw content with appropriate attribute
            try:
                if i < mascot_line_count:
                    popup_win.addstr(current_row, 1, padded, mascot_attr)
                else:
                    popup_win.addstr(current_row, 1, padded, popup_attr)
            except curses_error:
                pass

            current_row += 1

    popup_win.noutrefresh()
