"""Shared types and constants for the monitor module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cache import ShaInfoCache


@dataclass
class DataContext:
    """Shared reference bundle for host data.

    This dataclass bundles the mutable data structures used by renderers and pollers
    so they can see the same data without accessing MonitorDisplay directly.

    Attributes:
        latest: Most recent audit records by hostname
        latest_ok: Most recent successful (ok=True) audit records by hostname
        tc_data: TaskCluster worker data by short hostname
        github_refs: GitHub reference data (branches, commits) by repository
        sha_cache: Optional cache for SHA to human-readable info mappings
        fqdn_suffix: Optional common FQDN suffix to strip from hostnames
    """

    latest: dict[str, dict[str, Any]]
    latest_ok: dict[str, dict[str, Any]]
    tc_data: dict[str, dict[str, Any]]
    github_refs: dict[str, dict[str, Any]]
    sha_cache: ShaInfoCache | None
    fqdn_suffix: str | None


FLEETROLL_MASCOT = [
    "  ▄█████▄  ",
    " ▐▛▀▀▀▀▀▜▌ ",
    "▗▟█▄███▄█▙▖",
    "  ▀▘   ▀▘  ",
]


def compute_header_layout(left: str, right: str, usable_width: int) -> int:
    """Compute the number of rows needed for the header.

    Args:
        left: Left side text
        right: Right side text
        usable_width: Available screen width

    Returns:
        Number of rows needed (1 or 2)
    """
    if usable_width > 0 and len(left) + 1 + len(right) > usable_width:
        return 2
    return 1


def cycle_os_filter(current: str | None) -> str | None:
    """Cycle OS filter: None -> 'L' -> 'M' -> None.

    Args:
        current: Current OS filter value

    Returns:
        Next OS filter value in cycle
    """
    if current is None:
        return "L"
    if current == "L":
        return "M"
    return None


def os_filter_label(os_filter: str | None) -> str | None:
    """Return human-readable label for OS filter, or None if no filter.

    Args:
        os_filter: OS filter value ("L", "M", or None)

    Returns:
        Human-readable label or None
    """
    if os_filter == "L":
        return "Linux"
    if os_filter == "M":
        return "macOS"
    return None


COLUMN_GUIDE_TEXT = """\
Column Guide (press any key to close)

Keybindings:
  ↑/↓ or j/k  Scroll rows (page by page)
  ←/→ or h/l  Scroll columns (when they don't fit)
  s           Cycle sort order (host → role → ovr_sha → host)
  o           Toggle override filter (all hosts ↔ overrides only)
  O           Cycle OS filter (all → Linux → macOS → all)
  q           Quit monitor

Columns:
HOST      Hostname (FQDN suffix stripped if common)
ROLE      Puppet role assigned to host
OS        Host operating system (M=macOS/Darwin, L=Linux, W=Windows)
VLT_SHA   Vault file SHA256 hash (symlink name in parentheses)
OVR_SHA   Override file SHA256 hash (branch name in parentheses)
UPTIME    Host uptime since last boot
PP_LAST   Time since last puppet run (FAIL if failed)
PP_EXP    Expected puppet SHA (branch HEAD or master HEAD)
PP_SHA    Actual puppet SHA that was applied
PP_MATCH  Puppet SHA matches expected (master or override branch)
          Y = puppet git SHA matches expected
          N = SHA mismatch or puppet failed
          - = no puppet/GitHub data available
TC_ACT    Time since TC worker was last active
TC_T_DUR  TC task duration (or time since start if in progress)
          Color: green=completed, yellow=exception, red=failed
TC_QUAR   TC quarantine status (Y if quarantined)
DATA      Data freshness: audit_age/tc_age

HEALTHY   Overall host health status
          Y = PP_MATCH and TC worker active (< 1 hour)
          N = not matched or TC worker stale
          - = no puppet/GitHub data available
"""
