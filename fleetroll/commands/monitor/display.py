"""Curses-based UI display for monitor command."""

from __future__ import annotations

import datetime as dt
import sqlite3
import time
from curses import error as curses_error
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cache import ShaInfoCache

from ...utils import get_log_file_size
from .curses_colors import CursesColors
from .data import (
    age_seconds,
    build_row_values,
    detect_common_fqdn_suffix,
    get_host_sort_key,
    humanize_duration,
    load_github_refs_from_db,
    load_tc_worker_data_from_db,
    strip_fqdn,
)
from .header_renderer import HeaderInfo, HeaderRenderer
from .help_popup import draw_help_popup
from .row_renderer import RowRenderer
from .types import cycle_os_filter


def compute_visible_columns(
    all_columns: list[str],
    *,
    widths: dict[str, int],
    usable_width: int,
    col_offset: int,
) -> tuple[list[str], str, int, int]:
    """Determine which columns fit on screen with horizontal scrolling.

    Args:
        all_columns: Complete list of column names in display order
        widths: Column name to pixel/character width mapping
        usable_width: Available screen width in characters
        col_offset: Current horizontal scroll position (0-based index into scrollable cols)

    Returns:
        Tuple of (visible_columns, scroll_indicator, new_col_offset, max_col_offset) where:
        - visible_columns: Column names to display (frozen "host" + scrollable subset)
        - scroll_indicator: String with scroll arrows and position, e.g. " [▶ 1-3/5]"
        - new_col_offset: col_offset clamped to valid range
        - max_col_offset: Maximum valid col_offset value
    """
    frozen_col = "host"
    scrollable_cols = [c for c in all_columns if c != frozen_col]

    frozen_width = widths[frozen_col]
    available_width = max(usable_width - frozen_width - 3, 0)  # 3 for " | "

    # First pass: determine how many cols fit from col_offset to compute max_col_offset
    visible_scrollable: list[str] = []
    running_width = 0
    for col in scrollable_cols[col_offset:]:
        col_width = widths[col]
        separator_width = 3 if visible_scrollable else 0
        if running_width + col_width + separator_width <= available_width:
            visible_scrollable.append(col)
            running_width += col_width + separator_width
        else:
            break

    max_col_offset = max(len(scrollable_cols) - len(visible_scrollable), 0)
    col_offset = min(col_offset, max_col_offset)

    # Second pass: rebuild with clamped offset
    visible_scrollable = []
    running_width = 0
    for col in scrollable_cols[col_offset:]:
        col_width = widths[col]
        separator_width = 3 if visible_scrollable else 0
        if running_width + col_width + separator_width <= available_width:
            visible_scrollable.append(col)
            running_width += col_width + separator_width
        else:
            break

    columns = [frozen_col] + visible_scrollable

    scroll_indicator = ""
    if max_col_offset > 0:
        first_visible_idx = col_offset + 1
        last_visible_idx = col_offset + len(visible_scrollable)
        total_scrollable = len(scrollable_cols)
        arrows = ""
        if col_offset > 0:
            arrows += "◀ "
        if col_offset < max_col_offset:
            arrows += "▶"
        scroll_indicator = (
            f" [{arrows.strip()} {first_visible_idx}-{last_visible_idx}/{total_scrollable}]"
        )

    return columns, scroll_indicator, col_offset, max_col_offset


