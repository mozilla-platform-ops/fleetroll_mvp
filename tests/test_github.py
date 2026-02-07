"""Tests for fleetroll/github.py - GitHub API integration."""

from __future__ import annotations

import time
from unittest.mock import Mock, patch

from fleetroll.github import (
    collect_repo_branches,
    fetch_branch_shas,
    parse_github_repo_url,
    should_fetch,
)


class TestParseGithubRepoUrl:
    """Tests for parse_github_repo_url function."""

    def test_https_url_with_git_extension(self):
        """Parse HTTPS URL with .git extension."""
        result = parse_github_repo_url("https://github.com/aerickson/ronin_puppet.git")
        assert result == ("aerickson", "ronin_puppet")

    def test_https_url_without_git_extension(self):
        """Parse HTTPS URL without .git extension."""
        result = parse_github_repo_url("https://github.com/mozilla/gecko-dev")
        assert result == ("mozilla", "gecko-dev")

    def test_git_ssh_url(self):
        """Parse git@github.com SSH URL."""
        result = parse_github_repo_url("git@github.com:rcurranmoz/ronin_puppet.git")
        assert result == ("rcurranmoz", "ronin_puppet")

    def test_non_github_url(self):
        """Non-GitHub URL should return None."""
        result = parse_github_repo_url("https://gitlab.com/someuser/somerepo.git")
        assert result is None

    def test_invalid_url(self):
        """Invalid URL should return None."""
        result = parse_github_repo_url("not-a-url")
        assert result is None

    def test_empty_string(self):
        """Empty string should return None."""
        result = parse_github_repo_url("")
        assert result is None

    def test_repo_with_hyphen(self):
        """Parse repo name with hyphens."""
        result = parse_github_repo_url("https://github.com/mozilla-platform-ops/ronin_puppet.git")
        assert result == ("mozilla-platform-ops", "ronin_puppet")


class TestShouldFetch:
    """Tests for should_fetch function."""

    def test_file_does_not_exist(self, tmp_path):
        """Should return True if file doesn't exist."""
        refs_path = tmp_path / "github_refs.jsonl"
        assert should_fetch(refs_path) is True

    def test_file_is_old(self, tmp_path):
        """Should return True if file is older than interval."""
        refs_path = tmp_path / "github_refs.jsonl"
        refs_path.write_text("test\n")

        # Set mtime to 2 hours ago (7200 seconds)
        old_time = time.time() - 7200
        import os

        os.utime(refs_path, (old_time, old_time))

        assert should_fetch(refs_path) is True

    def test_file_is_recent(self, tmp_path):
        """Should return False if file is newer than interval."""
        refs_path = tmp_path / "github_refs.jsonl"
        refs_path.write_text("test\n")

        # Set mtime to 30 minutes ago (1800 seconds < 3600)
        recent_time = time.time() - 1800
        import os

        os.utime(refs_path, (recent_time, recent_time))

        assert should_fetch(refs_path) is False

    def test_file_exactly_at_interval(self, tmp_path):
        """Should return True if file is exactly at the interval threshold."""
        refs_path = tmp_path / "github_refs.jsonl"
        refs_path.write_text("test\n")

        # Set mtime to exactly 3600 seconds ago
        exact_time = time.time() - 3600
        import os

        os.utime(refs_path, (exact_time, exact_time))

        assert should_fetch(refs_path) is True


