"""GitHub API integration for fetching branch refs."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import requests

from .commands.monitor.cache import parse_override_file
from .constants import (
    AUDIT_DIR_NAME,
    DEFAULT_GITHUB_REPO,
    GITHUB_REFS_FILE_NAME,
    OVERRIDES_DIR_NAME,
)
from .utils import utc_now_iso

logger = logging.getLogger(__name__)

GITHUB_FETCH_INTERVAL_S = 3600  # 1 hour


def parse_github_repo_url(url: str) -> tuple[str, str] | None:
    """Parse GitHub URL to extract owner and repo name.

    Args:
        url: GitHub URL (e.g., 'https://github.com/aerickson/ronin_puppet.git')

    Returns:
        Tuple of (owner, repo) or None if URL is not a GitHub URL
    """
    # Match both https and git@github.com URLs
    # Examples:
    # - https://github.com/aerickson/ronin_puppet.git
    # - git@github.com:aerickson/ronin_puppet.git
    # - https://github.com/aerickson/ronin_puppet
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not match:
        return None

    owner = match.group(1)
    repo = match.group(2)
    return (owner, repo)


def collect_repo_branches(overrides_dir: Path) -> dict[tuple[str, str], set[str]]:
    """Scan override files to discover unique (owner, repo) -> branches mapping.

    Args:
        overrides_dir: Path to overrides directory

    Returns:
        Dictionary mapping (owner, repo) to set of branch names.
        Always includes ('mozilla-platform-ops', 'ronin_puppet') -> {'master'}.
    """
    repo_branches: dict[tuple[str, str], set[str]] = {}

    # Always include default repo/branch
    default_owner, default_repo = DEFAULT_GITHUB_REPO.split("/")
    repo_branches[(default_owner, default_repo)] = {"master"}

    if not overrides_dir.exists():
        logger.debug("Overrides directory does not exist: %s", overrides_dir)
        return repo_branches

    try:
        for item in overrides_dir.iterdir():
            if not item.is_file():
                continue

            override_info = parse_override_file(item)
            if not override_info:
                continue

            # Read file content directly to extract PUPPET_REPO
            try:
                content = item.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            repo_match = re.search(r"PUPPET_REPO=['\"](.+?)['\"]", content)
            if not repo_match:
                continue

            repo_url = repo_match.group(1)
            parsed = parse_github_repo_url(repo_url)
            if not parsed:
                continue

            owner, repo = parsed
            branch = override_info.get("branch")
            if not branch:
                continue

            repo_key = (owner, repo)
            if repo_key not in repo_branches:
                repo_branches[repo_key] = set()
            repo_branches[repo_key].add(branch)

    except OSError:
        logger.exception("Error scanning overrides directory")

    return repo_branches


def fetch_branch_shas(owner: str, repo: str) -> list[dict[str, str]]:
    """Fetch branch refs from GitHub API.

    Args:
        owner: GitHub repository owner
        repo: GitHub repository name

    Returns:
        List of dicts with 'ref' and 'sha' keys, or [] on error
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "fleetroll",
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        refs = response.json()
        results = []

        for ref_data in refs:
            ref_full = ref_data.get("ref", "")
            sha = ref_data.get("object", {}).get("sha")

            # Extract branch name from refs/heads/branch-name
            if ref_full.startswith("refs/heads/") and sha:
                branch = ref_full[len("refs/heads/") :]
                results.append({"ref": branch, "sha": sha})

        return results

    except requests.RequestException:
        logger.exception("Failed to fetch branches for %s/%s", owner, repo)
        return []


def should_fetch(refs_path: Path) -> bool:
    """Check if we should fetch GitHub refs based on throttle interval.

    Args:
        refs_path: Path to github_refs.jsonl file

    Returns:
        True if file doesn't exist or was last modified > GITHUB_FETCH_INTERVAL_S ago
    """
    if not refs_path.exists():
        return True

    try:
        mtime = refs_path.stat().st_mtime
        import time

        age_seconds = time.time() - mtime
        return age_seconds >= GITHUB_FETCH_INTERVAL_S
    except OSError:
        return True


