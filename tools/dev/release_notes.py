"""Release notes generator for fleetroll.

Auto-detects version ranges from pyproject.toml commits, fetches closed beads,
cross-references with git commits, and generates per-release draft markdown.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VersionRange:
    version: str
    from_sha: str | None  # None means "from the beginning"
    to_sha: str
    from_date: str | None
    to_date: str


SECTION_ORDER = ["feature", "bug", "task", "docs", "epic", "chore"]
SECTION_LABELS = {
    "feature": "Features",
    "bug": "Bug Fixes",
    "task": "Tasks",
    "docs": "Documentation",
    "epic": "Epics",
    "chore": "Chores",
}


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def extract_version_from_toml(content: str) -> str | None:
    """Parse version string from pyproject.toml content."""
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    return match.group(1) if match else None


def filter_beads_by_date(beads: list[dict], start: str | None, end: str) -> list[dict]:
    """Filter beads to those closed within [start, end] date range.

    Dates are compared as ISO strings (date portion only).
    end is inclusive; start is exclusive (we want beads closed AFTER the previous release).
    If start is None, include everything up to end.
    """
    end_date = end[:10]
    result = []
    for bead in beads:
        closed_at = bead.get("closed_at")
        if not closed_at:
            continue
        closed_date = closed_at[:10]
        if start is not None:
            start_date = start[:10]
            if closed_date <= start_date:
                continue
        if closed_date <= end_date:
            result.append(bead)
    return result


def group_beads_by_type(beads: list[dict]) -> dict[str, list[dict]]:
    """Return dict of issue_type -> list of beads, sorted by priority then title."""
    groups: dict[str, list[dict]] = {}
    for bead in beads:
        btype = bead.get("issue_type", "task")
        groups.setdefault(btype, []).append(bead)
    for btype, bead_list in groups.items():
        bead_list.sort(key=lambda b: (b.get("priority", 99), b.get("title", "")))
    return groups


def parse_git_log_line(line: str) -> dict | None:
    """Parse a git log line in format 'sha|date|subject'."""
    parts = line.split("|", 2)
    if len(parts) != 3:
        return None
    sha, date, subject = parts
    return {"sha": sha.strip(), "date": date.strip(), "subject": subject.strip()}


def extract_bead_id(subject: str) -> str | None:
    """Extract mvp-xxx bead ID from a commit subject line."""
    match = re.search(r"\((mvp-[a-z0-9]+)\)", subject)
    return match.group(1) if match else None


def classify_commits(commits: list[dict], bead_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """Split commits into (covered, orphan) based on bead ID references.

    covered = commit references a bead in bead_ids
    orphan  = commit does not reference any bead in bead_ids
    """
    covered = []
    orphans = []
    for commit in commits:
        bead_id = extract_bead_id(commit["subject"])
        if bead_id and bead_id in bead_ids:
            covered.append(commit)
        else:
            orphans.append(commit)
    return covered, orphans


def render_bead_line(bead: dict) -> str:
    """Render a single bead as a markdown bullet."""
    title = bead.get("title", "(no title)")
    bead_id = bead.get("id", "")
    reason = bead.get("close_reason") or "(no reason provided)"
    if len(reason) > 120:
        reason = reason[:117] + "..."
    return f"- **{title}** ({bead_id}) — {reason}"


def render_commit_line(commit: dict) -> str:
    """Render a single orphan commit as a markdown bullet."""
    short_sha = commit["sha"][:7]
    subject = commit["subject"]
    return f"- `{short_sha}` {subject}"


def render_markdown(
    version: str,
    vrange: VersionRange,
    grouped_beads: dict[str, list[dict]],
    orphan_commits: list[dict],
    total_commits: int,
) -> str:
    """Render the full markdown string for a version's release notes."""
    total_beads = sum(len(v) for v in grouped_beads.values())
    orphan_count = len(orphan_commits)

    from_sha_display = vrange.from_sha[:7] if vrange.from_sha else "initial"
    to_sha_display = vrange.to_sha[:7]
    from_date_display = vrange.from_date[:10] if vrange.from_date else "start"
    to_date_display = vrange.to_date[:10]

    lines = [
        f"# v{version} Release Notes (DRAFT)",
        "",
        f"**Range:** `{from_sha_display}..{to_sha_display}` "
        f"({from_date_display} to {to_date_display})",
        f"**Beads closed:** {total_beads} | "
        f"**Commits:** {total_commits} | "
        f"**Orphan commits:** {orphan_count}",
        "",
    ]

    for btype in SECTION_ORDER:
        beads = grouped_beads.get(btype, [])
        if not beads:
            continue
        label = SECTION_LABELS.get(btype, btype.capitalize())
        lines.append(f"## {label}")
        lines.extend(render_bead_line(bead) for bead in beads)
        lines.append("")

    if orphan_commits:
        lines.append("## Orphan Commits")
        lines.append("")
        lines.append("These commits don't reference a bead. Include or discard as appropriate.")
        lines.append("")
        lines.extend(render_commit_line(commit) for commit in orphan_commits)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subprocess-backed functions
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def detect_version_ranges() -> list[VersionRange]:
    """Walk git log for pyproject.toml, detect version bumps, return ranges."""
    result = _run(["git", "log", "--format=%H %aI", "--", "pyproject.toml"])
    commits = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        sha, date = parts[0], parts[1]
        toml_result = _run(["git", "show", f"{sha}:pyproject.toml"], check=False)
        if toml_result.returncode != 0:
            continue
        version = extract_version_from_toml(toml_result.stdout)
        if version:
            commits.append({"sha": sha, "date": date, "version": version})

    if not commits:
        return []

    # commits is newest-first; group by version changes
    ranges: list[VersionRange] = []
    # The most recent commit is commits[0]; oldest is commits[-1]
    prev_version = None
    for i, commit in enumerate(commits):
        version = commit["version"]
        if version == prev_version:
            continue
        # Version changed at this commit
        to_sha = commit["sha"]
        to_date = commit["date"]
        if i == 0:
            # This is the HEAD version — handled as "unreleased" separately
            prev_version = version
            continue
        # The range for this version: from the commit after the previous version bump
        # to_sha = this commit (the version bump commit for `version`)
        # from_sha = the commit just before this one in the log (i.e. commits[i-1] sha)
        # But actually: the range is from the previous version's commit (exclusive) to this commit
        # In git terms: prev_commit..this_commit
        prev_commit = commits[i - 1]
        from_sha = prev_commit["sha"]
        from_date = prev_commit["date"]
        ranges.append(
            VersionRange(
                version=version,
                from_sha=from_sha,
                to_sha=to_sha,
                from_date=from_date,
                to_date=to_date,
            )
        )
        prev_version = version

    # Handle the oldest range: from initial commit to first version bump
    if commits:
        oldest = commits[-1]
        # If there's a range that ends at the same commit as the oldest pyproject.toml commit
        # then we need a range from None to this commit
        # Check if we have a range ending at oldest["sha"]
        existing_to_shas = {r.to_sha for r in ranges}
        if oldest["sha"] not in existing_to_shas:
            ranges.append(
                VersionRange(
                    version=oldest["version"],
                    from_sha=None,
                    to_sha=oldest["sha"],
                    from_date=None,
                    to_date=oldest["date"],
                )
            )

    # Also handle HEAD (unreleased commits after the latest version bump)
    if commits:
        latest_bump = commits[0]
        head_result = _run(["git", "rev-parse", "HEAD"])
        head_sha = head_result.stdout.strip()
        if head_sha != latest_bump["sha"]:
            # There are commits after the latest version bump
            head_date_result = _run(["git", "log", "-1", "--format=%aI", "HEAD"])
            head_date = head_date_result.stdout.strip()
            ranges.insert(
                0,
                VersionRange(
                    version="unreleased",
                    from_sha=latest_bump["sha"],
                    to_sha=head_sha,
                    from_date=latest_bump["date"],
                    to_date=head_date,
                ),
            )

    # Sort: unreleased first, then newest version first
    def sort_key(r: VersionRange):
        if r.version == "unreleased":
            return (0, "")
        return (1, [int(x) for x in r.version.split(".")])

    ranges.sort(key=sort_key)
    return ranges


