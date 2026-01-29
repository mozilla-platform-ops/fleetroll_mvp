#!/usr/bin/env python3
"""Test program to visualize color palette combinations."""

from __future__ import annotations

import argparse
import sys

# ANSI color codes
RESET = "\033[0m"
REVERSE = "\033[7m"

# Basic foreground colors (matching curses color pairs)
COLORS = {
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "magenta": "\033[35m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "white": "\033[37m",
}

# High-contrast fg/bg combinations
FG_BG_COMBOS = [
    ("\033[33;44m", "yellow/blue"),  # yellow on blue
    ("\033[30;46m", "black/cyan"),  # black on cyan
    ("\033[30;42m", "black/green"),  # black on green
    ("\033[33;45m", "yellow/magenta"),  # yellow on magenta
    ("\033[30;43m", "black/yellow"),  # black on yellow
    ("\033[37;41m", "white/red"),  # white on red
    ("\033[37;44m", "white/blue"),  # white on blue
    ("\033[36;40m", "cyan/black"),  # cyan on black
    ("\033[32;40m", "green/black"),  # green on black
    ("\033[33;40m", "yellow/black"),  # yellow on black
    ("\033[37;45m", "white/magenta"),  # white on magenta
    ("\033[30;47m", "black/white"),  # black on white
    ("\033[34;43m", "blue/yellow"),  # blue on yellow
    ("\033[31;46m", "red/cyan"),  # red on cyan
    ("\033[35;43m", "magenta/yellow"),  # magenta on yellow
    ("\033[34;47m", "blue/white"),  # blue on white
]


def build_color_map(
    value_count: int,
    *,
    seed: int = 0,
) -> list[tuple[str, str]]:
    """Build a color map using three tiers: normal, reverse, fg/bg combos.

    Adjacent seeds are automatically spread for maximum visual distinction.

    Returns list of (label, ansi_code) tuples.
    """
    # Build basic palette (matches monitor display.py order)
    basic_colors = ["blue", "cyan", "green", "magenta", "yellow", "cyan", "yellow", "magenta"]
    palette_size = len(basic_colors)
    fg_bg_count = len(FG_BG_COMBOS)
    total_capacity = palette_size * 2 + fg_bg_count

    # Spread adjacent seeds for maximum visual distinction (matches display.py)
    spread_factor = 11
    effective_seed = (seed * spread_factor) % total_capacity

    result = []

    for idx in range(value_count):
        adjusted_idx = (idx + effective_seed) % total_capacity

        if adjusted_idx < palette_size:
            # Tier 1: Normal basic colors
            color_name = basic_colors[adjusted_idx]
            ansi_code = COLORS[color_name]
            color_label = f"normal:{color_name}"
        elif adjusted_idx < palette_size * 2:
            # Tier 2: Reversed basic colors
            color_idx = adjusted_idx - palette_size
            color_name = basic_colors[color_idx]
            ansi_code = REVERSE + COLORS[color_name]
            color_label = f"reverse:{color_name}"
        else:
            # Tier 3: High-contrast fg/bg combinations
            fg_bg_idx = adjusted_idx - (palette_size * 2)
            if fg_bg_idx < fg_bg_count:
                ansi_code, color_desc = FG_BG_COMBOS[fg_bg_idx]
                color_label = f"fg/bg:{color_desc}"
            else:
                # Wrap around fallback
                color_idx = adjusted_idx % palette_size
                color_name = basic_colors[color_idx]
                ansi_code = COLORS[color_name]
                color_label = f"wrap:{color_name}"

        # Create a compact label showing value index, color index, and description
        label = f"v{idx:<2d} c{adjusted_idx:<2d} {color_label}"
        result.append((label, ansi_code))

    return result


def main():
    parser = argparse.ArgumentParser(description="Test color palette combinations")
    parser.add_argument(
        "--values",
        type=int,
        default=30,
        help="Number of unique values to display (default: 30)",
    )
    parser.add_argument(
        "--columns",
        type=int,
        default=3,
        help="Number of columns to display (default: 3)",
    )
    args = parser.parse_args()

    if args.values < 1:
        print("Error: --values must be at least 1", file=sys.stderr)
        sys.exit(1)
    if args.columns < 1:
        print("Error: --columns must be at least 1", file=sys.stderr)
        sys.exit(1)

    print(f"\nColor Palette Test: {args.values} values x {args.columns} columns")
    print("=" * 100)

    # Create color maps for each column with different seeds
    columns = []
    for col_idx in range(args.columns):
        seed = col_idx  # Adjacent seeds for testing
        color_map = build_color_map(args.values, seed=seed)
        columns.append(color_map)

    # Print column headers
    header_parts = [
        f"Column {col_idx + 1} (seed={col_idx})".ljust(50) for col_idx in range(args.columns)
    ]
    print("  ".join(header_parts))
    print("-" * (52 * args.columns))

    # Print values
    for val_idx in range(args.values):
        row_parts = []
        for col_idx in range(args.columns):
            label, ansi_code = columns[col_idx][val_idx]
            # Show label with sample text in color
            display = f"{label:38s} sample-123"
            colored = f"{ansi_code}{display}{RESET}"
            row_parts.append(colored)
        print("  ".join(row_parts))

    print("\n" + "=" * 100)
    print(f"Total capacity: {len(FG_BG_COMBOS) + 16} distinct appearances\n")


if __name__ == "__main__":
    main()
