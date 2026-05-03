"""FleetRoll utility functions."""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import re
from pathlib import Path

from .constants import AUDIT_DIR_NAME, AUDIT_FILE_NAME
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


def format_host_preview(hosts: list[str], *, limit: int) -> list[str]:
    """Format host list for preview, showing first N hosts and overflow count.

    Args:
        hosts: List of hostnames
        limit: Maximum number of hosts to show

    Returns:
        List of formatted lines showing hosts and overflow message if applicable
    """
    if len(hosts) <= limit:
        return [f"  - {host}" for host in hosts]

    lines = [f"  - {host}" for host in hosts[:limit]]
    remaining = len(hosts) - limit
    lines.append(f"  ... and {remaining} more host{'s' if remaining > 1 else ''}")
    return lines


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


def check_log_sizes(*, warn_threshold_mb: int = 100) -> list[str]:
    """Return warning strings for log files that exceed the size threshold."""
    bytes_per_mb = 1024 * 1024
    fleetroll_dir = Path(os.path.expanduser("~")) / ".fleetroll"
    warnings = []

    audit_path = fleetroll_dir / "audit.jsonl"
    audit_size_mb = get_log_file_size(audit_path) / bytes_per_mb
    if audit_size_mb >= warn_threshold_mb:
        warnings.append(f"audit: {audit_size_mb:.0f}M")

    from .db import get_db_path

    db_file = get_db_path()
    db_size_mb = get_log_file_size(db_file) / bytes_per_mb
    if db_size_mb >= warn_threshold_mb:
        warnings.append(f"db: {db_size_mb:.0f}M")

    return warnings


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
    """Parse host list file. One host per line, ignore comments (#) and blank lines.

    Supports optional ``# fqdn: .suffix`` directive: short hostnames (no dots)
    get the suffix appended automatically.
    """
    if not (file_path.exists() and file_path.is_file()):
        raise FleetRollError(f"Host list file not found: {file_path}")
    fqdn_suffix: str | None = None
    hosts = []
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                m = re.match(r"^#\s*fqdn:\s*(\S+)", line)
                if m:
                    fqdn_suffix = m.group(1)
                    if not fqdn_suffix.startswith("."):
                        fqdn_suffix = "." + fqdn_suffix
                continue
            hosts.append(line)
    if not hosts:
        raise FleetRollError(f"No valid hosts found in {file_path}")
    if fqdn_suffix:
        hosts = [h + fqdn_suffix if "." not in h else h for h in hosts]
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


def resolve_host_args(args: tuple[str, ...]) -> tuple[list[str], Path | None]:
    """Resolve positional HOST_OR_FILE args to (expanded_hosts, host_file).

    Rules:
    - 1 arg, resolves to a file → parse it; return (hosts, path).
    - ≥1 args, none are files → expand each; return (expanded, None).
    - Mix of file + hostnames → raise UserError.
    """
    if not args:
        raise UserError("At least one HOST or file argument is required.")

    file_args = [a for a in args if is_host_file(a)]
    host_args = [a for a in args if not is_host_file(a)]

    if file_args and host_args:
        raise UserError(
            f"Cannot mix host file ({file_args[0]!r}) with bare hostnames. "
            "Pass either a file or one or more hostnames, not both."
        )

    if file_args:
        if len(file_args) > 1:
            raise UserError("Only one host list file may be specified.")
        host_file = Path(file_args[0])
        hosts = parse_host_list(host_file)
        return hosts, host_file

    expanded: list[str] = []
    for h in args:
        ensure_host_or_file(h)
        e = expand_hostname(h)
        if e != h:
            print(f"Expanding {h} → {e}")
        expanded.append(e)
    return expanded, None


def ensure_fqdn(hostname: str) -> None:
    """Raise UserError if hostname is not a valid FQDN (requires at least one dot)."""
    host = hostname.rsplit("@", 1)[-1] if "@" in hostname else hostname
    if not _HOSTNAME_RE.match(host) or "." not in host:
        raise UserError(f"Hostname must be a fully-qualified domain name (got {hostname!r})")


FQDN_DEFAULT_SUFFIX = ".test.releng.mdc1.mozilla.com"

_MS_SHORT_RE = re.compile(r"^ms(\d+)$", re.IGNORECASE)


def expand_hostname(hostname: str) -> str:
    """Expand a short hostname to an FQDN. Pass-through if already dotted."""
    if "." in hostname:
        return hostname
    m = _MS_SHORT_RE.match(hostname)
    if m:
        hostname = f"t-linux64-ms-{int(m.group(1)):03d}"
    return hostname + FQDN_DEFAULT_SUFFIX
