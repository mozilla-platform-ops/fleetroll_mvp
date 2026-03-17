#!/usr/bin/env python3
"""Generate per-OS all.list files and a combined base all.list.

Does two things:
1. In each OS subdir (linux/, mac/), combine all .list files into all.list
2. Combine OS all.list files into one all.list at the base, with section comments

Usage:
    uv run tools/generate_all_host_lists.py
"""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

# Allow importing sibling tools modules
sys.path.insert(0, str(Path(__file__).parent))
from natural_sort import natural_key

BASE_DIR = Path("configs/host-lists")
OS_DIRS = ["linux", "mac", "windows"]
OUTPUT_PATH = BASE_DIR / "all.list"


def read_hosts(list_file: Path) -> list[str]:
    """Read hosts from a list file, expanding short names via # fqdn: directives."""
    fqdn_suffix: str | None = None
    hosts = []
    for line in list_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            m = re.match(r"^#\s*fqdn:\s*(\S+)", stripped)
            if m:
                suffix = m.group(1)
                fqdn_suffix = suffix if suffix.startswith(".") else f".{suffix}"
            continue
        hosts.append(stripped)
    if fqdn_suffix:
        hosts = [h + fqdn_suffix if "." not in h else h for h in hosts]
    return hosts


def generate_os_all_list(os_dir: Path) -> Path:
    """Combine all .list files in os_dir (excluding all.list) into os_dir/all.list.

    Returns the path to the generated file.
    """
    output = os_dir / "all.list"

    list_files = sorted(
        (f for f in os_dir.glob("*.list") if f.name != "all.list"),
        key=lambda f: f.stat().st_size,
        reverse=True,
    )

    if not list_files:
        print(f"Warning: no .list files found in {os_dir}", file=sys.stderr)
        return output

    # Collect hosts with deduplication; track which source contributed each host
    seen: set[str] = set()
    # (host, source_name) for all unique hosts; duplicates tracked separately
    host_source: list[tuple[str, str]] = []
    duplicates: list[tuple[str, str]] = []

    for list_file in list_files:
        hosts = read_hosts(list_file)
        for h in hosts:
            if h not in seen:
                seen.add(h)
                host_source.append((h, list_file.name))
            else:
                duplicates.append((h, list_file.name))

    # Globally sort all unique hosts by natural key so ordering is correct
    # across sources (e.g. host-44 before host-104 regardless of which file
    # each came from).
    host_source.sort(key=lambda hs: natural_key(hs[0]))

    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# #############################################################",
        "# THIS FILE IS AUTO-GENERATED. DO NOT EDIT MANUALLY.",
        f"# Generated: {timestamp}",
        f"# Source:    configs/host-lists/{os_dir.name}/*.list (excluding all.list)",
        "# Regenerate: uv run tools/generate_all_host_lists.py",
        "# #############################################################",
        "",
    ]

    # Emit hosts in global natural-sort order; annotate source changes inline.
    current_source: str | None = None
    for host, source_name in host_source:
        if source_name != current_source:
            if current_source is not None:
                lines.append("")
            lines.append(f"# source: {source_name}")
            current_source = source_name
        lines.append(host)

    if duplicates:
        lines.append("")
        lines.append("# duplicates (already listed above):")
        for host, source_name in sorted(duplicates, key=lambda hs: natural_key(hs[0])):
            lines.append(f"# {host}  (from {source_name})")

    lines.append("")

    content = "\n".join(lines)
    output.write_text(content, encoding="utf-8")

    print(f"Wrote {len(host_source)} hosts to {output}", file=sys.stderr)
    return output


def generate_base_all_list(os_all_lists: list[tuple[str, Path]]) -> None:
    """Combine per-OS all.list files into the base all.list."""
    seen: set[str] = set()
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        "# #############################################################",
        "# THIS FILE IS AUTO-GENERATED. DO NOT EDIT MANUALLY.",
        f"# Generated: {timestamp}",
        "# Source:    configs/host-lists/{linux,mac,windows}/all.list",
        "# Regenerate: uv run tools/generate_all_host_lists.py",
        "# #############################################################",
        "",
    ]

    for os_name, list_path in os_all_lists:
        if not list_path.exists():
            print(f"Warning: {list_path} does not exist, skipping", file=sys.stderr)
            continue

        hosts = read_hosts(list_path)
        new_hosts = [h for h in hosts if h not in seen]
        duplicate_hosts = [h for h in hosts if h in seen]

        lines.append(f"# {os_name}")
        for host in new_hosts:
            seen.add(host)
            lines.append(host)

        if duplicate_hosts:
            lines.append("# duplicates (also in a previous section):")
            lines.extend(f"# {host}" for host in duplicate_hosts)

        lines.append("")

    content = "\n".join(lines)
    OUTPUT_PATH.write_text(content, encoding="utf-8")

    host_lines = [ln for ln in content.splitlines() if ln and not ln.startswith("#")]
    print(f"Wrote {len(host_lines)} hosts to {OUTPUT_PATH}", file=sys.stderr)


def main() -> None:
    # Step 1: Generate per-OS all.list for linux and mac
    os_all_lists: list[tuple[str, Path]] = []

    for os_name in OS_DIRS:
        os_dir = BASE_DIR / os_name
        if not os_dir.is_dir():
            print(f"Warning: {os_dir} does not exist, skipping", file=sys.stderr)
            continue
        all_list = generate_os_all_list(os_dir)
        os_all_lists.append((os_name, all_list))

    # Step 2: Generate base all.list
    generate_base_all_list(os_all_lists)


if __name__ == "__main__":
    main()
