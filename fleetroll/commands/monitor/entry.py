"""Monitor command entry point."""

from __future__ import annotations

import curses
import json
import logging
import sys
import time
from curses import wrapper as curses_wrapper
from pathlib import Path
from typing import TYPE_CHECKING

from ...data_provider import LocalProvider
from ...exceptions import FleetRollError
from ...notes import default_notes_path, load_latest_notes
from ...utils import (
    ensure_host_or_file,
    is_host_file,
    parse_host_list,
)
from .cache import ShaInfoCache
from .data import (
    build_row_values,
    get_host_sort_key,
    strip_fqdn,
    tail_audit_log,
)
from .display import MonitorDisplay
from .filter_history import filter_history_path
from .formatting import (
    compute_columns_and_widths,
    format_monitor_row,
    render_monitor_lines,
)
from .query import apply_query, parse_query_safe

if TYPE_CHECKING:
    from ...cli_types import HostMonitorArgs


def cmd_host_monitor(args: HostMonitorArgs) -> None:
    """Monitor the latest audit record for hosts by tailing the audit log."""
    from ...db import get_connection, get_db_path, init_db

    if args.hostname_only and not args.once:
        raise FleetRollError("--hostname-only requires --once")
    if args.hostname_only and args.json:
        raise FleetRollError("--hostname-only and --json are mutually exclusive")

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
    provider = LocalProvider(db_conn)

    try:
        latest, latest_ok = provider.load_latest_records(hosts=hosts)

        # Load TaskCluster worker data
        tc_data = provider.load_tc_workers(hosts=hosts)

        # Load GitHub refs data
        github_refs = provider.load_github_refs()

        # Load SHA info cache
        fleetroll_dir = Path.home() / ".fleetroll"
        overrides_dir = fleetroll_dir / "overrides"
        vault_dir = fleetroll_dir / "vault_yamls"
        sha_cache = ShaInfoCache(overrides_dir, vault_dir)
        sha_cache.load_all()

        # Load notes data
        notes_path = default_notes_path()
        notes_data = load_latest_notes(notes_path)

        if args.once:
            # Sort hosts according to --sort option
            sorted_hosts = sorted(
                hosts,
                key=lambda h: get_host_sort_key(
                    h, sort_field=args.sort, latest=latest, latest_ok=latest_ok
                ),
            )

            if args.filter:
                query = parse_query_safe(args.filter)
                if not query.is_empty():
                    row_dicts = []
                    for h in sorted_hosts:
                        values = build_row_values(
                            h,
                            latest.get(h),
                            last_ok=latest_ok.get(h),
                            tc_data=tc_data.get(strip_fqdn(h)),
                            sha_cache=sha_cache,
                            notes_data=notes_data,
                        )
                        values["_host"] = h
                        row_dicts.append(values)
                    filtered = apply_query(row_dicts, query)
                    sorted_hosts = [d["_host"] for d in filtered]

            if args.hostname_only:
                for h in sorted_hosts:
                    print(h)
            elif args.json:
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
                    notes_data=notes_data,
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
                    notes_data=notes_data,
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
                    notes_data=notes_data,
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
                            notes_data=notes_data,
                        )
                    )
            return

        filters_configs_dir = Path("configs/filters")

        display_ref: list[MonitorDisplay] = []

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
                provider=provider,
                github_refs=github_refs,
                sha_cache=sha_cache,
                notes_data=notes_data,
                notes_path=notes_path,
                filters_configs_dir=filters_configs_dir,
            )
            display_ref.append(display)
            display.load_history(filter_history_path())
            if args.filter:
                display.set_query(args.filter)
            display.draw_screen()
            tailer = provider.make_tailer(hosts=hosts)
            last_redraw_time = time.monotonic()
            while True:
                key = stdscr.getch()

                if display.handle_key(key, draw=False):
                    return

                # Track if we redrew the screen in this iteration
                redrew = False

                if key != -1:
                    if display.filter_bar_active or display.filters_popup_active:
                        # Drain any remaining buffered keys before redrawing so that
                        # paste (all chars arrive at once) causes one redraw, not one per char.
                        # Use timeout(0) so the drain is non-blocking — otherwise each
                        # getch() in the loop waits up to 200ms making navigation sluggish.
                        stdscr.timeout(0)
                        while True:
                            next_key = stdscr.getch()
                            if next_key == -1:
                                break
                            if display.handle_key(next_key, draw=False):
                                return
                        stdscr.timeout(200)
                    else:
                        # Flush buffered scroll/repeat events to avoid lag.
                        peek = stdscr.getch()
                        if peek != -1:
                            curses.flushinp()
                    display.draw_screen()
                    redrew = True

                record = tailer.poll()
                if record:
                    display.update_record(record)
                    display.draw_screen()
                    redrew = True
                if display.poll_tc_data():
                    display.draw_screen()
                    redrew = True
                if display.poll_github_data():
                    display.draw_screen()
                    redrew = True
                if display.poll_windows_pools_data():
                    display.draw_screen()
                    redrew = True
                if display.poll_sha_cache():
                    display.draw_screen()
                    redrew = True
                if display.poll_notes_data():
                    display.draw_screen()
                    redrew = True

                # Periodic redraw to update DATA column ages (every 2 seconds)
                now = time.monotonic()
                if not redrew and now - last_redraw_time >= 2.0:
                    display.draw_screen()
                    redrew = True

                if redrew:
                    last_redraw_time = now

        fleetroll_logger = logging.getLogger("fleetroll")
        stderr_handlers = [
            h
            for h in fleetroll_logger.handlers
            if isinstance(h, logging.StreamHandler)
            and getattr(h, "stream", None) in (sys.stderr, sys.stdout)
        ]
        log_path = Path.home() / ".fleetroll" / "monitor.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        for h in stderr_handlers:
            fleetroll_logger.removeHandler(h)
        fleetroll_logger.addHandler(file_handler)
        try:
            curses_wrapper(curses_main)
        finally:
            fleetroll_logger.removeHandler(file_handler)
            file_handler.close()
            for h in stderr_handlers:
                fleetroll_logger.addHandler(h)
            if display_ref:
                display_ref[0].save_history(filter_history_path())
    finally:
        db_conn.close()
