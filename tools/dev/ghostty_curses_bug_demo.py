#!/usr/bin/env python3
"""
Demonstration of Ghostty terminal rendering issue with curses.

ISSUE: When drawing a centered popup dialog with box-drawing characters,
the top border line does not render in Ghostty terminal, but works fine
in WezTerm and other terminal emulators.

REPRODUCTION:
1. Run this script in Ghostty: `python ghostty_curses_bug_demo.py`
   - Observe that the top border (┌─...─┐) is MISSING
2. Run the same script in WezTerm or another terminal
   - Observe that the top border renders correctly

POSSIBLE CAUSES:
- Ghostty curses rendering bug at specific row positions
- Font rendering issue with Unicode box-drawing characters
- Font fallback issue in Ghostty

The script draws TWO popups side-by-side:
- Left: Unicode box-drawing characters (┌─┐│└┘)
- Right: ASCII characters (+-+||+-+)

This helps determine if it's a Unicode/font issue or a general rendering issue.

Tested with:
- Ghostty: top border missing (both Unicode and ASCII)
- WezTerm: works correctly (both styles)
"""

import curses


def draw_popup(stdscr, start_y, start_x, width, height, use_unicode=True):
    """Draw a popup with borders at the specified position.

    Args:
        use_unicode: If True, use Unicode box-drawing chars (┌─┐│└┘)
                     If False, use ASCII chars (+-+|+-+)
    """
    max_height, max_width = stdscr.getmaxyx()

    if use_unicode:
        # Unicode box-drawing characters
        top_left, top_right = "┌", "┐"
        bottom_left, bottom_right = "└", "┘"
        horizontal, vertical = "─", "│"
        label = "Unicode: ┌─┐│└┘"
    else:
        # ASCII characters
        top_left, top_right = "+", "+"
        bottom_left, bottom_right = "+", "+"
        horizontal, vertical = "-", "|"
        label = "ASCII: +-+|"

    # ===== DRAW TOP BORDER (PROBLEMATIC LINE IN GHOSTTY) =====
    try:
        top_border = top_left + (horizontal * (width - 2)) + top_right
        stdscr.addstr(start_y, start_x, top_border)
    except curses.error:
        pass

    # Draw content lines with side borders
    for i in range(1, height - 1):
        row = start_y + i
        if row >= max_height:
            break

        try:
            # Left border
            stdscr.addstr(row, start_x, vertical)

            # Content
            if i == 1:
                content = label.center(width - 2)
            elif i == 3:
                content = "Top border should".center(width - 2)
            elif i == 4:
                content = "be visible above".center(width - 2)
            elif i == 6:
                content = f"start_y={start_y}".center(width - 2)
            elif i == 7:
                content = f"this row={row}".center(width - 2)
            else:
                content = " " * (width - 2)

            stdscr.addstr(row, start_x + 1, content)

            # Right border
            stdscr.addstr(row, start_x + width - 1, vertical)
        except curses.error:
            pass

    # Draw bottom border
    bottom_row = start_y + height - 1
    if bottom_row < max_height:
        try:
            bottom_border = bottom_left + (horizontal * (width - 2)) + bottom_right
            stdscr.addstr(bottom_row, start_x, bottom_border)
        except curses.error:
            pass


def main(stdscr):
    # Clear screen
    stdscr.clear()

    # Get screen dimensions
    height, width = stdscr.getmaxyx()

    # Draw header
    stdscr.addstr(0, 0, "Ghostty Curses Rendering Test")
    stdscr.addstr(1, 0, "Both popups should have TOP borders visible")

    # Popup dimensions
    popup_width = 35
    popup_height = 12

    # Calculate positions for side-by-side popups
    start_y = max((height - popup_height) // 2, 2)

    # Left popup (Unicode)
    left_x = max((width // 2 - popup_width - 2), 2)
    draw_popup(stdscr, start_y, left_x, popup_width, popup_height, use_unicode=True)

    # Right popup (ASCII)
    right_x = width // 2 + 2
    draw_popup(stdscr, start_y, right_x, popup_width, popup_height, use_unicode=False)

    # Footer with instructions
    stdscr.addstr(height - 3, 0, "If top borders are missing, this may indicate:")
    stdscr.addstr(height - 2, 0, "  - Font rendering issue (if only Unicode fails)")
    stdscr.addstr(height - 1, 0, "  - General rendering bug (if both fail) | Press any key to exit")

    stdscr.refresh()
    stdscr.getch()


if __name__ == "__main__":
    curses.wrapper(main)
