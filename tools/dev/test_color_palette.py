#!/usr/bin/env python3
"""Test program to visualize color palette combinations."""

from __future__ import annotations

import argparse
import sys

from fleetroll.commands.monitor.colors import (
    ANSI_BG_COLORS,
    ANSI_FG_COLORS,
    ANSI_RESET,
    BASIC_COLORS,
    EXTENDED_COLORS,
    EXTENDED_FG_BG_COMBOS,
    FG_BG_COMBOS,
    build_color_mapping,
    get_ansi_code,
    get_categorical_combos,
)


def _ansi_for_extended_fg_bg(fg_code: int, bg_name: str) -> str:
    """Return ANSI escape for a 256-color fg on a basic bg."""
    bg_code = ANSI_BG_COLORS[bg_name]
    return f"\033[38;5;{fg_code};{bg_code}m"


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
    total_capacity = palette_size + len(FG_BG_COMBOS)  # 15 + 25 = 40

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


def show_categorical(value_count: int) -> None:
    """Display only categorical combos (fg/bg pairs, no plain fg colors)."""
    cat_combos = get_categorical_combos(include_extended=True)
    total = len(cat_combos)

    print(f"\nCategorical Combos: {total} total (21 basic + 24 extended)")
    print("=" * 100)
    print("Pair  FG            BG        Description                  Sample")
    print("-" * 100)

    for pair_num, fg_name, bg_name, desc in cat_combos:
        # Build ANSI code for display
        if bg_name in ANSI_BG_COLORS and fg_name in ANSI_FG_COLORS:
            fg_code = ANSI_FG_COLORS[fg_name]
            bg_code = ANSI_BG_COLORS[bg_name]
            ansi = f"\033[{fg_code};{bg_code}m"
        else:
            # Extended 256-color fg
            combo_idx = pair_num - 52
            _, fg_code_int, _, _ = EXTENDED_FG_BG_COMBOS[combo_idx]
            ansi = _ansi_for_extended_fg_bg(fg_code_int, bg_name)

        sample = f"{ansi} {desc:28s} sample-text {ANSI_RESET}"
        print(f"  {pair_num:<4d}  {fg_name:<12s}  {bg_name:<8s}  {sample}")

    print("\n" + "=" * 100)
    print(f"Total categorical combos: {total} (21 basic + 24 extended)\n")

    # Also show a spread test with value_count values
    if value_count > 0:
        print(f"\nSpread test: {value_count} values using categorical palette")
        print("-" * 100)
        values = [f"value_{i}" for i in range(value_count)]
        color_mapping = build_color_mapping(values, total_capacity=total, seed=0)
        for val in values:
            idx = color_mapping[val]
            pair_num, fg_name, bg_name, desc = cat_combos[idx]
            if bg_name in ANSI_BG_COLORS and fg_name in ANSI_FG_COLORS:
                fg_code = ANSI_FG_COLORS[fg_name]
                bg_code = ANSI_BG_COLORS[bg_name]
                ansi = f"\033[{fg_code};{bg_code}m"
            else:
                combo_idx = pair_num - 52
                _, fg_code_int, _, _ = EXTENDED_FG_BG_COMBOS[combo_idx]
                ansi = _ansi_for_extended_fg_bg(fg_code_int, bg_name)
            print(f"  {val:12s}  -> idx {idx:<3d} {ansi} {desc:28s} {ANSI_RESET}")


def show_all_extended() -> None:
    """Display extended fg/bg combos (pairs 52+)."""
    print(f"\nExtended FG_BG_COMBOS: {len(EXTENDED_FG_BG_COMBOS)} combos (pairs 52+)")
    print("=" * 100)
    print("Pair  FG Name       256-code  BG        Description                  Sample")
    print("-" * 100)

    for i, (fg_name, fg_code_int, bg_name, desc) in enumerate(EXTENDED_FG_BG_COMBOS):
        pair_num = 52 + i
        ansi = _ansi_for_extended_fg_bg(fg_code_int, bg_name)
        sample = f"{ansi} {desc:28s} sample-text {ANSI_RESET}"
        print(f"  {pair_num:<4d}  {fg_name:<12s}  {fg_code_int:<8d}  {bg_name:<8s}  {sample}")

    print("\n" + "=" * 100)
    print(f"Total extended combos: {len(EXTENDED_FG_BG_COMBOS)}\n")


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
    parser.add_argument(
        "--categorical",
        action="store_true",
        help="Show only categorical combos (fg/bg pairs, state-indicator-safe)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show everything including extended fg/bg combos",
    )
    args = parser.parse_args()

    if args.values < 1:
        print("Error: --values must be at least 1", file=sys.stderr)
        sys.exit(1)
    if args.columns < 1:
        print("Error: --columns must be at least 1", file=sys.stderr)
        sys.exit(1)

    if args.categorical:
        show_categorical(args.values)
        return

    if args.all:
        show_all_extended()

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
    cat_basic = sum(
        1
        for fg, bg, _ in FG_BG_COMBOS
        if not (fg in {"red", "yellow", "green"} and bg in {"black", "white"})
    )
    cat_total = cat_basic + len(EXTENDED_FG_BG_COMBOS)
    print(
        f"Total capacity: {total} distinct appearances "
        f"({palette_colors} palette + {fg_bg_count} fg/bg combos)\n"
        f"Palette: {len(BASIC_COLORS)} standard + {len(EXTENDED_COLORS)} extended 256-colors\n"
        f"Categorical capacity: {cat_total} "
        f"({cat_basic} basic fg/bg + {len(EXTENDED_FG_BG_COMBOS)} extended fg/bg)\n"
    )


if __name__ == "__main__":
    main()
