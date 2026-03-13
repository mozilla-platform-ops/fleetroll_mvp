"""Tests for categorical color combo filtering in colors.py."""

from __future__ import annotations

import pytest
from fleetroll.commands.monitor.colors import (
    EXTENDED_FG_BG_COMBOS,
    FG_BG_COMBOS,
    STATE_INDICATOR_BG,
    STATE_INDICATOR_FG,
    get_categorical_combos,
)


class TestGetCategoricalCombos:
    def test_excludes_state_indicator_combos(self):
        """State-indicator fg on black/white bg must be absent."""
        combos = get_categorical_combos(include_extended=False)
        for _pair_num, fg, bg, _desc in combos:
            assert not (fg in STATE_INDICATOR_FG and bg in STATE_INDICATOR_BG), (
                f"State-indicator combo should be excluded: {fg}/{bg}"
            )

    def test_basic_only_count(self):
        """With include_extended=False, should return 21 basic combos."""
        combos = get_categorical_combos(include_extended=False)
        assert len(combos) == 21

    def test_with_extended_count(self):
        """With include_extended=True, should return 45 combos total."""
        combos = get_categorical_combos(include_extended=True)
        assert len(combos) == 45

    def test_basic_combo_pair_numbers(self):
        """Basic combos use pair numbers 27+ (one per FG_BG_COMBOS entry)."""
        combos_basic = get_categorical_combos(include_extended=False)
        pair_nums = {pair_num for pair_num, _, _, _ in combos_basic}
        # All pair numbers must be >= 27 and < 52 (basic range)
        assert all(27 <= p < 52 for p in pair_nums)

    def test_extended_combo_pair_numbers(self):
        """Extended combos use pair numbers 52+."""
        all_combos = get_categorical_combos(include_extended=True)
        basic_combos = get_categorical_combos(include_extended=False)
        extended_only = [c for c in all_combos if c not in basic_combos]
        assert len(extended_only) == len(EXTENDED_FG_BG_COMBOS)
        pair_nums = {pair_num for pair_num, _, _, _ in extended_only}
        assert all(p >= 52 for p in pair_nums)

    def test_extended_combos_appended_after_basic(self):
        """Extended combos appear after basic combos in the returned list."""
        combos = get_categorical_combos(include_extended=True)
        basic_count = sum(1 for pair_num, _, _, _ in combos if pair_num < 52)
        extended_count = sum(1 for pair_num, _, _, _ in combos if pair_num >= 52)
        # Basic should come first
        first_extended_pos = next(i for i, (p, _, _, _) in enumerate(combos) if p >= 52)
        assert first_extended_pos == basic_count
        assert basic_count == 21
        assert extended_count == 24

    def test_excluded_combos_are_exactly_four(self):
        """Exactly 4 combos should be excluded from FG_BG_COMBOS."""
        excluded = [
            (fg, bg)
            for fg, bg, _ in FG_BG_COMBOS
            if fg in STATE_INDICATOR_FG and bg in STATE_INDICATOR_BG
        ]
        assert len(excluded) == 4

    def test_known_excluded_combos(self):
        """Verify the 4 specifically excluded combos."""
        combos_basic = get_categorical_combos(include_extended=False)
        basic_fg_bg = {(fg, bg) for _, fg, bg, _ in combos_basic}

        for excluded_pair in [
            ("green", "black"),
            ("yellow", "black"),
            ("red", "black"),
            ("red", "white"),
        ]:
            assert excluded_pair not in basic_fg_bg, f"Should be excluded: {excluded_pair}"

    def test_return_type(self):
        """Each combo is a 4-tuple of (int, str, str, str)."""
        for combo in get_categorical_combos():
            assert len(combo) == 4
            pair_num, fg, bg, desc = combo
            assert isinstance(pair_num, int)
            assert isinstance(fg, str)
            assert isinstance(bg, str)
            assert isinstance(desc, str)

    @pytest.mark.parametrize("include_extended", [True, False])
    def test_no_duplicate_pair_numbers(self, include_extended: bool):
        """Pair numbers must be unique across all returned combos."""
        combos = get_categorical_combos(include_extended=include_extended)
        pair_nums = [pair_num for pair_num, _, _, _ in combos]
        assert len(pair_nums) == len(set(pair_nums))
