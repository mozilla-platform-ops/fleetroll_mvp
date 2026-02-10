"""Monitor command entry point."""

from __future__ import annotations

import curses
import json
import sys
from curses import wrapper as curses_wrapper
from pathlib import Path
from typing import TYPE_CHECKING

from ...constants import GITHUB_REFS_FILE_NAME
from ...exceptions import FleetRollError
from ...utils import (
    ensure_host_or_file,
    is_host_file,
    parse_host_list,
)
from .cache import ShaInfoCache
from .data import (
    AuditLogTailer,
    get_host_sort_key,
    load_github_refs,
    load_latest_records,
    load_tc_worker_data_from_db,
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
    from ...cli_types import HostMonitorArgs


def cmd_host_monitor(args: HostMonitorArgs) -> None:
    """Monitor the latest audit record for hosts by tailing the audit log."""
    from ...db import get_connection, get_db_path, init_db

    ensure_host_or_file(args.host)
    if is_host_file(args.host):
        host_file = Path(args.host)
        hosts = parse_host_list(host_file)
        host_source = str(host_file)
    else:
        hosts = [args.host]
        host_source = args.host

    # Initialize SQLite database
    db_path = get_db_path()
    init_db(db_path)

    if args.once and not db_path.exists():
        raise FleetRollError(f"Database not found: {db_path}")

    db_conn = get_connection(db_path)

    try:
        latest, latest_ok = load_latest_records(
            db_conn,
            hosts=hosts,
        )

        # Load TaskCluster worker data
        tc_data = load_tc_worker_data_from_db(db_conn, hosts=hosts)

        # Load GitHub refs data
        github_refs_path = Path.home() / ".fleetroll" / GITHUB_REFS_FILE_NAME
        github_refs = load_github_refs(github_refs_path)

        # Load SHA info cache
        fleetroll_dir = Path.home() / ".fleetroll"
        overrides_dir = fleetroll_dir / "overrides"
        vault_dir = fleetroll_dir / "vault_yamls"
        sha_cache = ShaInfoCache(overrides_dir, vault_dir)
        sha_cache.load_all()

        if args.once:
            # Sort hosts according to --sort option
            sorted_hosts = sorted(
                hosts,
                key=lambda h: get_host_sort_key(
                    h, sort_field=args.sort, latest=latest, latest_ok=latest_ok
                ),
            )

            if args.json:
                payload = {host: latest.get(host) for host in sorted_hosts}
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                header, lines = render_monitor_lines(
                    hosts=sorted_hosts,
                    latest=latest,
                    latest_ok=latest_ok,
                    tc_data=tc_data,
                    max_width=0,
                    cap_widths=False,
                    col_sep="  ",
                    sha_cache=sha_cache,
                )
                print(header)
                for line in lines:
                    print(line)
            return

        if args.json or not sys.stdout.isatty():
            # Sort hosts for non-interactive display
            sorted_hosts = sorted(
                hosts,
                key=lambda h: get_host_sort_key(
                    h, sort_field=args.sort, latest=latest, latest_ok=latest_ok
                ),
            )

            columns = None
            widths = None
            if args.json:
                payload = {host: latest.get(host) for host in sorted_hosts}
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                columns, widths = compute_columns_and_widths(
                    hosts=sorted_hosts,
                    latest=latest,
                    latest_ok=latest_ok,
                    tc_data=tc_data,
                    max_width=0,
                    cap_widths=False,
                    sep_len=2,
                    sha_cache=sha_cache,
                    github_refs=github_refs,
                )
                header, lines = render_monitor_lines(
                    hosts=sorted_hosts,
                    latest=latest,
                    latest_ok=latest_ok,
                    tc_data=tc_data,
                    max_width=0,
                    cap_widths=False,
                    col_sep="  ",
                    sha_cache=sha_cache,
                    github_refs=github_refs,
                )
                print(header)
                for line in lines:
                    print(line)
            for record in tail_audit_log(
                db_conn,
                hosts=hosts,
                latest=latest,
            ):
                if args.json:
                    print(json.dumps(record, sort_keys=True))
                elif columns is not None and widths is not None:
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
                            github_refs=github_refs,
                            sha_cache=sha_cache,
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
                db_conn=db_conn,
                github_refs=github_refs,
                github_refs_path=github_refs_path,
                sha_cache=sha_cache,
            )
            display.draw_screen()
            tailer = AuditLogTailer(
                db_conn,
                hosts=hosts,
                latest=latest,
            )
            while True:
                key = stdscr.getch()

                if display.handle_key(key, draw=False):
                    return

                # After processing a key, check if there's more input pending
                # If so, flush it to avoid lag from processing hundreds of scroll events
                if key != -1:
                    # Peek to see if there's pending input
                    stdscr.nodelay(True)
                    peek = stdscr.getch()
                    if peek != -1:
                        # There's pending input - likely rapid scrolling
                        # Flush the entire input buffer to avoid lag
                        curses.flushinp()
                    display.draw_screen()

                record = tailer.poll()
                if record:
                    display.update_record(record)
                    display.draw_screen()
                if display.poll_tc_data():
                    display.draw_screen()
                if display.poll_github_data():
                    display.draw_screen()

        curses_wrapper(curses_main)
    finally:
        db_conn.close()