class TestCollectRepoBranches:
    """Tests for collect_repo_branches function."""

    def test_empty_directory(self, tmp_path):
        """Should return only default repo when directory is empty."""
        overrides_dir = tmp_path / "overrides"
        overrides_dir.mkdir()

        result = collect_repo_branches(overrides_dir)

        assert result == {("mozilla-platform-ops", "ronin_puppet"): {"master"}}

    def test_directory_does_not_exist(self, tmp_path):
        """Should return only default repo when directory doesn't exist."""
        overrides_dir = tmp_path / "nonexistent"

        result = collect_repo_branches(overrides_dir)

        assert result == {("mozilla-platform-ops", "ronin_puppet"): {"master"}}

    def test_parse_override_with_github_url(self, tmp_path):
        """Should extract repo and branch from override file."""
        overrides_dir = tmp_path / "overrides"
        overrides_dir.mkdir()

        override_content = """
PUPPET_REPO="https://github.com/aerickson/ronin_puppet.git"
PUPPET_BRANCH="my-feature-branch"
"""
        override_file = overrides_dir / "abc123"
        override_file.write_text(override_content)

        result = collect_repo_branches(overrides_dir)

        assert ("aerickson", "ronin_puppet") in result
        assert "my-feature-branch" in result[("aerickson", "ronin_puppet")]
        # Default repo should still be present
        assert ("mozilla-platform-ops", "ronin_puppet") in result

    def test_multiple_branches_same_repo(self, tmp_path):
        """Should collect multiple branches for the same repo."""
        overrides_dir = tmp_path / "overrides"
        overrides_dir.mkdir()

        # First override file
        override1 = overrides_dir / "abc123"
        override1.write_text(
            'PUPPET_REPO="https://github.com/user/repo.git"\nPUPPET_BRANCH="branch1"'
        )

        # Second override file with same repo, different branch
        override2 = overrides_dir / "def456"
        override2.write_text(
            'PUPPET_REPO="https://github.com/user/repo.git"\nPUPPET_BRANCH="branch2"'
        )

        result = collect_repo_branches(overrides_dir)

        assert ("user", "repo") in result
        assert result[("user", "repo")] == {"branch1", "branch2"}

    def test_ignore_non_github_repos(self, tmp_path):
        """Should ignore non-GitHub repos."""
        overrides_dir = tmp_path / "overrides"
        overrides_dir.mkdir()

        override_content = """
PUPPET_REPO="https://gitlab.com/user/repo.git"
PUPPET_BRANCH="main"
"""
        override_file = overrides_dir / "abc123"
        override_file.write_text(override_content)

        result = collect_repo_branches(overrides_dir)

        # Should only have default repo
        assert result == {("mozilla-platform-ops", "ronin_puppet"): {"master"}}

    def test_ignore_files_without_branch(self, tmp_path):
        """Should ignore files without PUPPET_BRANCH."""
        overrides_dir = tmp_path / "overrides"
        overrides_dir.mkdir()

        override_content = 'PUPPET_REPO="https://github.com/user/repo.git"\n'
        override_file = overrides_dir / "abc123"
        override_file.write_text(override_content)

        result = collect_repo_branches(overrides_dir)

        # Should only have default repo
        assert result == {("mozilla-platform-ops", "ronin_puppet"): {"master"}}


class TestFetchBranchShas:
    """Tests for fetch_branch_shas function."""

    @patch("fleetroll.github.requests.get")
    def test_successful_fetch(self, mock_get):
        """Should return list of branch refs on success."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {"ref": "refs/heads/master", "object": {"sha": "abc123"}},
            {"ref": "refs/heads/develop", "object": {"sha": "def456"}},
        ]
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = fetch_branch_shas("owner", "repo")

        assert len(result) == 2
        assert {"ref": "master", "sha": "abc123"} in result
        assert {"ref": "develop", "sha": "def456"} in result

    @patch("fleetroll.github.requests.get")
    def test_api_error(self, mock_get):
        """Should return empty list on API error."""
        import requests

        mock_get.side_effect = requests.RequestException("Network error")

        result = fetch_branch_shas("owner", "repo")

        assert result == []

    @patch("fleetroll.github.requests.get")
    def test_http_error(self, mock_get):
        """Should return empty list on HTTP error."""
        import requests

        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        result = fetch_branch_shas("owner", "repo")

        assert result == []

    @patch("fleetroll.github.requests.get")
    def test_filters_non_branch_refs(self, mock_get):
        """Should filter out non-branch refs (tags, etc)."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {"ref": "refs/heads/master", "object": {"sha": "abc123"}},
            {"ref": "refs/tags/v1.0", "object": {"sha": "tag123"}},
            {"ref": "refs/pull/123", "object": {"sha": "pr123"}},
        ]
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = fetch_branch_shas("owner", "repo")

        assert len(result) == 1
        assert result[0] == {"ref": "master", "sha": "abc123"}

    @patch("fleetroll.github.requests.get")
    def test_skip_refs_without_sha(self, mock_get):
        """Should skip refs without SHA."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {"ref": "refs/heads/master", "object": {"sha": "abc123"}},
            {"ref": "refs/heads/broken", "object": {}},
        ]
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = fetch_branch_shas("owner", "repo")

        assert len(result) == 1
        assert result[0] == {"ref": "master", "sha": "abc123"}
