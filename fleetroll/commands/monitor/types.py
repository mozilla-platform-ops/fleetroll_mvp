"""Shared types and constants for the monitor module."""

from __future__ import annotations

FLEETROLL_MASCOT = [
    "  ▄█████▄  ",
    " ▐▛▀▀▀▀▀▜▌ ",
    "▗▟█▄███▄█▙▖",
    "  ▀▘   ▀▘  ",
]

COLUMN_GUIDE_TEXT = """\
Column Guide (press any key to close)

Keybindings:
  s           Cycle sort order (host → role → ovr_sha → host)
  o           Toggle override filter (all hosts ↔ overrides only)
  q           Quit monitor

Columns:
HOST      Hostname (FQDN suffix stripped if common)
ROLE      Puppet role assigned to host
VLT_SHA   Vault file SHA256 hash (symlink name in parentheses)
OVR_SHA   Override file SHA256 hash (branch name in parentheses)
UPTIME    Host uptime since last boot
PP_LAST   Time since last puppet run (FAIL if failed)
PP_EXP    Expected puppet SHA (branch HEAD or master HEAD)
PP_SHA    Actual puppet SHA that was applied
PP_MATCH  Puppet running latest branch code
          Y = puppet git SHA matches expected
          N = SHA mismatch or puppet failed
          - = no puppet/GitHub data available
TC_ACT    Time since TC worker was last active
TC_T_DUR  TC task duration (or time since start if in progress)
          Color: green=completed, yellow=exception, red=failed
TC_QUAR   TC quarantine status (Y if quarantined)
DATA      Data freshness: audit_age/tc_age

RO_HEALTH Overall rollout health status
          Y = PP_MATCH and TC worker active (< 1 hour)
          N = not matched or TC worker stale
          - = no puppet/GitHub data available
"""
