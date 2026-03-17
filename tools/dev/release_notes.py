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


SECTION_ORDER = ["epic", "feature", "bug", "task", "docs", "chore"]
SECTION_LABELS = {
    "epic": "Epics",
    "feature": "Features",
    "bug": "Bug Fixes",
    "task": "Tasks",
    "docs": "Documentation",
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
    return f"- **{title}** ({bead_id})"


def render_commit_line(commit: dict) -> str:
    """Render a single orphan commit as a markdown bullet."""
    short_sha = commit["sha"][:7]
    subject = commit["subject"]
    return f"- `{short_sha}` {subject}"


def format_debug_log(
    ranges: list[VersionRange], annotated_commits: list[dict], *, color: bool = False
) -> str:
    """Format annotated git log for debug output.

    annotated_commits: list of dicts with 'sha', 'subject', 'version' keys.
    Returns a multi-line string with version boundary headers inserted between ranges.
    """
    if not annotated_commits:
        return "(no commits)"

    _cyan_bold = "\033[1;36m"
    _yellow_bold = "\033[1;33m"
    _reset = "\033[0m"

    def _color_end(line: str) -> str:
        return f"{_cyan_bold}{line}{_reset}" if color else line

    def _color_start(line: str) -> str:
        return f"{_yellow_bold}{line}{_reset}" if color else line

    version_counts: dict[str, int] = {}
    for c in annotated_commits:
        v = c["version"]
        version_counts[v] = version_counts.get(v, 0) + 1

    range_info: dict[str, str] = {}
    for r in ranges:
        from_short = r.from_sha[:7] if r.from_sha else "root"
        to_short = r.to_sha[:7]
        range_info[r.version] = f"{from_short}..{to_short}"

    def _fmt_start(version: str) -> str:
        count = version_counts.get(version, 0)
        range_str = range_info.get(version, "unknown")
        return f"--- start v{version} ({range_str}) [{count} commits] ---"

    def _fmt_end(version: str) -> str:
        return f"--- end {version} ---"

    lines = []
    current_version: str | None = None
    for commit in annotated_commits:
        version = commit["version"]
        if version != current_version:
            if current_version is not None:
                lines.append(_color_end(_fmt_end(current_version)))
            lines.append(_color_start(_fmt_start(version)))
            current_version = version
        lines.append(f"{commit['sha']} {commit['subject']}")

    if current_version is not None:
        lines.append(_color_end(_fmt_end(current_version)))

    return "\n".join(lines)


def render_markdown(
    version: str,
    vrange: VersionRange,
    grouped_beads: dict[str, list[dict]],
    orphan_commits: list[dict],
    all_commits: list[dict],
) -> str:
    """Render the full markdown string for a version's release notes."""
    total_beads = sum(len(v) for v in grouped_beads.values())
    orphan_count = len(orphan_commits)
    total_commits = len(all_commits)

    from_sha_display = vrange.from_sha[:7] if vrange.from_sha else "unknown"
    to_sha_display = vrange.to_sha[:7]
    from_date_display = vrange.from_date[:10] if vrange.from_date else "unknown"
    to_date_display = vrange.to_date[:10]

    covered_count = total_commits - orphan_count
    coverage_pct = (covered_count / total_commits * 100) if total_commits else 0
    bead_breakdown = " | ".join(
        f"{SECTION_LABELS.get(btype, btype.capitalize())}: {len(grouped_beads[btype])}"
        for btype in SECTION_ORDER
        if btype in grouped_beads
    )

    lines = [
        f"# v{version} Release Notes (DRAFT)",
        "",
        "## Stats",
        "",
        "| | |",
        "|---|---|",
        f"| **Range** | `{from_sha_display}..{to_sha_display}` ({from_date_display} to {to_date_display}) |",
        f"| **Commits** | {total_commits} ({coverage_pct:.0f}% bead-covered) |",
        f"| **Beads closed** | {total_beads} |",
        f"| **By type** | {bead_breakdown or 'none'} |",
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

    if all_commits:
        lines.append("## Git Log")
        lines.append("")
        lines.extend(render_commit_line(commit) for commit in all_commits)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subprocess-backed functions
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def detect_version_ranges(*, rolling_main: bool = True) -> list[VersionRange]:
    """Walk git log for pyproject.toml, detect version bumps, return ranges.

    rolling_main=True (default): era semantics — each version covers commits made
    while that version was active (from its bump commit to the next bump).

    rolling_main=False: traditional release-notes semantics — each version covers
    commits since the previous version bump up to the current bump.
    """
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

    # Find version-bump commits by comparing each entry to the next (older) one.
    # Many commits touch pyproject.toml without changing the version; a bump commit
    # is one whose version DIFFERS from its predecessor in the log (or is the oldest).
    bump_commits = []
    for i, commit in enumerate(commits):
        is_oldest = i + 1 >= len(commits)
        version_changed = is_oldest or commit["version"] != commits[i + 1]["version"]
        if version_changed:
            bump_commits.append(commit)

    ranges: list[VersionRange] = []

    if rolling_main:
        # Era semantics: each version's range covers commits made while it was active.
        # from_sha = this version's bump commit (exclusive lower bound in git range)
        # to_sha   = the next (newer) bump commit, so from_sha..to_sha captures the era.
        # Iterate oldest-first (high index → low index in bump_commits which is newest-first).
        for i in range(len(bump_commits) - 1, -1, -1):
            bump = bump_commits[i]
            if i > 0:
                newer = bump_commits[i - 1]
                to_sha: str = newer["sha"]
                to_date: str = newer["date"]
            else:
                # Newest bump: extends to itself; unreleased logic covers HEAD separately.
                to_sha = bump["sha"]
                to_date = bump["date"]
            from_sha: str | None = bump["sha"]
            from_date: str | None = bump["date"]
            # Oldest version: use the repo root as the lower bound so the era
            # captures commits from the very beginning through the next bump.
            if i == len(bump_commits) - 1:
                root_result = _run(["git", "rev-list", "--max-parents=0", bump["sha"]], check=False)
                if root_result.returncode == 0:
                    from_sha = root_result.stdout.strip()
                    root_date_result = _run(
                        ["git", "log", "-1", "--format=%aI", from_sha], check=False
                    )
                    from_date = (
                        root_date_result.stdout.strip()
                        if root_date_result.returncode == 0
                        else from_date
                    )
            ranges.append(
                VersionRange(
                    version=bump["version"],
                    from_sha=from_sha,
                    to_sha=to_sha,
                    from_date=from_date,
                    to_date=to_date,
                )
            )
    else:
        # Traditional semantics: each version covers commits since the previous bump.
        # to_sha   = bump commit for this version (newer end)
        # from_sha = bump commit for the previous (older) version
        for i, bump in enumerate(bump_commits):
            to_sha = bump["sha"]
            to_date = bump["date"]
            if i + 1 < len(bump_commits):
                older = bump_commits[i + 1]
                from_sha = older["sha"]
                from_date = older["date"]
            else:
                # Oldest version: use the repo root commit as the lower bound.
                root_result = _run(["git", "rev-list", "--max-parents=0", to_sha], check=False)
                from_sha = root_result.stdout.strip() if root_result.returncode == 0 else None
                root_date_result = _run(
                    ["git", "log", "-1", "--format=%aI", from_sha or to_sha], check=False
                )
                from_date = (
                    root_date_result.stdout.strip() if root_date_result.returncode == 0 else None
                )
            ranges.append(
                VersionRange(
                    version=bump["version"],
                    from_sha=from_sha,
                    to_sha=to_sha,
                    from_date=from_date,
                    to_date=to_date,
                )
            )

    # Also handle HEAD (unreleased commits after the latest version bump)
    if bump_commits:
        latest_bump = bump_commits[0]
        head_result = _run(["git", "rev-parse", "HEAD"])
        head_sha = head_result.stdout.strip()
        if head_sha != latest_bump["sha"]:
            # There are commits after the latest version bump
            head_date_result = _run(["git", "log", "-1", "--format=%aI", "HEAD"])
            head_date = head_date_result.stdout.strip()
            ranges.insert(
                0,
                VersionRange(
                    version=f"{latest_bump['version']}-IN_PROGRESS",
                    from_sha=latest_bump["sha"],
                    to_sha=head_sha,
                    from_date=latest_bump["date"],
                    to_date=head_date,
                ),
            )

    # When IN_PROGRESS exists, drop the redundant newest-bump era (from_sha==to_sha).
    unreleased_bases = {
        r.version.removesuffix("-IN_PROGRESS") for r in ranges if r.version.endswith("-IN_PROGRESS")
    }
    ranges = [r for r in ranges if not (r.from_sha == r.to_sha and r.version in unreleased_bases)]

    # Sort: unreleased first, then newest version first (descending)
    def sort_key(r: VersionRange):
        if r.version.endswith("-IN_PROGRESS"):
            return (0, [])
        return (1, [-int(x) for x in r.version.split(".")])

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
    if from_sha is None or from_sha == to_sha:
        # No lower bound (initial version whose bump IS the root commit).
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


def parse_bead_close_commits_from_diff(log_output: str) -> dict[str, str]:
    """Parse raw git log -p output and return {bead_id: first_close_commit_sha}.

    Identifies the first commit where each bead's status transitioned to "closed".
    Compaction commits (status stays "closed" on both sides) are ignored.
    """
    result: dict[str, str] = {}
    current_sha: str | None = None
    removed: dict[str, str] = {}  # bead_id -> old status in this commit
    added_closed: dict[str, str] = {}  # bead_id -> sha for +closed lines in this commit

    def _flush() -> None:
        for bead_id, sha in added_closed.items():
            old_status = removed.get(bead_id)
            if old_status is None or old_status != "closed":
                if bead_id not in result:
                    result[bead_id] = sha

    for line in log_output.splitlines():
        if line.startswith("COMMIT:"):
            _flush()
            current_sha = line[7:]
            removed = {}
            added_closed = {}
        elif line.startswith(("---", "+++")):
            continue
        elif line.startswith("-") and current_sha:
            try:
                data = json.loads(line[1:])
                if isinstance(data, dict) and "id" in data:
                    removed[data["id"]] = data.get("status", "")
            except (json.JSONDecodeError, ValueError):
                pass
        elif line.startswith("+") and current_sha:
            try:
                data = json.loads(line[1:])
                if isinstance(data, dict) and "id" in data and data.get("status") == "closed":
                    added_closed[data["id"]] = current_sha
            except (json.JSONDecodeError, ValueError):
                pass

    _flush()
    return result


def fetch_bead_close_commits() -> dict[str, str]:
    """Return {bead_id: commit_sha} for the first commit where each bead was closed."""
    result = _run(
        [
            "git",
            "log",
            "-p",
            "--diff-filter=M",
            "--format=COMMIT:%H",
            "--",
            ".beads/issues.jsonl",
        ],
        check=False,
    )
    if result.returncode != 0:
        return {}
    return parse_bead_close_commits_from_diff(result.stdout)


def build_sha_to_version(ranges: list[VersionRange]) -> dict[str, str]:
    """Return {full_sha: version} for all commits in all ranges."""
    sha_to_version: dict[str, str] = {}
    for vrange in ranges:
        if vrange.from_sha is None:
            result = _run(["git", "log", "--format=%H", vrange.to_sha], check=False)
        elif vrange.from_sha == vrange.to_sha:
            result = _run(["git", "log", "--format=%H", "-1", vrange.to_sha], check=False)
        else:
            parent_result = _run(["git", "log", "--format=%P", "-1", vrange.from_sha], check=False)
            is_root = parent_result.returncode == 0 and not parent_result.stdout.strip()
            rev_range = vrange.to_sha if is_root else f"{vrange.from_sha}..{vrange.to_sha}"
            result = _run(["git", "log", "--format=%H", rev_range], check=False)
        if result.returncode == 0:
            for sha in result.stdout.splitlines():
                sha = sha.strip()
                if sha:
                    sha_to_version[sha] = vrange.version
    return sha_to_version


def assign_beads_to_versions(all_beads: list[dict], ranges: list[VersionRange]) -> dict[str, str]:
    """Return {bead_id: version} for all closed beads using git commit attribution.

    Falls back to date-based filtering for beads whose close commit isn't in any range.
    """
    close_commits = fetch_bead_close_commits()
    sha_to_version = build_sha_to_version(ranges)

    bead_id_to_version: dict[str, str] = {}
    fallback_beads: list[dict] = []

    for bead in all_beads:
        bead_id = bead.get("id")
        if not bead_id:
            continue
        close_sha = close_commits.get(bead_id)
        if close_sha and close_sha in sha_to_version:
            bead_id_to_version[bead_id] = sha_to_version[close_sha]
        else:
            fallback_beads.append(bead)

    for vrange in ranges:
        fallback_in_range = filter_beads_by_date(fallback_beads, vrange.from_date, vrange.to_date)
        for bead in fallback_in_range:
            bead_id = bead.get("id")
            if bead_id and bead_id not in bead_id_to_version:
                bead_id_to_version[bead_id] = vrange.version

    return bead_id_to_version


def build_debug_annotated_commits(ranges: list[VersionRange]) -> list[dict]:
    """Build annotated commit list for debug output by mapping each commit to its version."""
    sha_to_version_short: dict[str, str] = {}
    for vrange in ranges:
        if vrange.from_sha is None:
            # No lower bound: include all ancestors of to_sha
            result = _run(["git", "log", "--format=%h", vrange.to_sha], check=False)
        elif vrange.from_sha == vrange.to_sha:
            # Single-commit era (newest bump with no further commits yet).
            # Use -1 to avoid bare SHA returning all ancestors.
            result = _run(["git", "log", "--format=%h", "-1", vrange.to_sha], check=False)
        else:
            # Check whether from_sha is the repo root (no parents), so we include it.
            parent_result = _run(["git", "log", "--format=%P", "-1", vrange.from_sha], check=False)
            is_root = parent_result.returncode == 0 and not parent_result.stdout.strip()
            rev_range = vrange.to_sha if is_root else f"{vrange.from_sha}..{vrange.to_sha}"
            result = _run(["git", "log", "--format=%h", rev_range], check=False)
        if result.returncode == 0:
            for sha in result.stdout.splitlines():
                sha = sha.strip()
                if sha:
                    sha_to_version_short[sha] = vrange.version

    result = _run(["git", "log", "--format=%h %s"], check=False)
    if result.returncode != 0:
        return []

    annotated = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        sha = parts[0]
        subject = parts[1] if len(parts) > 1 else ""
        version = sha_to_version_short.get(sha, "unknown")
        annotated.append({"sha": sha, "subject": subject, "version": version})
    return annotated


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def generate_notes_for_range(
    vrange: VersionRange,
    all_beads: list[dict],
    output_dir: Path,
    bead_id_to_version: dict[str, str],
    *,
    force: bool,
) -> str:
    """Generate notes for one version range. Returns the output file path."""
    version = vrange.version
    filename = f"v{version.removesuffix('-IN_PROGRESS')}.md"
    output_path = output_dir / filename

    if output_path.exists() and not force:
        print(f"  Skipping {filename} (already exists, use --force to overwrite)")
        return str(output_path)

    commits = fetch_git_commits(vrange.from_sha, vrange.to_sha)
    beads_in_range = [b for b in all_beads if bead_id_to_version.get(b.get("id")) == version]
    grouped = group_beads_by_type(beads_in_range)

    bead_ids = {b["id"] for b in beads_in_range}
    _, orphan_commits = classify_commits(commits, bead_ids)

    md = render_markdown(version, vrange, grouped, orphan_commits, commits)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md)

    total_beads = sum(len(v) for v in grouped.values())
    covered = len(commits) - len(orphan_commits)
    coverage_pct = (covered / len(commits) * 100) if commits else 0
    from_display = vrange.from_sha[:7] if vrange.from_sha else "root"
    git_range = f"{from_display}..{vrange.to_sha[:7]}"
    print(
        f"  {version:<24}  {git_range}  "
        f"{total_beads} beads ({coverage_pct:.0f}% covered)  "
        f"{len(commits)} commits"
    )
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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print annotated git log showing version boundaries, then exit.",
    )
    parser.add_argument(
        "--rolling-main",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use era-based version ranges (default). "
        "Each version includes commits made while that version was active. "
        "Use --no-rolling-main for traditional release-notes semantics.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    print("Detecting ranges and fetching beads...")
    ranges = detect_version_ranges(rolling_main=args.rolling_main)
    if not ranges:
        print("No version ranges detected.", file=sys.stderr)
        return 1

    if args.debug:
        annotated = build_debug_annotated_commits(ranges)
        print(format_debug_log(ranges, annotated, color=sys.stdout.isatty()))
        return 0

    all_beads = fetch_closed_beads()
    bead_id_to_version = assign_beads_to_versions(all_beads, ranges)

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
    elif (ranges and ranges[0].version.endswith("-IN_PROGRESS")) or ranges:
        ranges_to_process = [ranges[0]]
    else:
        print("No ranges to process.", file=sys.stderr)
        return 1

    for vrange in ranges_to_process:
        generate_notes_for_range(
            vrange, all_beads, output_dir, bead_id_to_version, force=args.force
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