class MonitorDisplay:
    """Curses-based monitor display with encapsulated state."""

    def __init__(
        self,
        stdscr,
        *,
        hosts: list[str],
        host_source: str,
        latest: dict[str, dict[str, Any]],
        latest_ok: dict[str, dict[str, Any]],
        tc_data: dict[str, dict[str, Any]],
        db_conn: sqlite3.Connection,
        github_refs: dict[str, dict[str, Any]],
        sha_cache: ShaInfoCache | None = None,
    ) -> None:
        self.stdscr = stdscr
        self.hosts = hosts
        # Show just the filename if it's a path, to save space in the status line
        self.host_source = Path(host_source).name if "/" in host_source else host_source
        self.latest = latest
        self.latest_ok = latest_ok
        self.tc_data = tc_data
        self.db_conn = db_conn
        self.github_refs = github_refs
        self.sha_cache = sha_cache
        self._tc_poll_time = 0.0
        self._github_poll_time = 0.0
        self._sha_cache_poll_time = 0.0
        self.offset = 0
        self.col_offset = 0
        self.page_step = 1
        self.max_offset = 0
        self.max_col_offset = 0
        timestamps = [r.get("ts") for r in latest.values() if r.get("ts")]
        self.last_updated = max(timestamps, default=None) if timestamps else None
        self.fqdn_suffix = detect_common_fqdn_suffix(hosts)
        self.show_help = False
        self.sort_field = "host"  # Current sort field: "host" or "role"
        self.show_only_overrides = False  # Filter to show only hosts with overrides
        self.os_filter: str | None = None  # OS filter: None=all, "L"=Linux, "M"=macOS
        self.colors = CursesColors(stdscr)
        self.curses_mod = self.colors.curses_mod
        self.header_renderer = HeaderRenderer(safe_addstr=self.safe_addstr, colors=self.colors)
        self.row_renderer = RowRenderer(safe_addstr=self.safe_addstr, colors=self.colors)
        self.log_size_warnings = self._check_log_sizes()

    def safe_addstr(self, row: int, col: int, text: str, attr: int = 0) -> None:
        try:
            if attr:
                self.stdscr.addstr(row, col, text, attr)
            else:
                self.stdscr.addstr(row, col, text)
        except curses_error:
            return

    def handle_key(self, key: int, *, draw: bool = True) -> bool:
        """Handle a keypress. Returns True if we should exit.

        Args:
            key: The key code from getch()
            draw: Whether to redraw immediately (default True)
        """
        # Handle help popup dismissal - any key closes help (except '?' which opens it)
        if self.show_help:
            # Don't dismiss on '?' to avoid immediate close when opening
            # Don't dismiss on -1 (timeout/no key pressed)
            if key != ord("?") and key != -1:
                self.show_help = False
                if draw:
                    # Fast refresh from cached screen instead of expensive redraw
                    self.stdscr.touchwin()
                    self.stdscr.refresh()
            return False  # Don't process the key that closed help

        if key in (ord("q"), ord("Q")):
            return True
        if not self.curses_mod:
            return False
        if key == ord("?"):
            self.show_help = True
            self.draw_screen()
            return False
        if key in (self.curses_mod.KEY_ENTER, ord("\n"), ord("\r")):
            self.draw_screen()
            return False
        if key in (self.curses_mod.KEY_UP, ord("k")):
            self.offset = max(self.offset - self.page_step, 0)
            self.draw_screen()
        elif key in (self.curses_mod.KEY_DOWN, ord("j")):
            self.offset = min(self.offset + self.page_step, self.max_offset)
            self.draw_screen()
        elif key in (self.curses_mod.KEY_PPAGE,):
            self.offset = max(self.offset - self.page_step, 0)
            self.draw_screen()
        elif key in (self.curses_mod.KEY_NPAGE,):
            self.offset = min(self.offset + self.page_step, self.max_offset)
            self.draw_screen()
        elif key in (self.curses_mod.KEY_LEFT, ord("h")):
            self.col_offset = max(self.col_offset - 1, 0)
            self.draw_screen()
        elif key in (self.curses_mod.KEY_RIGHT, ord("l")):
            self.col_offset = min(self.col_offset + 1, self.max_col_offset)
            self.draw_screen()
        elif key == ord("r"):
            # Reload will be handled later if needed
            self.draw_screen()
        elif key in (ord("s"), ord("S")):
            # Cycle between host -> role -> ovr_sha sort
            if self.sort_field == "host":
                self.sort_field = "role"
            elif self.sort_field == "role":
                self.sort_field = "ovr_sha"
            else:
                self.sort_field = "host"
            self.offset = 0  # Reset to first page on sort change
            self.draw_screen()
        elif key == ord("o"):
            # Toggle override filter
            self.show_only_overrides = not self.show_only_overrides
            self.offset = 0  # Reset to first page on filter change
            self.draw_screen()
        elif key == ord("O"):
            # Cycle OS filter
            self.os_filter = cycle_os_filter(self.os_filter)
            self.offset = 0  # Reset to first page on filter change
            self.draw_screen()
        return False

    def _check_log_sizes(self) -> list[str]:
        """Check log file sizes and return warnings for files over threshold.

        Returns:
            List of warning strings, e.g., ["audit: 120M", "obs: 105M"]
        """
        warn_threshold_mb = 100
        bytes_per_mb = 1024 * 1024

        warnings = []
        fleetroll_dir = Path.home() / ".fleetroll"

        # Check audit.jsonl
        audit_path = fleetroll_dir / "audit.jsonl"
        audit_size_mb = get_log_file_size(audit_path) / bytes_per_mb
        if audit_size_mb >= warn_threshold_mb:
            warnings.append(f"audit: {audit_size_mb:.0f}M")

        # Check SQLite database
        from ...db import get_db_path

        db_file = get_db_path()
        db_size_mb = get_log_file_size(db_file) / bytes_per_mb
        if db_size_mb >= warn_threshold_mb:
            warnings.append(f"db: {db_size_mb:.0f}M")

        return warnings

    def poll_tc_data(self) -> bool:
        """Check if TC data changed and reload if needed. Returns True if reloaded."""
        now = time.monotonic()
        if now - self._tc_poll_time < 5.0:
            return False
        self._tc_poll_time = now
        # Commit to end any stale read transaction and see latest writes
        self.db_conn.commit()
        new_data = load_tc_worker_data_from_db(self.db_conn, hosts=self.hosts)
        if new_data != self.tc_data:
            self.tc_data = new_data
            self._touch_updated()
            return True
        return False

    def poll_github_data(self) -> bool:
        """Check if GitHub refs changed and reload if needed. Returns True if reloaded."""
        now = time.monotonic()
        if now - self._github_poll_time < 5.0:
            return False
        self._github_poll_time = now
        # Commit to end any stale read transaction and see latest writes
        self.db_conn.commit()
        new_data = load_github_refs_from_db(self.db_conn)
        if new_data != self.github_refs:
            self.github_refs = new_data
            self._touch_updated()
            return True
        return False

    def _touch_updated(self) -> None:
        """Update last_updated timestamp to current UTC time."""
        self.last_updated = dt.datetime.now(dt.UTC).isoformat()

    def poll_sha_cache(self) -> bool:
        """Re-scan override/vault dirs if SHA cache is stale. Returns True if changed."""
        if self.sha_cache is None:
            return False
        now = time.monotonic()
        if now - self._sha_cache_poll_time < 30.0:
            return False
        self._sha_cache_poll_time = now
        old_overrides = dict(self.sha_cache.override_cache)
        old_vault = dict(self.sha_cache.vault_cache)
        self.sha_cache.override_cache.clear()
        self.sha_cache.vault_cache.clear()
        self.sha_cache.load_all()
        changed = (
            self.sha_cache.override_cache != old_overrides
            or self.sha_cache.vault_cache != old_vault
        )
        if changed:
            self._touch_updated()
        return changed

    def update_record(self, record: dict[str, Any]) -> None:
        self.latest[record["host"]] = record
        if record.get("ok"):
            self.latest_ok[record["host"]] = record
        self._touch_updated()

    def _compute_screen_metrics(self, *, host_count: int | None = None) -> dict[str, Any]:
        """Compute screen dimensions and pagination metrics.

        Args:
            host_count: Number of hosts to display (defaults to self.hosts length)

        Returns:
            Dictionary containing:
            - height: Terminal height
            - width: Terminal width
            - usable_width: Width minus 1 for margin
            - page_size: Number of host rows per page
            - total_pages: Total pagination pages
            - current_page: Current page number (1-indexed)
            - updated: Human-readable last updated time
        """
        if host_count is None:
            host_count = len(self.hosts)
        height, width = self.stdscr.getmaxyx()
        usable_width = max(width - 1, 0)
        page_size = max(height - 2, 0)
        self.page_step = max(page_size, 1)
        self.max_offset = max(host_count - page_size, 0)
        self.offset = min(self.offset, self.max_offset)
        total_pages = max((host_count + self.page_step - 1) // self.page_step, 1)
        current_page = min(((self.offset + self.page_step - 1) // self.page_step) + 1, total_pages)
        updated_age = age_seconds(self.last_updated) if self.last_updated else None
        updated = humanize_duration(updated_age) if updated_age is not None else "never"

        return {
            "height": height,
            "width": width,
            "usable_width": usable_width,
            "page_size": page_size,
            "total_pages": total_pages,
            "current_page": current_page,
            "updated": updated,
        }

    def _compute_column_widths(
        self,
        sorted_hosts: list[str],
    ) -> tuple[list[str], dict[str, str], dict[str, int]]:
        """Compute column labels and widths based on host data.

        Args:
            sorted_hosts: List of hostnames in display order

        Returns:
            Tuple of (all_columns, labels_dict, widths_dict) where:
            - all_columns is the canonical list of column names
            - labels_dict maps column name to header label
            - widths_dict maps column name to computed width
        """
        all_columns = [
            "host",
            "os",
            "role",
            "vlt_sha",
            "sha",
            "uptime",
            "pp_last",
            "pp_sha",
            "pp_exp",
            "pp_match",
            "tc_act",
            "tc_j_sf",
            "tc_quar",
            "data",
            "healthy",
        ]

        labels = {
            "host": "HOST",
            "uptime": "UPTIME",
            "role": "ROLE",
            "os": "OS",
            "sha": "OVR_SHA",
            "vlt_sha": "VLT_SHA",
            "tc_quar": "TC_QUAR",
            "tc_act": "TC_ACT",
            "tc_j_sf": "TC_T_DUR",
            "pp_last": "PP_LAST",
            "pp_exp": "PP_EXP",
            "pp_sha": "PP_SHA",
            "pp_match": "PP_MATCH",
            "healthy": "HEALTHY",
            "data": "DATA",
        }

        # Add sort indicator to active column
        sort_field_to_column = {
            "host": "host",
            "role": "role",
            "ovr_sha": "sha",
        }
        active_column = sort_field_to_column.get(self.sort_field)
        if active_column and active_column in labels:
            labels[active_column] = labels[active_column] + " *"

        widths = {col: len(labels[col]) for col in all_columns}
        for host in sorted_hosts:
            short_host = strip_fqdn(host)
            tc_worker_data = self.tc_data.get(short_host)
            values = build_row_values(
                host,
                self.latest.get(host),
                last_ok=self.latest_ok.get(host),
                tc_data=tc_worker_data,
                fqdn_suffix=self.fqdn_suffix,
                sha_cache=self.sha_cache,
                github_refs=self.github_refs,
            )
            for col in all_columns:
                widths[col] = max(widths[col], len(values[col]))

        # Add padding for role/sha columns
        for col in ("role", "sha", "vlt_sha"):
            if col in widths:
                widths[col] += 2

        return all_columns, labels, widths

    def _compute_visible_columns(
        self,
        all_columns: list[str],
        *,
        widths: dict[str, int],
        usable_width: int,
    ) -> tuple[list[str], str]:
        """Determine which columns fit on screen with horizontal scrolling."""
        columns, scroll_indicator, col_offset, max_col_offset = compute_visible_columns(
            all_columns, widths=widths, usable_width=usable_width, col_offset=self.col_offset
        )
        self.col_offset = col_offset
        self.max_col_offset = max_col_offset
        return columns, scroll_indicator

    def _get_sort_key(self, hostname: str) -> tuple:
        """Extract sort key for a host based on current sort field.

        Returns tuple for stable multi-level sorting.
        Always includes hostname as secondary sort.
        """
        return get_host_sort_key(
            hostname, sort_field=self.sort_field, latest=self.latest, latest_ok=self.latest_ok
        )

    def _has_overrides(self, hostname: str) -> bool:
        """Check if a host has override configuration.

        Args:
            hostname: Hostname to check

        Returns:
            True if host has overrides, False otherwise
        """
        short_host = strip_fqdn(hostname)
        tc_worker_data = self.tc_data.get(short_host)
        values = build_row_values(
            hostname,
            self.latest.get(hostname),
            last_ok=self.latest_ok.get(hostname),
            tc_data=tc_worker_data,
            fqdn_suffix=self.fqdn_suffix,
            sha_cache=self.sha_cache,
            github_refs=self.github_refs,
        )
        sha_value = values.get("sha", "")
        return sha_value not in ("-", "?", "")

    def _get_host_os(self, hostname: str) -> str:
        """Get the OS abbreviation for a host.

        Args:
            hostname: Hostname to check

        Returns:
            OS abbreviation ("L", "M", "W", etc.)
        """
        short_host = strip_fqdn(hostname)
        tc_worker_data = self.tc_data.get(short_host)
        values = build_row_values(
            hostname,
            self.latest.get(hostname),
            last_ok=self.latest_ok.get(hostname),
            tc_data=tc_worker_data,
            fqdn_suffix=self.fqdn_suffix,
            sha_cache=self.sha_cache,
            github_refs=self.github_refs,
        )
        return values["os"]

    def draw_screen(self) -> None:
        self.stdscr.erase()

        # Sort and filter hosts
        all_hosts_sorted = sorted(self.hosts, key=self._get_sort_key)
        sorted_hosts = all_hosts_sorted
        if self.show_only_overrides:
            sorted_hosts = [h for h in sorted_hosts if self._has_overrides(h)]
        if self.os_filter is not None:
            sorted_hosts = [h for h in sorted_hosts if self._get_host_os(h) == self.os_filter]

        # Compute screen metrics with filtered host count
        metrics = self._compute_screen_metrics(host_count=len(sorted_hosts))

        # Compute column configuration
        all_columns, labels, widths = self._compute_column_widths(sorted_hosts)

        # Determine visible columns
        columns, scroll_indicator = self._compute_visible_columns(
            all_columns, widths=widths, usable_width=metrics["usable_width"]
        )

        # Draw headers
        # Pass filtered count if any filter is active
        filtered_count = (
            len(sorted_hosts) if (self.show_only_overrides or self.os_filter is not None) else None
        )
        header_info = HeaderInfo(
            sort_field=self.sort_field,
            show_only_overrides=self.show_only_overrides,
            os_filter=self.os_filter,
            fqdn_suffix=self.fqdn_suffix,
            host_source=self.host_source,
            total_hosts=len(self.hosts),
            log_size_warnings=self.log_size_warnings,
        )
        header_rows = self.header_renderer.draw_top_header(
            header_info=header_info,
            total_pages=metrics["total_pages"],
            current_page=metrics["current_page"],
            scroll_indicator=scroll_indicator,
            updated=metrics["updated"],
            usable_width=metrics["usable_width"],
            filtered_host_count=filtered_count,
        )
        self.header_renderer.draw_column_header(
            labels=labels, columns=columns, widths=widths, header_row=header_rows
        )

        # Adjust page size if using two-line header
        page_size = metrics["page_size"]
        if header_rows == 2:
            page_size = max(page_size - 1, 0)

        # Prepare categorical colors
        color_maps = self.colors.prepare_categorical_colors(
            all_hosts_sorted,
            latest=self.latest,
            latest_ok=self.latest_ok,
            tc_data=self.tc_data,
            fqdn_suffix=self.fqdn_suffix,
            sha_cache=self.sha_cache,
            github_refs=self.github_refs,
        )

        host_slice = (
            sorted_hosts[self.offset :]
            if page_size <= 0
            else sorted_hosts[self.offset : self.offset + page_size]
        )

        # Draw host rows
        for idx, host in enumerate(host_slice, start=1):
            row = idx + header_rows
            if row >= metrics["height"]:
                break
            render_data = self.row_renderer.compute_row_render_data(
                host,
                latest=self.latest,
                latest_ok=self.latest_ok,
                tc_data=self.tc_data,
                fqdn_suffix=self.fqdn_suffix,
                sha_cache=self.sha_cache,
                github_refs=self.github_refs,
            )
            self.row_renderer.draw_host_row(
                row, render_data=render_data, columns=columns, widths=widths, color_maps=color_maps
            )

        # Refresh main screen first, then draw help popup on top
        if self.show_help:
            self.stdscr.noutrefresh()
            draw_help_popup(self.stdscr, self.curses_mod, color_enabled=self.colors.color_enabled)
            self.curses_mod.doupdate()
        else:
            self.stdscr.refresh()
