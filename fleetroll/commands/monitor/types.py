"""Shared types and constants for the monitor module."""

from __future__ import annotations

FLEETROLL_MASCOT = [
    "  ▄█████▄  ",
    " ▐▛▀▀▀▀▀▜▌ ",
    "▗▟█▄███▄█▙▖",
    "  ▀▘   ▀▘  ",
]

COLUMN_GUIDE_TEXT = """\
Column Guide (press q or Esc to close)

Keybindings:
  s, S        Toggle sort order (host ↔ role)

Columns:
HOST      Hostname (FQDN suffix stripped if common)
ROLE      Puppet role assigned to host
OVR_SHA   Override file SHA256 hash
VLT_SHA   Vault file SHA256 hash
UPTIME    Host uptime since last boot
PP_LAST   Time since last puppet run (FAIL if failed)
TC_LAST   Time since TC worker was last active
TC_T_DUR  TC task duration (or time since start if in progress)
TC_QUAR   TC quarantine status (Y if quarantined)
DATA      Data freshness: audit_age/tc_age

APPLIED   Override applied by puppet
          Y = override present, puppet ran after, succeeded
          N = override present, puppet hasn't run or failed
          - = no override present

HEALTHY   Overall rollout health status
          Y = APPLIED and TC worker active (< 1 hour)
          N = not applied or TC worker stale
          - = no override present
"""
