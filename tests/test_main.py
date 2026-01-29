"""Tests for fleetroll/__main__.py - module entry point."""

from __future__ import annotations


class TestMainModule:
    """Tests for __main__ module entry point."""

    def test_main_is_importable(self):
        """Verify that __main__ module can be imported and main is callable."""
        from fleetroll.__main__ import main

        assert callable(main)
