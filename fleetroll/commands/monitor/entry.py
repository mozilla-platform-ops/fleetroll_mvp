"""Monitor command entry point."""

from __future__ import annotations

import json
import sys
from curses import wrapper as curses_wrapper
from pathlib import Path
from typing import TYPE_CHECKING

from ...constants import TC_WORKERS_FILE_NAME
from ...exceptions import FleetRollError
from ...utils import (
    default_audit_log_path,
    ensure_host_or_file,
    is_host_file,
    parse_host_list,
)
from .data import (
    AuditLogTailer,
    load_latest_records,
    load_tc_worker_data,
    strip_fqdn,
    tail_audit_log,
)
from .display import MonitorDisplay
from .formatting import (
    compute_columns_and_widths,
    format_monitor_row,
    render_monitor_lines,
)

if TYPE_CHECKING:
    from ...cli import Args


def cmd_host_monitor(args: Args) -> None:
    """Monitor the latest audit record for hosts by tailing the audit log."""
    ensure_host_or_file(args.host)
    if is_host_file(args.host):
        host_file = Path(args.host)
        hosts = parse_host_list(host_file)
        host_source = str(host_file)
    else:
        hosts = [args.host]
        host_source = args.host

    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()

    if args.once and not audit_log.exists():
        raise FleetRollError(f"Audit log not found: {audit_log}")

    latest, latest_ok = load_latest_records(
        audit_log,
        hosts=hosts,
        override_path=args.override_path,
        role_path=args.role_path,
        vault_path=args.vault_path,
    )

    # Load TaskCluster worker data
    tc_workers_path = Path.home() / ".fleetroll" / TC_WORKERS_FILE_NAME
    tc_data = load_tc_worker_data(tc_workers_path)

    if args.once:
        if args.json:
            payload = {host: latest.get(host) for host in hosts}
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            header, lines = render_monitor_lines(
                hosts=hosts,
                latest=latest,
                latest_ok=latest_ok,
                tc_data=tc_data,
                max_width=0,
                cap_widths=False,
                col_sep="  ",
            )
            print(header)
            for line in lines:
                print(line)
        return

    if args.json or not sys.stdout.isatty():
        if args.json:
            payload = {host: latest.get(host) for host in hosts}
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            columns, widths = compute_columns_and_widths(
                hosts=hosts,
                latest=latest,
                latest_ok=latest_ok,
                tc_data=tc_data,
                max_width=0,
                cap_widths=False,
                sep_len=2,
            )
            header, lines = render_monitor_lines(
                hosts=hosts,
                latest=latest,
                latest_ok=latest_ok,
                tc_data=tc_data,
                max_width=0,
                cap_widths=False,
                col_sep="  ",
            )
            print(header)
            for line in lines:
                print(line)
        for record in tail_audit_log(
            audit_log,
            hosts=hosts,
            override_path=args.override_path,
            role_path=args.role_path,
            vault_path=args.vault_path,
            start_at_end=True,
        ):
            if args.json:
                print(json.dumps(record, sort_keys=True))
            else:
                if record.get("ok"):
                    latest_ok[record["host"]] = record
                host = record["host"]
                short_host = strip_fqdn(host)
                print(
                    format_monitor_row(
                        host,
                        record,
                        last_ok=latest_ok.get(host),
                        tc_data=tc_data.get(short_host),
                        columns=columns,
                        widths=widths,
                        col_sep="  ",
                    )
                )
        return

    def curses_main(stdscr) -> None:
        stdscr.nodelay(True)
        stdscr.timeout(200)
        display = MonitorDisplay(
            stdscr,
            hosts=hosts,
            host_source=host_source,
            latest=latest,
            latest_ok=latest_ok,
            tc_data=tc_data,
            tc_workers_path=tc_workers_path,
        )
        display.draw_screen()
        tailer = AuditLogTailer(
            audit_log,
            hosts=hosts,
            override_path=args.override_path,
            role_path=args.role_path,
            vault_path=args.vault_path,
            start_at_end=True,
        )
        while True:
            key = stdscr.getch()
            if display.handle_key(key):
                return
            record = tailer.poll()
            if record:
                display.update_record(record)
                display.draw_screen()
            if display.poll_tc_data():
                display.draw_screen()

    curses_wrapper(curses_main)