def fetch_closed_beads() -> list[dict]:
    """Fetch all closed beads via br CLI. Returns [] if br not available."""
    try:
        result = _run(
            ["br", "list", "--status=closed", "--format", "json", "--limit", "0"],
            check=False,
        )
        if result.returncode != 0:
            print(
                f"Warning: br list failed (exit {result.returncode}): {result.stderr.strip()}",
                file=sys.stderr,
            )
            return []
        return json.loads(result.stdout)
    except FileNotFoundError:
        print("Warning: br not found on PATH. Skipping bead data.", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"Warning: Failed to parse br output: {e}", file=sys.stderr)
        return []


def fetch_git_commits(from_sha: str | None, to_sha: str) -> list[dict]:
    """Fetch git commits in the given range."""
    if from_sha is None:
        rev_range = to_sha
    else:
        rev_range = f"{from_sha}..{to_sha}"

    result = _run(
        ["git", "log", "--format=%H|%aI|%s", rev_range],
        check=False,
    )
    if result.returncode != 0:
        return []

    commits = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = parse_git_log_line(line)
        if parsed:
            commits.append(parsed)
    return commits


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def generate_notes_for_range(
    vrange: VersionRange, all_beads: list[dict], output_dir: Path, *, force: bool
) -> str:
    """Generate notes for one version range. Returns the output file path."""
    version = vrange.version
    filename = f"v{version}.md" if version != "unreleased" else "unreleased.md"
    output_path = output_dir / filename

    if output_path.exists() and not force:
        print(f"  Skipping {filename} (already exists, use --force to overwrite)")
        return str(output_path)

    commits = fetch_git_commits(vrange.from_sha, vrange.to_sha)
    beads_in_range = filter_beads_by_date(all_beads, vrange.from_date, vrange.to_date)
    grouped = group_beads_by_type(beads_in_range)

    bead_ids = {b["id"] for b in beads_in_range}
    _, orphan_commits = classify_commits(commits, bead_ids)

    md = render_markdown(version, vrange, grouped, orphan_commits, len(commits))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md)
    print(f"  Wrote {output_path}")
    print(md)
    return str(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate release notes from git history and closed beads."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate notes for all detected version ranges.",
    )
    parser.add_argument(
        "--version",
        metavar="VERSION",
        help="Generate notes for a specific version (e.g. 0.2.3).",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default="docs/release-notes/generated",
        help="Directory to write output files (default: docs/release-notes/generated).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output files.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    print("Detecting version ranges from git history...")
    ranges = detect_version_ranges()
    if not ranges:
        print("No version ranges detected.", file=sys.stderr)
        return 1

    print("Fetching closed beads...")
    all_beads = fetch_closed_beads()

    if args.version:
        target = args.version.lstrip("v")
        matching = [r for r in ranges if r.version == target]
        if not matching:
            available = ", ".join(r.version for r in ranges)
            print(
                f"Version '{target}' not found. Available: {available}",
                file=sys.stderr,
            )
            return 1
        ranges_to_process = matching
    elif args.all:
        ranges_to_process = ranges
    # Default: generate for the latest unreleased (or newest version if all released)
    elif (ranges and ranges[0].version == "unreleased") or ranges:
        ranges_to_process = [ranges[0]]
    else:
        print("No ranges to process.", file=sys.stderr)
        return 1

    for vrange in ranges_to_process:
        print(f"\nGenerating notes for v{vrange.version}...")
        generate_notes_for_range(vrange, all_beads, output_dir, force=args.force)

    return 0


if __name__ == "__main__":
    sys.exit(main())
