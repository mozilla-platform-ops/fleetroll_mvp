"""Shared color palette definitions and mapping logic for monitor display.

This module provides format-agnostic color palette definitions and mapping
algorithms that can be used by both curses-based displays and ANSI terminal
output tools.
"""

from __future__ import annotations

from collections.abc import Iterable

# Basic 7 standard colors (available in most terminals)
BASIC_COLORS = [
    "blue",
    "cyan",
    "green",
    "magenta",
    "yellow",
    "red",
    "white",
]

# Extended 256-color palette (8 additional colors)
# Format: (name, 256-color-code)
EXTENDED_COLORS = [
    ("orange", 208),
    ("purple", 129),
    ("pink", 205),
    ("teal", 33),
    ("maroon", 160),
    ("gold", 220),
    ("forest-green", 28),
    ("orange-red", 214),
]

# ANSI escape codes for basic colors
ANSI_BASIC_COLORS = {
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "magenta": "\033[35m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "white": "\033[37m",
}

# ANSI control codes
ANSI_RESET = "\033[0m"
ANSI_REVERSE = "\033[7m"

# High-contrast foreground/background combinations (19 pairs)
# Format: (fg_color, bg_color, description)
# Comments indicate UNREADABLE combinations that are excluded
FG_BG_COMBOS = [
    # ("yellow", "blue", "yellow/blue"),  # c16 UNREADABLE
    ("black", "cyan", "black/cyan"),
    ("black", "green", "black/green"),
    ("yellow", "magenta", "yellow/magenta"),
    ("black", "yellow", "black/yellow"),
    ("white", "red", "white/red"),
    # ("white", "blue", "white/blue"),  # c22 UNREADABLE
    ("cyan", "black", "cyan/black"),
    ("green", "black", "green/black"),
    ("yellow", "black", "yellow/black"),
    ("red", "black", "red/black"),
    ("magenta", "black", "magenta/black"),
    # ("blue", "yellow", "blue/yellow"),  # c28 UNREADABLE
    ("red", "cyan", "red/cyan"),
    ("cyan", "magenta", "cyan/magenta"),
    # ("blue", "white", "blue/white"),  # c31 UNREADABLE
    # New combos with red/white backgrounds
    ("red", "green", "red/green"),
    ("green", "red", "green/red"),
    ("cyan", "red", "cyan/red"),
    ("magenta", "white", "magenta/white"),
    ("red", "white", "red/white"),
    # ("green", "white", "green/white"),  # c33 UNREADABLE
    ("blue", "red", "blue/red"),
    # ("blue", "cyan", "blue/cyan"),  # c34 UNREADABLE
    ("yellow", "red", "yellow/red"),
    # New fg/bg combinations
    ("black", "magenta", "black/magenta"),
    ("white", "black", "white/black"),
    ("blue", "magenta", "blue/magenta"),
    ("green", "magenta", "green/magenta"),
    ("magenta", "green", "magenta/green"),
    ("blue", "black", "blue/black"),
]

# State-indicator fg/bg sets — combos using these are excluded from categorical palettes
# to avoid confusion with threshold-colored columns (uptime, last_ok, etc.)
STATE_INDICATOR_FG = {"red", "yellow", "green"}
STATE_INDICATOR_BG = {"black", "white"}

# Extended fg/bg combos: 256-color foreground on basic background (pairs 52+)
# Format: (fg_color_name, 256_color_code, bg_color_name, description)
EXTENDED_FG_BG_COMBOS = [
    # orange (208)
    ("orange", 208, "cyan", "orange/cyan"),
    ("orange", 208, "blue", "orange/blue"),
    ("orange", 208, "black", "orange/black"),
    # purple (129)
    ("purple", 129, "cyan", "purple/cyan"),
    ("purple", 129, "yellow", "purple/yellow"),
    ("purple", 129, "green", "purple/green"),
    # pink (205)
    ("pink", 205, "cyan", "pink/cyan"),
    ("pink", 205, "blue", "pink/blue"),
    ("pink", 205, "black", "pink/black"),
    # teal (33)
    ("teal", 33, "white", "teal/white"),
    ("teal", 33, "black", "teal/black"),
    ("teal", 33, "yellow", "teal/yellow"),
    # maroon (160)
    ("maroon", 160, "cyan", "maroon/cyan"),
    ("maroon", 160, "yellow", "maroon/yellow"),
    ("maroon", 160, "green", "maroon/green"),
    # gold (220)
    ("gold", 220, "black", "gold/black"),
    ("gold", 220, "magenta", "gold/magenta"),
    ("gold", 220, "red", "gold/red"),
    # forest-green (28)
    ("forest-green", 28, "cyan", "forest-green/cyan"),
    ("forest-green", 28, "yellow", "forest-green/yellow"),
    ("forest-green", 28, "white", "forest-green/white"),
    # orange-red (214)
    ("orange-red", 214, "cyan", "orange-red/cyan"),
    ("orange-red", 214, "white", "orange-red/white"),
    ("orange-red", 214, "black", "orange-red/black"),
]


def get_categorical_combos(
    *,
    include_extended: bool = True,
) -> list[tuple[int, str, str, str]]:
    """Return FG_BG combo indices suitable for categorical columns.

    Excludes combos where fg is a state-indicator color (red/yellow/green)
    and bg is black or white (would mimic plain state-indicator appearance).

    Returns list of (pair_number, fg, bg, desc) tuples.
    Pair numbers: 27+ for basic FG_BG_COMBOS, 52+ for extended.
    """
    result: list[tuple[int, str, str, str]] = []
    for i, (fg, bg, desc) in enumerate(FG_BG_COMBOS):
        if fg in STATE_INDICATOR_FG and bg in STATE_INDICATOR_BG:
            continue
        result.append((27 + i, fg, bg, desc))
    if include_extended:
        for i, (fg_name, _, bg, desc) in enumerate(EXTENDED_FG_BG_COMBOS):
            result.append((52 + i, fg_name, bg, desc))
    return result


# ANSI background color codes
ANSI_BG_COLORS = {
    "black": "40",
    "red": "41",
    "green": "42",
    "yellow": "43",
    "blue": "44",
    "magenta": "45",
    "cyan": "46",
    "white": "47",
}

# ANSI foreground color codes
ANSI_FG_COLORS = {
    "black": "30",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
}


def build_color_mapping(
    values: Iterable[str],
    *,
    total_capacity: int,
    seed: int = 0,
) -> dict[str, int]:
    """Build a color index mapping for categorical values.

    This is the core algorithm used by both curses and ANSI output.
    Maps each unique value to a color index using a spreading algorithm
    that maximizes visual distinction between adjacent seeds.

    Args:
        values: Unique values to assign colors to
        total_capacity: Total number of colors available (palette + fg/bg combos)
        seed: Column index (0, 1, 2...) - automatically spread for distinction

    Returns:
        Dictionary mapping value to color index (0-based)
    """
    ordered = sorted(values)
    mapping: dict[str, int] = {}

    # Spread adjacent seeds for maximum visual distinction
    # Using 11 (prime) ensures good distribution across total capacity
    spread_factor = 11
    effective_seed = (seed * spread_factor) % total_capacity

    for idx, value in enumerate(ordered):
        adjusted_idx = (idx + effective_seed) % total_capacity
        mapping[value] = adjusted_idx

    return mapping


def get_ansi_code(
    color_index: int,
    *,
    palette_size: int,
    extended_support: bool = True,
) -> str:
    """Convert a color index to an ANSI escape code.

    Args:
        color_index: Color index from build_color_mapping()
        palette_size: Number of basic palette colors (7 or 15)
        extended_support: Whether to use 256-color codes for extended palette

    Returns:
        ANSI escape code string
    """
    # Determine the actual palette size based on extended support
    actual_palette_size = palette_size

    if color_index < len(BASIC_COLORS):
        # Tier 1: Basic colors
        color_name = BASIC_COLORS[color_index]
        return ANSI_BASIC_COLORS[color_name]
    if extended_support and color_index < len(BASIC_COLORS) + len(EXTENDED_COLORS):
        # Extended 256 colors
        extended_idx = color_index - len(BASIC_COLORS)
        _, color_code = EXTENDED_COLORS[extended_idx]
        return f"\033[38;5;{color_code}m"
    # Tier 2: High-contrast fg/bg combinations
    fg_bg_idx = color_index - actual_palette_size
    if fg_bg_idx < len(FG_BG_COMBOS):
        fg_color, bg_color, _ = FG_BG_COMBOS[fg_bg_idx]
        fg_code = ANSI_FG_COLORS[fg_color]
        bg_code = ANSI_BG_COLORS[bg_color]
        return f"\033[{fg_code};{bg_code}m"
    # Wrap around to basic colors if we exceed capacity
    wrapped_idx = color_index % len(BASIC_COLORS)
    color_name = BASIC_COLORS[wrapped_idx]
    return ANSI_BASIC_COLORS[color_name]


def get_curses_attr(
    color_index: int,
    curses_mod,
    *,
    palette_size: int,
    fg_bg_pair_start: int = 27,
    base_attr: int = 0,
) -> int:
    """Convert a color index to a curses attribute.

    Args:
        color_index: Color index from build_color_mapping()
        curses_mod: The curses module (must be initialized)
        palette_size: Number of basic palette colors available
        fg_bg_pair_start: Starting pair number for fg/bg combinations
        base_attr: Base attribute to OR with the color

    Returns:
        Curses attribute (color_pair | base_attr)
    """
    if color_index < palette_size:
        # Tier 1: Basic palette colors (pairs 7-26 depending on palette)
        # Assuming pairs are initialized starting at pair 7 for basic colors
        pair_offset = 7  # Basic colors start at pair 7 in display.py
        color_pair_num = pair_offset + color_index
        return curses_mod.color_pair(color_pair_num) | base_attr
    # Tier 2: High-contrast fg/bg combinations
    fg_bg_idx = color_index - palette_size
    pair_num = fg_bg_pair_start + fg_bg_idx
    return curses_mod.color_pair(pair_num) | base_attr


def build_color_map_ansi(
    values: Iterable[str],
    *,
    extended_support: bool = True,
    seed: int = 0,
) -> list[tuple[str, str]]:
    """Build a color map with ANSI codes for terminal output.

    This is a convenience function for tools that need direct ANSI codes.

    Args:
        values: Unique values to assign colors to
        extended_support: Whether to use 256-color codes
        seed: Column index for color spreading

    Returns:
        List of (label, ansi_code) tuples for each value
    """
    palette_size = len(BASIC_COLORS) + (len(EXTENDED_COLORS) if extended_support else 0)
    total_capacity = palette_size + len(FG_BG_COMBOS)

    # Get the color mapping
    color_mapping = build_color_mapping(
        values,
        total_capacity=total_capacity,
        seed=seed,
    )

    # Convert to ANSI codes with labels
    result = []
    for value, color_index in color_mapping.items():
        ansi_code = get_ansi_code(
            color_index,
            palette_size=palette_size,
            extended_support=extended_support,
        )
        result.append((value, ansi_code))

    return result
