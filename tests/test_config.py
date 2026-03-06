"""Tests for fleetroll/config.py - configuration loading."""

from __future__ import annotations

from unittest.mock import patch

from fleetroll.config import load_config


class TestLoadConfig:
    """Tests for load_config function."""

    def test_missing_file_returns_empty_dict(self, tmp_path):
        """Missing config file returns {}."""
        with patch("fleetroll.config.Path.home", return_value=tmp_path):
            result = load_config()
        assert result == {}

    def test_valid_toml_returns_parsed_dict(self, tmp_path):
        """Valid TOML file returns parsed dict."""
        config_dir = tmp_path / ".fleetroll"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text('[github]\napi_token = "ghp_test_token"\n')

        with patch("fleetroll.config.Path.home", return_value=tmp_path):
            result = load_config()

        assert result == {"github": {"api_token": "ghp_test_token"}}

    def test_invalid_toml_returns_empty_dict(self, tmp_path):
        """Invalid TOML returns {}."""
        config_dir = tmp_path / ".fleetroll"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("this is not valid toml ][[\n")

        with patch("fleetroll.config.Path.home", return_value=tmp_path):
            result = load_config()

        assert result == {}

    def test_empty_toml_returns_empty_dict(self, tmp_path):
        """Empty TOML file returns {}."""
        config_dir = tmp_path / ".fleetroll"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("")

        with patch("fleetroll.config.Path.home", return_value=tmp_path):
            result = load_config()

        assert result == {}
