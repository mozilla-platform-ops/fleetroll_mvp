#!/usr/bin/env python3
"""Natural sort utility for sorting lines with embedded numbers.

Sorts lines treating numbers as integers rather than strings.
For example: host-1, host-2, host-10 instead of host-1, host-10, host-2.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def natural_key(s: str) -> list[int | str]:
    """Split string into list of strings and integers for natural sort.

    Args:
        s: String to split

    Returns:
        List of alternating strings and integers for sorting
    """
    return [int(text) if text.isdigit() else text for text in re.split(r"(\d+)", s)]


def main() -> None:
    """Sort lines from file or stdin using natural sort order."""
    parser = argparse.ArgumentParser(
        description="Sort lines with natural ordering (treats numbers as integers).",
        epilog="Example: natural-sort host-list.txt",
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=Path,
        help="File to sort (reads from stdin if not provided)",
    )
    args = parser.parse_args()

    try:
        if args.file:
            if not args.file.exists():
                print(f"Error: File not found: {args.file}", file=sys.stderr)
                sys.exit(1)
            with args.file.open(encoding="utf-8") as f:
                lines = [line.rstrip("\n") for line in f if line.strip()]
        else:
            # Read from stdin
            lines = [line.rstrip("\n") for line in sys.stdin if line.strip()]

        lines.sort(key=natural_key)

        for line in lines:
            print(line)

    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
