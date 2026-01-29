#!/usr/bin/env python3
"""Test extended 256-color palette for most distinct colors."""

# ANSI escape codes
RESET = "\033[0m"
REVERSE = "\033[7m"


def color_256(num: int, reverse: bool = False) -> str:
    """Return ANSI code for 256-color palette."""
    prefix = REVERSE if reverse else ""
    return f"{prefix}\033[38;5;{num}m"


# Standard 7 colors (for reference - these are already used):
# blue, cyan, green, magenta, yellow, red, white

# 8 extended colors maximally different from standard AND each other
# Chosen to fill gaps: orange, purple, pink, teal, darker variants
# Format: (color_num, name, description)
DISTINCT_COLORS = [
    (208, "bright-orange", "Vivid orange - fills orange gap"),
    (129, "bright-purple", "Bright purple - fills purple gap"),
    (205, "hot-pink", "Hot pink - fills pink gap"),
    (33, "teal", "Deep cyan/teal - distinct from cyan"),
    (160, "maroon", "Deep red/maroon - darker red"),
    (220, "gold", "Gold - distinct from yellow"),
    (28, "forest-green", "Forest green - darker green"),
    (214, "orange-red", "Orange-red - between orange/red"),
]


def main():
    print("\nExtended 256-Color Palette - Most Distinct Colors\n")
    print("=" * 100)

    # Show normal colors
    print("\nNORMAL COLORS:")
    print("-" * 100)
    for i, (color_num, name, desc) in enumerate(DISTINCT_COLORS):
        ansi = color_256(color_num)
        display = f"c{i:02d} [{color_num:3d}] {name:20s} {desc:25s} sample-text-123"
        print(f"{ansi}{display}{RESET}")

    # Show reverse colors
    print("\nREVERSE COLORS:")
    print("-" * 100)
    for i, (color_num, name, desc) in enumerate(DISTINCT_COLORS):
        ansi = color_256(color_num, reverse=True)
        display = f"c{i:02d} [{color_num:3d}] {name:20s} {desc:25s} sample-text-123"
        print(f"{ansi}{display}{RESET}")

    print("\n" + "=" * 100)
    total = len(DISTINCT_COLORS) * 2
    print(
        f"Total shown: {len(DISTINCT_COLORS)} normal + {len(DISTINCT_COLORS)} reverse = {total} colors"
    )
    print(f"Combined with standard 7 (14 with reverse) = {total + 14} total distinct colors\n")


if __name__ == "__main__":
    main()
