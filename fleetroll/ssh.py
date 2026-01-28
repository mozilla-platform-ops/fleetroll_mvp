"""FleetRoll SSH execution and remote script generation."""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from typing import TYPE_CHECKING, List, Optional, Tuple

from .constants import CONTENT_SENTINEL, SSH_TIMEOUT_EXIT_CODE
from .exceptions import FleetRollError

if TYPE_CHECKING:
    from .cli import Args

logger = logging.getLogger("fleetroll")


def run_ssh(
    host: str,
    remote_cmd: str,
    *,
    ssh_options: List[str],
    input_bytes: Optional[bytes] = None,
    timeout_s: int = 60,
) -> Tuple[int, str, str]:
    """
    Executes: ssh [opts...] host remote_cmd

    Returns (returncode, stdout, stderr). Does NOT raise on non-zero rc.
    """
    cmd = ["ssh", "-o", "BatchMode=yes"] + ssh_options + [host, remote_cmd]
    logger.debug("SSH command: ssh %s %s '<script>'", " ".join(ssh_options), host)
    logger.debug("SSH timeout: %ds", timeout_s)

    start_time = time.time()
    try:
        p = subprocess.run(
            cmd,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - start_time
        logger.debug("SSH timeout after %.2fs", elapsed)
        return (
            SSH_TIMEOUT_EXIT_CODE,
            e.stdout.decode("utf-8", "replace") if e.stdout else "",
            e.stderr.decode("utf-8", "replace") if e.stderr else "ssh timeout",
        )
    except FileNotFoundError:
        raise FleetRollError(
            "ssh binary not found on PATH. Install OpenSSH client (ssh)."
        )

    elapsed = time.time() - start_time
    logger.debug("SSH completed in %.2fs (rc=%d)", elapsed, p.returncode)
    return (
        p.returncode,
        p.stdout.decode("utf-8", "replace"),
        p.stderr.decode("utf-8", "replace"),
    )


def build_ssh_options(args: Args) -> List[str]:
    """Build SSH options list from command arguments."""
    opts: List[str] = []
    # ConnectTimeout is client-side only; safe default for humans.
    opts += ["-o", f"ConnectTimeout={args.connect_timeout}"]
    # Prefer to fail fast rather than hang on unknown host key prompts.
    # Users can override via --ssh-option if they prefer.
    opts += ["-o", "StrictHostKeyChecking=accept-new"]
    if args.ssh_option:
        for item in args.ssh_option:
            # Each --ssh-option can include multiple tokens, e.g. "-J bastion" or "-p 2222"
            opts += shlex.split(item)
    return opts


def audit_script_body(
    override_path: str,
    role_path: str,
    vault_path: str,
    *,
    include_content: bool,
) -> str:
    """Return the remote shell script body for auditing a host."""
    # Remote output is line-oriented to make it easy to parse.
    # We intentionally avoid printing arbitrary separators in content output.
    # Content is printed after a sentinel line.
    sentinel = CONTENT_SENTINEL
    include_content_cmd = "true" if include_content else "false"
    # Note: use /bin/sh for portability.
    # We use sudo -n everywhere; failures will be visible.
    script = f"""
set -eu
op={shlex.quote(override_path)}
rp={shlex.quote(role_path)}
vp={shlex.quote(vault_path)}

# Uptime (best effort)
if [ -r /proc/uptime ]; then
  uptime_s=$(awk '{{print int($1)}}' /proc/uptime 2>/dev/null || true)
  if [ -n "$uptime_s" ]; then
    printf 'UPTIME_S=%s\\n' "$uptime_s"
  fi
fi

# Role (best effort)
if sudo -n test -e "$rp" 2>/dev/null; then
  role=$(sudo -n cat "$rp" 2>/dev/null || true)
  printf 'ROLE_PRESENT=1\\n'
  printf 'ROLE=%s\\n' "$(printf %s "$role" | tr '\\n' ' ' | sed 's/[[:space:]]\\+/ /g' )"
else
  printf 'ROLE_PRESENT=0\\n'
fi

# Vault (best effort)
if sudo -n test -e "$vp" 2>/dev/null; then
  printf 'VLT_PRESENT=1\\n'
  stat_out=$(sudo -n stat -c '%a %U %G %s %Y' "$vp" 2>/dev/null || true)
  if [ -n "$stat_out" ]; then
    set -- $stat_out
    printf 'VLT_MODE=%s\\n' "$1"
    printf 'VLT_OWNER=%s\\n' "$2"
    printf 'VLT_GROUP=%s\\n' "$3"
    printf 'VLT_SIZE=%s\\n' "$4"
    printf 'VLT_MTIME=%s\\n' "$5"
  fi
  if sudo -n test -r "$vp" 2>/dev/null; then
    if command -v sha256sum >/dev/null 2>&1; then
      vsha=$(sudo -n sha256sum "$vp" 2>/dev/null | awk '{{print $1}}')
    elif command -v shasum >/dev/null 2>&1; then
      vsha=$(sudo -n shasum -a 256 "$vp" 2>/dev/null | awk '{{print $1}}')
    else
      vsha=""
    fi
    if [ -n "$vsha" ]; then
      printf 'VLT_SHA256=%s\\n' "$vsha"
    fi
  fi
else
  printf 'VLT_PRESENT=0\\n'
fi

# Puppet last run state (best effort)
# Try last_run_report.yaml first (Puppet 7+), then fall back to summary files
pp_report="/opt/puppetlabs/puppet/cache/state/last_run_report.yaml"
if sudo -n test -e "$pp_report" 2>/dev/null; then
  # Parse report file (Puppet 7+): time is ISO timestamp, status indicates success
  pp_time=$(sudo -n grep "^time:" "$pp_report" 2>/dev/null | head -1 | sed 's/^time: *//' | tr -d "\\\"\\'")
  if [ -n "$pp_time" ]; then
    # Convert ISO timestamp to epoch (strip nanoseconds for compatibility)
    pp_time_clean=$(printf '%s' "$pp_time" | sed 's/\\.[0-9]*//; s/+00:00$/Z/')
    pp_epoch=$(date -d "$pp_time_clean" +%s 2>/dev/null || true)
    if [ -n "$pp_epoch" ]; then
      printf 'PP_LAST_RUN_EPOCH=%s\\n' "$pp_epoch"
    fi
  fi
  pp_status=$(sudo -n awk '/^status:/ {{print $2; exit}}' "$pp_report" 2>/dev/null || true)
  if [ -n "$pp_status" ]; then
    if [ "$pp_status" = "failed" ]; then
      printf 'PP_SUCCESS=0\\n'
    else
      printf 'PP_SUCCESS=1\\n'
    fi
  fi
else
  # Fall back to summary files (older Puppet versions)
  pp_state=""
  for pp_path in /opt/puppetlabs/puppet/cache/state/last_run_summary.yaml /var/lib/puppet/state/last_run_summary.yaml; do
    if sudo -n test -e "$pp_path" 2>/dev/null; then
      pp_state=$(sudo -n cat "$pp_path" 2>/dev/null || true)
      break
    fi
  done
  if [ -n "$pp_state" ]; then
    # Extract time.last_run (Unix epoch)
    pp_last_run=$(printf '%s' "$pp_state" | awk '/^time:/{{found=1}} found && /last_run:/{{print $2; exit}}')
    if [ -n "$pp_last_run" ]; then
      printf 'PP_LAST_RUN_EPOCH=%s\\n' "$pp_last_run"
    fi
    # Extract events.failure count (0 = success)
    pp_failure=$(printf '%s' "$pp_state" | awk '/^events:/{{found=1}} found && /failure:/{{print $2; exit}}')
    if [ -n "$pp_failure" ]; then
      if [ "$pp_failure" = "0" ]; then
        printf 'PP_SUCCESS=1\\n'
      else
        printf 'PP_SUCCESS=0\\n'
      fi
    fi
  fi
fi

# Override (best effort)
if sudo -n test -e "$op" 2>/dev/null; then
  printf 'OVERRIDE_PRESENT=1\\n'
  # stat fields: numeric mode, owner user, group, size, mtime epoch
  stat_out=$(sudo -n stat -c '%a %U %G %s %Y' "$op" 2>/dev/null || true)
  if [ -n "$stat_out" ]; then
    set -- $stat_out
    printf 'OVERRIDE_MODE=%s\\n' "$1"
    printf 'OVERRIDE_OWNER=%s\\n' "$2"
    printf 'OVERRIDE_GROUP=%s\\n' "$3"
    printf 'OVERRIDE_SIZE=%s\\n' "$4"
    printf 'OVERRIDE_MTIME=%s\\n' "$5"
  fi
  if {include_content_cmd}; then
    printf '{sentinel}\\n'
    sudo -n cat "$op" 2>/dev/null || true
  fi
else
  printf 'OVERRIDE_PRESENT=0\\n'
fi
"""
    # Trim leading/trailing whitespace so the remote shell doesn't see a blank first line as a command.
    return script.strip("\n")


def remote_audit_script(
    override_path: str, role_path: str, vault_path: str, *, include_content: bool
) -> str:
    """Generate remote shell script for auditing a host."""
    return "sh -c " + shlex.quote(
        audit_script_body(
            override_path=override_path,
            role_path=role_path,
            vault_path=vault_path,
            include_content=include_content,
        )
    )


def remote_read_file_script(path: str) -> str:
    """Generate remote shell script to read a file via sudo."""
    p = shlex.quote(path)
    script = f"""
set -eu
fp={p}
sudo -n cat "$fp"
"""
    return "sh -c " + shlex.quote(script.strip("\n"))


def remote_set_script(
    override_path: str,
    *,
    mode: str,
    owner: str,
    group: str,
    backup: bool,
    backup_suffix: str,
) -> str:
    """Generate remote shell script for setting override file."""
    op = shlex.quote(override_path)
    m = shlex.quote(mode)
    og = shlex.quote(f"{owner}:{group}")
    b = "true" if backup else "false"
    suf = shlex.quote(backup_suffix)
    # stdin is the desired file contents
    # Steps:
    # 1) mktemp in same dir (atomic mv)
    # 2) write via tee
    # 3) chmod/chown
    # 4) optional backup
    # 5) mv into place
    script = f"""
set -eu
op={op}
dir=$(dirname "$op")
tmp=$(sudo -n mktemp "$dir/.ronin_settings.tmp.XXXXXX")

# Write new contents
sudo -n tee "$tmp" >/dev/null

# Normalize perms/ownership
sudo -n chmod {m} "$tmp"
sudo -n chown {og} "$tmp"

# Backup existing if requested
if {b}; then
  if sudo -n test -e "$op" 2>/dev/null; then
    sudo -n cp -a "$op" "$op.bak.{suf}"
  fi
fi

# Atomic replace
sudo -n mv -f "$tmp" "$op"
"""
    return "sh -c " + shlex.quote(script.strip("\n"))


def remote_unset_script(override_path: str, *, backup: bool, backup_suffix: str) -> str:
    """Generate remote shell script for unsetting (removing) override file."""
    op = shlex.quote(override_path)
    b = "true" if backup else "false"
    suf = shlex.quote(backup_suffix)
    script = f"""
set -eu
op={op}
if sudo -n test -e "$op" 2>/dev/null; then
  if {b}; then
    sudo -n cp -a "$op" "$op.bak.{suf}"
  fi
  sudo -n rm -f "$op"
  echo "REMOVED=1"
else
  echo "REMOVED=0"
fi
"""
    return "sh -c " + shlex.quote(script.strip("\n"))
