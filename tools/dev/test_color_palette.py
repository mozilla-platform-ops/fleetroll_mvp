#!/usr/bin/env python3
"""Test program to visualize color palette combinations."""

from __future__ import annotations

import argparse
import sys

from fleetroll.commands.monitor.colors import (
    ANSI_RESET,
    BASIC_COLORS,
    EXTENDED_COLORS,
    FG_BG_COMBOS,
    build_color_mapping,
    get_ansi_code,
)


def build_color_map(
    value_count: int,
    *,
    seed: int = 0,
) -> list[tuple[str, str]]:
    """Build a color map using the shared color palette logic.

    Adjacent seeds are automatically spread for maximum visual distinction.

    Returns list of (label, ansi_code) tuples.
    """
    # Build combined palette: 7 standard + 8 extended = 15 colors
    palette_size = len(BASIC_COLORS) + len(EXTENDED_COLORS)  # 15
    total_capacity = palette_size + len(FG_BG_COMBOS)  # 15 + 19 = 34

    # Create synthetic values for testing
    values = [f"value_{idx}" for idx in range(value_count)]

    # Get color mapping using shared algorithm
    color_mapping = build_color_mapping(
        values,
        total_capacity=total_capacity,
        seed=seed,
    )

    result = []
    for idx, value in enumerate(values):
        color_index = color_mapping[value]
        ansi_code = get_ansi_code(
            color_index,
            palette_size=palette_size,
            extended_support=True,
        )

        # Determine color label for display
        if color_index < len(BASIC_COLORS):
            color_name = BASIC_COLORS[color_index]
            color_label = f"basic:{color_name}"
        elif color_index < len(BASIC_COLORS) + len(EXTENDED_COLORS):
            extended_idx = color_index - len(BASIC_COLORS)
            color_name, _ = EXTENDED_COLORS[extended_idx]
            color_label = f"256:{color_name}"
        else:
            fg_bg_idx = color_index - palette_size
            if fg_bg_idx < len(FG_BG_COMBOS):
                _, _, combo_desc = FG_BG_COMBOS[fg_bg_idx]
                color_label = f"fg/bg:{combo_desc}"
            else:
                color_label = "wrapped"

        # Create a compact label showing value index, color index, and description
        label = f"v{idx:<2d} c{color_index:<2d} {color_label}"
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
            colored = f"{ansi_code}{display}{ANSI_RESET}"
            row_parts.append(colored)
        print("  ".join(row_parts))

    print("\n" + "=" * 100)
    palette_colors = len(BASIC_COLORS) + len(EXTENDED_COLORS)
    fg_bg_count = len(FG_BG_COMBOS)
    total = palette_colors + fg_bg_count
    print(
        f"Total capacity: {total} distinct appearances "
        f"({palette_colors} palette + {fg_bg_count} fg/bg combos)\n"
        f"Palette: {len(BASIC_COLORS)} standard + {len(EXTENDED_COLORS)} extended 256-colors\n"
    )


if __name__ == "__main__":
    main()
