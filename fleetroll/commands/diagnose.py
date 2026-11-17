"""Read-only live health diagnosis; independent of idle/action eligibility."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..exceptions import CommandFailureError, UserError
from ..gwhc import parse_diagnose_output
from ..ssh import build_ssh_options, remote_diagnose_script, run_ssh
from ..utils import expand_hostname, looks_like_host, utc_now_iso

if TYPE_CHECKING:
    from ..cli_types import HostDiagnoseArgs


def cmd_host_diagnose(args: HostDiagnoseArgs) -> None:
    """Emit a live snapshot without persisting observations or changing the host."""
    if not looks_like_host(args.host):
        raise UserError("host diagnose requires exactly one hostname; host files are unsupported.")
    user, separator, hostname = args.host.rpartition("@")
    host = f"{user}@{expand_hostname(hostname)}" if separator else expand_hostname(args.host)
    try:
        ssh_rc, stdout, stderr = run_ssh(
            host,
            remote_diagnose_script(),
            ssh_options=build_ssh_options(args),
            timeout_s=args.timeout,
        )
        result = parse_diagnose_output(stdout, ssh_rc=ssh_rc)
        result["stderr"] = stderr.strip()
    except Exception as exc:
        result = parse_diagnose_output("", ssh_rc=1)
        result.update(reason="ssh_error", error=f"{type(exc).__name__}: {exc}", ssh_exit_code=None)
    result.update(host=host, ts=utc_now_iso())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Host: {host}")
        print(f"Checked at: {result['checked_at'] or result['ts']}")
        print(f"Collection: {result['collection_status']}; health: {result['health']}")
        print(f"Reason: {result['reason']}; gwhc state: {result['gwhc_state']}")
        print(f"Zombie: {result['zombie']['status']} ({result['zombie']['reason']})")
        print("Snapshot only; diagnostic evidence does not authorize recovery.")
    if not result["ok"]:
        raise CommandFailureError