def do_github_fetch(*, override_delay: bool = False, quiet: bool = False) -> None:
    """Fetch GitHub branch refs and write to JSONL file.

    Args:
        override_delay: If True, skip throttle check and fetch immediately
        quiet: If True, minimal output (single line)
    """
    import click

    # Determine paths
    home = Path.home()
    fleetroll_dir = home / AUDIT_DIR_NAME
    overrides_dir = fleetroll_dir / OVERRIDES_DIR_NAME
    refs_path = fleetroll_dir / GITHUB_REFS_FILE_NAME

    # Check throttle
    if not override_delay and not should_fetch(refs_path):
        if not quiet:
            import time

            mtime = refs_path.stat().st_mtime
            age_seconds = time.time() - mtime
            remaining_seconds = GITHUB_FETCH_INTERVAL_S - age_seconds
            remaining_mins = int(remaining_seconds / 60)
            click.echo(
                f"GitHub refs recently fetched ({remaining_mins}m ago), "
                f"skipping (use --override-delay to force)"
            )
        return

    # Collect repos and branches from overrides
    if not quiet:
        click.echo("Scanning overrides for GitHub repos/branches...")
    repo_branches = collect_repo_branches(overrides_dir)

    if not quiet:
        click.echo(f"Found {len(repo_branches)} unique repo(s)")

    # Fetch data from GitHub
    ts = utc_now_iso()
    total_branches_requested = sum(len(branches) for branches in repo_branches.values())
    branches_found = 0
    errors: list[str] = []

    refs_path.parent.mkdir(parents=True, exist_ok=True)

    with refs_path.open("a", encoding="utf-8") as f:
        for (owner, repo), branches in repo_branches.items():
            if not quiet:
                click.echo(f"Fetching {owner}/{repo}...", nl=False)

            refs = fetch_branch_shas(owner, repo)

            if not refs:
                error_msg = f"No refs returned for {owner}/{repo}"
                errors.append(error_msg)
                if not quiet:
                    click.echo(f" FAILED: {error_msg}")
                continue

            # Write branch_ref records for branches we care about
            refs_map = {ref["ref"]: ref["sha"] for ref in refs}
            matched = 0

            for branch in branches:
                if branch in refs_map:
                    record = {
                        "type": "branch_ref",
                        "ts": ts,
                        "owner": owner,
                        "repo": repo,
                        "branch": branch,
                        "sha": refs_map[branch],
                    }
                    f.write(json.dumps(record) + "\n")
                    branches_found += 1
                    matched += 1
                else:
                    error_msg = f"Branch {branch} not found in {owner}/{repo}"
                    errors.append(error_msg)

            if not quiet:
                click.echo(f" {matched}/{len(branches)} branch(es) found")

        # Write scan record
        scan_record = {
            "type": "scan",
            "ts": ts,
            "repos_queried": [f"{owner}/{repo}" for owner, repo in repo_branches],
            "branches_found": branches_found,
            "branches_requested": total_branches_requested,
            "errors": errors,
        }
        f.write(json.dumps(scan_record) + "\n")

    # Output summary
    if quiet:
        if errors:
            status = click.style("⚠ WARNING", fg="yellow")
            msg = f"{status} Wrote {branches_found}/{total_branches_requested} branch refs ({len(errors)} errors)"
        else:
            status = click.style("✓ SUCCESS", fg="green")
            msg = f"{status} Wrote {branches_found} branch refs"
        click.echo(msg)
    else:
        click.echo(f"Wrote {branches_found} branch ref(s) to {refs_path}")
        if errors:
            click.echo(f"Encountered {len(errors)} error(s):")
            for error in errors[:5]:  # Show first 5 errors
                click.echo(f"  - {error}")
            if len(errors) > 5:
                click.echo(f"  ... and {len(errors) - 5} more")
