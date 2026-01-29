"""FleetRoll utility functions."""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import re
from pathlib import Path

from .constants import AUDIT_DIR_NAME, AUDIT_FILE_NAME, HOST_OBSERVATIONS_FILE_NAME
from .exceptions import FleetRollError, UserError


def natural_sort_key(text: str) -> list[int | str]:
    """Return a key for natural (alphanumeric) sorting.

    Splits strings into text and numeric parts for natural ordering.
    Example: ['host1', 'host2', 'host10'] sorts as 1, 2, 10 (not 1, 10, 2).

    Args:
        text: String to generate sort key for

    Returns:
        List of alternating strings and integers for sorting
    """

    def convert(part: str) -> int | str:
        return int(part) if part.isdigit() else part.lower()

    return [convert(c) for c in re.split(r"(\d+)", text)]


def utc_now_iso() -> str:
    """Return current UTC time in ISO format without microseconds."""
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def format_elapsed_time(seconds: float) -> str:
    """Format elapsed time in human-readable format.

    Args:
        seconds: Elapsed time in seconds

    Returns:
        Formatted string like "1m25s", "45s", or "1h05m30s"
    """
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def sha256_hex(data: bytes) -> str:
    """Return SHA256 hex digest of data."""
    return hashlib.sha256(data).hexdigest()


def ensure_parent_dir(p: Path) -> None:
    """Create parent directory of path if it doesn't exist."""
    p.parent.mkdir(parents=True, exist_ok=True)


def infer_actor() -> str:
    """Infer the actor (user) performing the operation."""
    return (
        os.environ.get("FLEETROLL_ACTOR")
        or os.environ.get("SUDO_USER")
        or os.environ.get("USER")
        or "unknown"
    )


def default_audit_log_path() -> Path:
    """Return default path for audit log file."""
    home = Path(os.path.expanduser("~"))
    return home / AUDIT_DIR_NAME / AUDIT_FILE_NAME


def default_host_observations_log_path() -> Path:
    """Return default path for host observations log file."""
    home = Path(os.path.expanduser("~"))
    return home / AUDIT_DIR_NAME / HOST_OBSERVATIONS_FILE_NAME


def get_log_file_size(path: Path) -> int:
    """Return file size in bytes, or 0 if file doesn't exist.

    Args:
        path: Path to log file

    Returns:
        File size in bytes, or 0 if file not found
    """
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def parse_kv_lines(output: str) -> dict[str, str]:
    """Parse key=value lines from string output."""
    d: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def is_host_file(host_arg: str) -> bool:
    """Check if argument is a host list file path."""
    p = Path(host_arg)
    if p.suffix == ".list":
        return True
    return p.exists() and p.is_file()


def parse_host_list(file_path: Path) -> list[str]:
    """Parse host list file. One host per line, ignore comments (#) and blank lines."""
    if not (file_path.exists() and file_path.is_file()):
        raise FleetRollError(f"Host list file not found: {file_path}")
    hosts = []
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            hosts.append(line)
    if not hosts:
        raise FleetRollError(f"No valid hosts found in {file_path}")
    return hosts


_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def looks_like_host(host_arg: str) -> bool:
    """Return True if host_arg looks like a hostname or IP address."""
    if not host_arg:
        return False
    if "@" in host_arg:
        _, host = host_arg.rsplit("@", 1)
    else:
        host = host_arg
    if not host:
        return False
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return _HOSTNAME_RE.match(host) is not None


def ensure_host_or_file(host_arg: str) -> None:
    """If not host-like, require host_arg to exist as a file."""
    p = Path(host_arg)
    if p.suffix == ".list":
        if not (p.exists() and p.is_file()):
            raise UserError(f"Host list file not found: {host_arg}")
        return
    if looks_like_host(host_arg):
        return
    if not (p.exists() and p.is_file()):
        raise UserError(
            f"HOST_OR_FILE does not look like a hostname or IP and file was not found: {host_arg}"
        )
