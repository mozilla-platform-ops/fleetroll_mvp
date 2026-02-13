"""Help popup rendering for the monitor display."""

from __future__ import annotations

from curses import error as curses_error
from importlib.metadata import version as get_version

from .types import COLUMN_GUIDE_TEXT, FLEETROLL_MASCOT


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

    # Combine mascot, version, and help text
    try:
        ver = get_version("fleetroll")
    except Exception:
        ver = "?"
    version_line = f"fleetroll v{ver}"
    mascot_with_version = FLEETROLL_MASCOT + [version_line]
    all_lines = mascot_with_version + [""] + COLUMN_GUIDE_TEXT.strip().split("\n")
    mascot_line_count = len(mascot_with_version)

    # Add padding: 2 chars horizontal, 1 line vertical
    h_pad = 2
    v_pad = 1

    # Calculate popup dimensions
    content_width = max(len(line) for line in all_lines)
    popup_width = content_width + (h_pad * 2)
    popup_height = len(all_lines) + (v_pad * 2)

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

    # Draw content lines with padding
    current_row = v_pad + 1  # Start after border and top padding
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
