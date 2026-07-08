#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Generate configs/host-lists/mac/<group>.list from local ronin_puppet inventory.d.

Reads inventory YAML files from a local ronin_puppet checkout, parses all Mac groups,
and writes one host list file per group.

Usage:
    uv run tools/generate_mac_host_list.py
    uv run tools/generate_mac_host_list.py --force
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from host_list_headers import local_source_revision, write_if_changed
from natural_sort import natural_key

REPO = "mozilla-platform-ops/ronin_puppet"
INVENTORY_DIR = "inventory.d"
SOURCE_REPO_PATH = Path.home() / "git" / "ronin_puppet"
OUTPUT_DIR = Path("configs/host-lists/mac")
IGNORE_FILES = {"services.yaml"}


def inventory_files() -> list[Path]:
    """Return inventory YAML files from the local source checkout."""
    source_dir = SOURCE_REPO_PATH / INVENTORY_DIR
    if not source_dir.is_dir():
        raise FileNotFoundError(f"missing inventory directory: {source_dir}")
    return sorted(
        (path for path in source_dir.glob("*.yaml") if path.name not in IGNORE_FILES),
        key=lambda path: path.name,
    )


def parse_inventory(raw_yaml: str) -> list[dict]:
    """Parse inventory YAML and return the groups list."""
    data = yaml.safe_load(raw_yaml)
    return data.get("groups", [])


def generate_group_file(group: dict, *, inventory_name: str, source_revision: str) -> str | None:
    """Build file content for one inventory group."""
    targets = group.get("targets") or []
    facts = group.get("facts") or {}
    puppet_role = facts.get("puppet_role", "")

    lines: list[str] = [
        "# #############################################################",
        "# THIS FILE IS AUTO-GENERATED. DO NOT EDIT MANUALLY.",
        f"# Source revision: {source_revision}",
        f"# Source:    mozilla-platform-ops/ronin_puppet inventory.d/{inventory_name}",
        "# Regenerate: uv run tools/generate_mac_host_list.py",
        "# #############################################################",
        "",
        f"# inventory: {inventory_name}",
    ]

    if puppet_role:
        lines.append(f"# puppet_role: {puppet_role}")

    lines.append("")

    filtered = [t for t in targets if not str(t).endswith(".local")]
    if not filtered:
        return None

    sorted_targets = sorted(filtered, key=natural_key)
    lines.extend(str(t) for t in sorted_targets)

    lines.append("")

    return "\n".join(lines)


def main() -> None:
    """Fetch inventory files and write one .list file per group to OUTPUT_DIR."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if output files were updated within the last 60 minutes",
    )
    args = parser.parse_args()

    if not args.force and OUTPUT_DIR.exists():
        # Check the most recently modified .list file
        list_files = list(OUTPUT_DIR.glob("*.list"))
        if list_files:
            newest_mtime = max(f.stat().st_mtime for f in list_files)
            age_seconds = time.time() - newest_mtime
            if age_seconds < 3600:
                remaining = int((3600 - age_seconds) / 60)
                print(
                    f"Skipping: {OUTPUT_DIR}/*.list was updated {int(age_seconds / 60)}m ago "
                    f"(use --force to regenerate, or wait {remaining}m)",
                    file=sys.stderr,
                )
                return

    print(f"Reading inventory files from {SOURCE_REPO_PATH / INVENTORY_DIR}...", file=sys.stderr)
    try:
        source_files = inventory_files()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Found {len(source_files)} inventory files: "
        f"{', '.join(path.name for path in source_files)}",
        file=sys.stderr,
    )

    source_revision = local_source_revision(cwd=SOURCE_REPO_PATH, repo=REPO)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_hosts = 0
    total_groups = 0

    for source_file in source_files:
        print(f"Reading {source_file}...", file=sys.stderr)
        raw_yaml = source_file.read_text(encoding="utf-8")

        groups = parse_inventory(raw_yaml)
        for group in groups:
            group_name = group.get("name", "unknown")
            content = generate_group_file(
                group,
                inventory_name=source_file.name,
                source_revision=source_revision,
            )
            out_path = OUTPUT_DIR / f"{group_name}.list"

            if content is None:
                if out_path.exists():
                    out_path.unlink()
                    print(f"  Removed {out_path} (empty after filtering)", file=sys.stderr)
                else:
                    print(f"  Skipped {group_name} (empty after filtering)", file=sys.stderr)
                continue

            host_count = len(group.get("targets") or [])
            total_hosts += host_count
            total_groups += 1
            if write_if_changed(out_path, content, force=args.force):
                print(f"  Wrote {host_count} hosts to {out_path}", file=sys.stderr)
            else:
                print(f"  Unchanged {host_count} hosts in {out_path}", file=sys.stderr)

    print(
        f"Done: {total_groups} group files, {total_hosts} total hosts in {OUTPUT_DIR}/",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
