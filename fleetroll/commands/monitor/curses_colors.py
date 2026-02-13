"""Curses color initialization and attribute management for monitor display."""

from __future__ import annotations

from collections.abc import Iterable
from curses import error as curses_error
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .colors import EXTENDED_COLORS, FG_BG_COMBOS, build_color_mapping
from .data import build_row_values, strip_fqdn

if TYPE_CHECKING:
    from .cache import ShaInfoCache


@dataclass
class CursesAttrs:
    """Named curses attributes for consistent styling.

    Attributes:
        fleetroll_attr: Attribute for "fleetroll" branding
        header_data_attr: Attribute for header data sections
        column_attr: Attribute for column headers
        warning_attr: Attribute for warning messages
    """

    fleetroll_attr: int
    header_data_attr: int
    column_attr: int
    warning_attr: int


class CursesColors:
    """Curses color initialization and attribute management.

    This class encapsulates all color-related logic for the monitor display,
    including color pair setup, feature detection, and color attribute resolution
    for different data types (thresholds, categorical values, etc.).
    """

    def __init__(self, stdscr) -> None:
        """Initialize curses colors and detect terminal capabilities.

        Args:
            stdscr: The curses screen object
        """
        self.stdscr = stdscr
        self.curses_mod = None
        self.color_enabled = False
        self.extended_colors = False
        self.attrs = CursesAttrs(
            fleetroll_attr=0,
            header_data_attr=0,
            column_attr=0,
            warning_attr=0,
        )
        self._init_curses()

    def _init_curses(self) -> None:
        """Initialize curses with color support and feature detection."""
        try:
            import curses

            self.curses_mod = curses
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_CYAN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
                curses.init_pair(3, curses.COLOR_MAGENTA, -1)
                curses.init_pair(4, curses.COLOR_GREEN, -1)
                curses.init_pair(5, curses.COLOR_YELLOW, -1)
                curses.init_pair(6, curses.COLOR_RED, -1)
                curses.init_pair(7, curses.COLOR_BLUE, -1)
                curses.init_pair(8, curses.COLOR_CYAN, -1)
                curses.init_pair(9, curses.COLOR_GREEN, -1)
                curses.init_pair(10, curses.COLOR_MAGENTA, -1)
                curses.init_pair(11, curses.COLOR_YELLOW, -1)
                curses.init_pair(12, curses.COLOR_RED, -1)
                curses.init_pair(13, curses.COLOR_YELLOW, -1)
                curses.init_pair(14, curses.COLOR_MAGENTA, -1)
                curses.init_pair(15, curses.COLOR_WHITE, -1)
                curses.init_pair(16, curses.COLOR_BLACK, -1)
                curses.init_pair(17, curses.COLOR_YELLOW, curses.COLOR_BLACK)
                curses.init_pair(18, curses.COLOR_WHITE, curses.COLOR_BLACK)
                # Extended 256 colors for more distinct palette (pairs 19-26)
                if curses.COLORS >= 256:
                    for i, (_, color_code) in enumerate(EXTENDED_COLORS, start=19):
                        curses.init_pair(i, color_code, -1)
                # High-contrast fg/bg combinations for extended palette (pairs 27+)
                # Using shared FG_BG_COMBOS from colors module
                curses_color_map = {
                    "black": curses.COLOR_BLACK,
                    "red": curses.COLOR_RED,
                    "green": curses.COLOR_GREEN,
                    "yellow": curses.COLOR_YELLOW,
                    "blue": curses.COLOR_BLUE,
                    "magenta": curses.COLOR_MAGENTA,
                    "cyan": curses.COLOR_CYAN,
                    "white": curses.COLOR_WHITE,
                }
                for i, (fg_name, bg_name, _) in enumerate(FG_BG_COMBOS, start=27):
                    if i < curses.COLOR_PAIRS:
                        fg = curses_color_map[fg_name]
                        bg = curses_color_map[bg_name]
                        curses.init_pair(i, fg, bg)
                self.color_enabled = True
                # Detect 256-color support (for future use)
                if curses.COLORS >= 256:
                    self.extended_colors = True
            self.attrs = CursesAttrs(
                fleetroll_attr=curses.A_BOLD | (curses.color_pair(1) if self.color_enabled else 0),
                header_data_attr=curses.color_pair(2) if self.color_enabled else 0,
                column_attr=curses.A_BOLD | (curses.color_pair(3) if self.color_enabled else 0),
                warning_attr=curses.color_pair(5) if self.color_enabled else 0,
            )
        except curses_error:
            return

    def threshold_color_attr(self, seconds_value: int | None, thresholds: tuple[int, int]) -> int:
        """Color by thresholds: green if < thresholds[0], yellow if < thresholds[1], red otherwise.

        Args:
            seconds_value: Value in seconds
            thresholds: (green_threshold, yellow_threshold) in seconds

        Returns:
            Color attribute for curses
        """
        if not self.color_enabled:
            return 0
        if seconds_value is None:
            return 0
        green_max, yellow_max = thresholds
        if seconds_value < green_max:
            return self.curses_mod.color_pair(4)  # GREEN
        if seconds_value < yellow_max:
            return self.curses_mod.color_pair(5)  # YELLOW
        return self.curses_mod.color_pair(6)  # RED

    def uptime_attr(self, seconds_value: int | None) -> int:
        """Color uptime: green < 1h, yellow < 6h, red >= 6h."""
        return self.threshold_color_attr(seconds_value, (60 * 60, 6 * 60 * 60))

    def last_ok_attr(self, seconds_value: int | None) -> int:
        """Color last_ok age: green < 5m, yellow < 30m, red >= 30m."""
        return self.threshold_color_attr(seconds_value, (5 * 60, 30 * 60))

    def tc_act_attr(self, seconds_value: int | None) -> int:
        """Color TC last active: green < 5m, yellow < 1h, red >= 1h."""
        return self.threshold_color_attr(seconds_value, (5 * 60, 60 * 60))

    def pp_last_attr(self, seconds_value: int | None, *, failed: bool = False) -> int:
        """Color PP_LAST: green < 1h (success), yellow < 6h (success), red >= 6h or failed."""
        if not self.color_enabled:
            return 0
        if failed:
            return self.curses_mod.color_pair(6)  # RED
        return self.threshold_color_attr(seconds_value, (60 * 60, 6 * 60 * 60))

    def pp_match_attr(self, value: str) -> int:
        """Color PP_MATCH: green=Y, yellow=N, gray=-."""
        if not self.color_enabled:
            return 0
        if value == "Y":
            return self.curses_mod.color_pair(4)  # GREEN
        if value == "N":
            return self.curses_mod.color_pair(5)  # YELLOW
        return 0  # gray/default for "-"

    def ro_health_attr(self, value: str) -> int:
        """Color RO_HEALTH: green=Y, red=N, gray=-."""
        if not self.color_enabled:
            return 0
        if value == "Y":
            return self.curses_mod.color_pair(4)  # GREEN
        if value == "N":
            return self.curses_mod.color_pair(6)  # RED
        return 0  # gray/default for "-"

    def tc_quar_attr(self, value: str) -> int:
        """Color TC_QUAR: red=YES (quarantined), gray=-."""
        if not self.color_enabled:
            return 0
        if value == "YES":
            return self.curses_mod.color_pair(6)  # RED
        return 0  # gray/default for "-"

    def tc_j_sf_attr(self, task_state: str | None) -> int:
        """Color TC_T_DUR based on task completion state.

        Args:
            task_state: TaskCluster task state (COMPLETED/EXCEPTION/FAILED/RUNNING/PENDING)

        Returns:
            Color attribute: green=completed, yellow=exception, red=failed, default=other
        """
        if not self.color_enabled:
            return 0
        if not task_state:
            return 0
        # TaskCluster returns state in uppercase
        state_upper = task_state.upper()
        if state_upper == "COMPLETED":
            return self.curses_mod.color_pair(4)  # GREEN
        if state_upper == "EXCEPTION":
            return self.curses_mod.color_pair(5)  # YELLOW
        if state_upper == "FAILED":
            return self.curses_mod.color_pair(6)  # RED
        return 0  # gray/default for running/pending/unknown

    def build_color_map(
        self,
        values: Iterable[str],
        *,
        palette: list[int],
        base_attr: int = 0,
        seed: int = 0,
    ) -> dict[str, int]:
        """Build a color map for categorical values.

        Uses two tiers (reversed colors removed to avoid terminal-dependent conflicts):
        1. Basic colors from palette (14 colors)
        2. High-contrast fg/bg combinations (19 pairs starting at 27)

        Total capacity: 33 unique colors (sufficient for up to 33 unique values).

        Adjacent seeds (0, 1, 2) are automatically spread out to maximize
        visual distinction between columns.

        Args:
            values: Unique values to assign colors to
            palette: List of color_pair() values for basic colors
            base_attr: Base attribute to OR with all colors
            seed: Column index (0, 1, 2...) - automatically spread for distinction

        Returns:
            Dictionary mapping value to curses attribute
        """
        if not self.color_enabled:
            return {}

        palette_size = len(palette)
        total_capacity = palette_size + len(FG_BG_COMBOS)

        # Use shared color mapping algorithm
        index_mapping = build_color_mapping(
            values,
            total_capacity=total_capacity,
            seed=seed,
        )

        # Convert color indices to curses attributes
        result: dict[str, int] = {}
        for value, color_index in index_mapping.items():
            if color_index < palette_size:
                # Tier 1: Basic colors from palette
                attr = palette[color_index] | base_attr
            else:
                # Tier 2: High-contrast fg/bg combinations
                fg_bg_idx = color_index - palette_size
                pair_num = 27 + fg_bg_idx
                attr = self.curses_mod.color_pair(pair_num) | base_attr

            result[value] = attr

        return result

    def prepare_categorical_colors(
        self,
        sorted_hosts: list[str],
        *,
        latest: dict[str, dict[str, Any]],
        latest_ok: dict[str, dict[str, Any]],
        tc_data: dict[str, dict[str, Any]],
        fqdn_suffix: str | None,
        sha_cache: ShaInfoCache | None,
        github_refs: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, int]]:
        """Build color maps for categorical columns (role, sha, vlt_sha).

        Args:
            sorted_hosts: List of hostnames to analyze
            latest: Latest audit records by hostname
            latest_ok: Latest successful audit records by hostname
            tc_data: TaskCluster worker data by short hostname
            fqdn_suffix: Optional common FQDN suffix
            sha_cache: Optional SHA cache
            github_refs: GitHub reference data

        Returns:
            Dictionary mapping column name to color map:
            {
                "sha": {value: curses_attr, ...},
                "vlt_sha": {value: curses_attr, ...},
                "role": {value: curses_attr, ...}
            }
        """
        sha_values = set()
        vlt_sha_values = set()
        role_values = set()
        for host in sorted_hosts:
            short_host = strip_fqdn(host)
            tc_worker_data = tc_data.get(short_host)
            values = build_row_values(
                host,
                latest.get(host),
                last_ok=latest_ok.get(host),
                tc_data=tc_worker_data,
                fqdn_suffix=fqdn_suffix,
                sha_cache=sha_cache,
                github_refs=github_refs,
            )
            sha = values.get("sha", "")
            vlt_sha = values.get("vlt_sha", "")
            role = values.get("role", "")
            if sha and sha not in ("-", "?"):
                sha_values.add(sha)
            if vlt_sha and vlt_sha not in ("-", "?"):
                vlt_sha_values.add(vlt_sha)
            if role and role not in ("-", "?", "missing"):
                role_values.add(role)

        # Use 14-color palette: 7 standard + 8 extended (if available)
        # No reversed colors to avoid terminal-dependent conflicts
        # Total: 14 base + 19 fg/bg = 33 unique colors
        sha_palette = [
            # Standard 7 colors
            self.curses_mod.color_pair(7),  # blue
            self.curses_mod.color_pair(8),  # cyan
            self.curses_mod.color_pair(9),  # green
            self.curses_mod.color_pair(10),  # magenta
            self.curses_mod.color_pair(11),  # yellow
            self.curses_mod.color_pair(6),  # red
            self.curses_mod.color_pair(15),  # white
        ]
        # Add extended colors if 256-color terminal
        if self.extended_colors:
            sha_palette.extend(
                [
                    self.curses_mod.color_pair(19),  # bright-orange
                    self.curses_mod.color_pair(20),  # bright-purple
                    self.curses_mod.color_pair(21),  # hot-pink
                    self.curses_mod.color_pair(22),  # teal
                    self.curses_mod.color_pair(23),  # maroon
                    self.curses_mod.color_pair(24),  # gold
                    self.curses_mod.color_pair(25),  # forest-green
                    self.curses_mod.color_pair(26),  # orange-red
                ]
            )
        role_palette = [
            self.curses_mod.color_pair(12),  # red
            self.curses_mod.color_pair(13),  # yellow
            self.curses_mod.color_pair(14),  # magenta
            self.curses_mod.color_pair(7),  # blue
            self.curses_mod.color_pair(8),  # cyan
            self.curses_mod.color_pair(9),  # green
            self.curses_mod.color_pair(10),  # magenta
            self.curses_mod.color_pair(11),  # yellow
        ]

        sha_colors = self.build_color_map(sha_values, palette=sha_palette, seed=0)
        vlt_sha_colors = self.build_color_map(vlt_sha_values, palette=sha_palette, seed=1)
        role_colors = self.build_color_map(
            role_values, palette=role_palette, base_attr=self.curses_mod.A_BOLD, seed=2
        )

        return {
            "sha": sha_colors,
            "vlt_sha": vlt_sha_colors,
            "role": role_colors,
        }
