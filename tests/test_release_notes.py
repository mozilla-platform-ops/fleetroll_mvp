"""Tests for tools/dev/release_notes.py - pure utility functions."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from tools.dev.release_notes import (
    VersionRange,
    assign_beads_to_versions,
    classify_commits,
    detect_version_ranges,
    extract_bead_id,
    extract_version_from_toml,
    filter_beads_by_date,
    format_debug_log,
    generate_notes_for_range,
    group_beads_by_type,
    parse_bead_close_commits_from_diff,
    parse_git_log_line,
    render_bead_line,
    render_commit_line,
    render_markdown,
)


class TestExtractVersionFromToml:
    def test_basic_version(self):
        content = '[project]\nname = "foo"\nversion = "1.2.3"\n'
        assert extract_version_from_toml(content) == "1.2.3"

    def test_missing_version(self):
        content = '[project]\nname = "foo"\n'
        assert extract_version_from_toml(content) is None

    def test_version_with_spaces(self):
        content = 'version  =  "0.1.0"\n'
        assert extract_version_from_toml(content) == "0.1.0"

    def test_ignores_inline_comment_style(self):
        content = '[project]\nversion = "2.0.0" # latest\n'
        assert extract_version_from_toml(content) == "2.0.0"

    def test_multiline_ignores_wrong_section(self):
        content = '[other]\nversion = "9.9.9"\n[project]\nversion = "1.0.0"\n'
        # Should return the first match
        assert extract_version_from_toml(content) == "9.9.9"


class TestFilterBeadsByDate:
    def _bead(self, bead_id, closed_at):
        return {"id": bead_id, "closed_at": closed_at}

    def test_no_start_includes_all_before_end(self):
        beads = [
            self._bead("a", "2026-01-01T00:00:00Z"),
            self._bead("b", "2026-02-01T00:00:00Z"),
        ]
        result = filter_beads_by_date(beads, start=None, end="2026-02-01T00:00:00Z")
        assert [b["id"] for b in result] == ["a", "b"]

    def test_excludes_beads_after_end(self):
        beads = [
            self._bead("a", "2026-01-01T00:00:00Z"),
            self._bead("b", "2026-03-01T00:00:00Z"),
        ]
        result = filter_beads_by_date(beads, start=None, end="2026-02-01T00:00:00Z")
        assert [b["id"] for b in result] == ["a"]

    def test_start_is_exclusive(self):
        beads = [
            self._bead("a", "2026-01-01T00:00:00Z"),
            self._bead("b", "2026-01-15T00:00:00Z"),
            self._bead("c", "2026-02-01T00:00:00Z"),
        ]
        # start = 2026-01-01, so beads closed ON that date are excluded
        result = filter_beads_by_date(
            beads,
            start="2026-01-01T00:00:00Z",
            end="2026-02-01T00:00:00Z",
        )
        assert [b["id"] for b in result] == ["b", "c"]

    def test_empty_beads(self):
        result = filter_beads_by_date([], start=None, end="2026-02-01T00:00:00Z")
        assert result == []

    def test_bead_without_closed_at_excluded(self):
        beads = [{"id": "x", "title": "no date"}]
        result = filter_beads_by_date(beads, start=None, end="2026-02-01T00:00:00Z")
        assert result == []


class TestGroupBeadsByType:
    def _bead(self, bead_id, btype, priority=2):
        return {"id": bead_id, "issue_type": btype, "priority": priority, "title": bead_id}

    def test_groups_by_type(self):
        beads = [
            self._bead("a", "feature"),
            self._bead("b", "bug"),
            self._bead("c", "feature"),
        ]
        groups = group_beads_by_type(beads)
        assert set(groups.keys()) == {"feature", "bug"}
        assert len(groups["feature"]) == 2
        assert len(groups["bug"]) == 1

    def test_sorted_by_priority_then_title(self):
        beads = [
            self._bead("z-feature", "feature", priority=2),
            self._bead("a-feature", "feature", priority=2),
            self._bead("p0-feature", "feature", priority=0),
        ]
        groups = group_beads_by_type(beads)
        ids = [b["id"] for b in groups["feature"]]
        assert ids == ["p0-feature", "a-feature", "z-feature"]

    def test_empty_list(self):
        assert group_beads_by_type([]) == {}

    def test_default_type_is_task(self):
        beads = [{"id": "x", "title": "x"}]  # no issue_type key
        groups = group_beads_by_type(beads)
        assert "task" in groups


class TestParseGitLogLine:
    def test_valid_line(self):
        line = "abc1234|2026-01-01T10:00:00+00:00|Fix the bug"
        result = parse_git_log_line(line)
        assert result == {
            "sha": "abc1234",
            "date": "2026-01-01T10:00:00+00:00",
            "subject": "Fix the bug",
        }

    def test_subject_with_pipes(self):
        line = "abc1234|2026-01-01T10:00:00+00:00|Subject with | pipe in it"
        result = parse_git_log_line(line)
        assert result["subject"] == "Subject with | pipe in it"

    def test_invalid_line(self):
        assert parse_git_log_line("no-pipes-here") is None

    def test_two_parts_only(self):
        assert parse_git_log_line("sha|date") is None


class TestExtractBeadId:
    def test_extracts_id(self):
        assert extract_bead_id("Fix bug (mvp-1ab)") == "mvp-1ab"

    def test_none_when_no_bead(self):
        assert extract_bead_id("Fix bug without bead") is None

    def test_at_end_of_subject(self):
        assert extract_bead_id("Add feature (mvp-xyz9)") == "mvp-xyz9"

    def test_no_match_without_parens(self):
        assert extract_bead_id("mvp-1ab without parens") is None


class TestClassifyCommits:
    def _commit(self, sha, subject):
        return {"sha": sha, "date": "2026-01-01", "subject": subject}

    def test_covered_vs_orphan(self):
        commits = [
            self._commit("aaa", "Fix bug (mvp-abc)"),
            self._commit("bbb", "Update docs"),
            self._commit("ccc", "Add feature (mvp-xyz)"),
        ]
        bead_ids = {"mvp-abc", "mvp-xyz"}
        covered, orphans = classify_commits(commits, bead_ids)
        assert len(covered) == 2
        assert len(orphans) == 1
        assert orphans[0]["sha"] == "bbb"

    def test_all_orphan(self):
        commits = [self._commit("aaa", "No bead ref")]
        covered, orphans = classify_commits(commits, set())
        assert covered == []
        assert len(orphans) == 1

    def test_bead_id_not_in_set_is_orphan(self):
        commits = [self._commit("aaa", "Fix (mvp-other)")]
        bead_ids = {"mvp-abc"}  # mvp-other not in range
        _, orphans = classify_commits(commits, bead_ids)
        assert len(orphans) == 1


class TestRenderBeadLine:
    def test_basic(self):
        bead = {"id": "mvp-1ab", "title": "Add ovr_info column"}
        result = render_bead_line(bead)
        assert result == "- **Add ovr_info column** (mvp-1ab)"

    def test_no_title_fallback(self):
        bead = {"id": "mvp-1ab"}
        result = render_bead_line(bead)
        assert "(no title)" in result

    def test_close_reason_not_shown(self):
        bead = {"id": "mvp-1ab", "title": "Feature", "close_reason": "Done"}
        result = render_bead_line(bead)
        assert "Done" not in result


class TestRenderCommitLine:
    def test_basic(self):
        commit = {"sha": "abc1234defgh", "date": "2026-01-01", "subject": "Fix the bug"}
        result = render_commit_line(commit)
        assert result == "- `abc1234` Fix the bug"


class TestRenderMarkdown:
    def _range(self):
        return VersionRange(
            version="0.2.3",
            from_sha="aaa1111",
            to_sha="bbb2222",
            from_date="2026-01-01T00:00:00Z",
            to_date="2026-03-01T00:00:00Z",
        )

    def test_contains_version_header(self):
        md = render_markdown("0.2.3", self._range(), {}, [], [])
        assert "# v0.2.3 Release Notes (DRAFT)" in md

    def test_contains_range_info(self):
        md = render_markdown("0.2.3", self._range(), {}, [], [])
        assert "aaa1111" in md
        assert "bbb2222" in md

    def test_features_section(self):
        beads = [{"id": "mvp-x", "title": "My feature", "close_reason": "Done", "priority": 2}]
        grouped = {"feature": beads}
        md = render_markdown("0.2.3", self._range(), grouped, [], [])
        assert "## Features" in md
        assert "My feature" in md

    def test_orphan_section_present_when_exists(self):
        orphans = [{"sha": "abc1234", "subject": "Fix without bead"}]
        md = render_markdown("0.2.3", self._range(), {}, orphans, orphans)
        assert "## Orphan Commits" in md
        assert "abc1234" in md

    def test_orphan_section_absent_when_empty(self):
        md = render_markdown("0.2.3", self._range(), {}, [], [])
        assert "## Orphan Commits" not in md

    def test_empty_sections_skipped(self):
        md = render_markdown("0.2.3", self._range(), {}, [], [])
        assert "## Features" not in md
        assert "## Bug Fixes" not in md

    def test_section_order(self):
        grouped = {
            "chore": [{"id": "c", "title": "chore", "priority": 2}],
            "feature": [{"id": "f", "title": "feature", "priority": 2}],
            "epic": [{"id": "e", "title": "epic", "priority": 2}],
        }
        md = render_markdown("0.2.3", self._range(), grouped, [], [])
        epic_pos = md.index("## Epics")
        feat_pos = md.index("## Features")
        chore_pos = md.index("## Chores")
        assert epic_pos < feat_pos < chore_pos

    def test_from_sha_shown_when_set(self):
        vrange = VersionRange(
            version="0.1.0",
            from_sha="test_from_sha_prefix_1234",
            to_sha="test_to_sha_prefix_5678",
            from_date="2026-01-01T00:00:00Z",
            to_date="2026-02-01T00:00:00Z",
        )
        md = render_markdown("0.1.0", vrange, {}, [], [])
        assert "test_fr" in md
        assert "test_to" in md


class TestFormatDebugLog:
    def _range(self, version, from_sha, to_sha):
        return VersionRange(
            version=version,
            from_sha=from_sha,
            to_sha=to_sha,
            from_date="2026-01-01T00:00:00Z",
            to_date="2026-03-01T00:00:00Z",
        )

    def test_boundary_headers_inserted(self):
        ranges = [
            self._range("unreleased", "aaa1111", "bbb2222"),
            self._range("0.2.3", "ccc3333", "aaa1111"),
        ]
        commits = [
            {"sha": "bbb2222", "subject": "latest commit", "version": "unreleased"},
            {"sha": "aaa1111", "subject": "rev version", "version": "0.2.3"},
            {"sha": "ccc3333", "subject": "older commit", "version": "0.2.3"},
        ]
        output = format_debug_log(ranges, commits)
        assert "--- start vunreleased (aaa1111..bbb2222) [1 commits] ---" in output
        assert "--- end unreleased ---" in output
        assert "--- start v0.2.3 (ccc3333..aaa1111) [2 commits] ---" in output
        assert "--- end 0.2.3 ---" in output

    def test_commit_lines_appear_after_header(self):
        ranges = [self._range("0.1.0", "root000", "abc1234")]
        commits = [
            {"sha": "abc1234", "subject": "fix bug", "version": "0.1.0"},
        ]
        output = format_debug_log(ranges, commits)
        lines = output.splitlines()
        header_idx = next(i for i, ln in enumerate(lines) if "--- start v0.1.0" in ln)
        assert lines[header_idx + 1] == "abc1234 fix bug"

    def test_empty_commits_returns_placeholder(self):
        assert format_debug_log([], []) == "(no commits)"

    def test_commit_count_in_header(self):
        ranges = [self._range("0.2.0", "aaa0000", "bbb1111")]
        commits = [
            {"sha": "bbb1111", "subject": "c1", "version": "0.2.0"},
            {"sha": "aaa9999", "subject": "c2", "version": "0.2.0"},
            {"sha": "aaa8888", "subject": "c3", "version": "0.2.0"},
        ]
        output = format_debug_log(ranges, commits)
        assert "[3 commits]" in output

    def test_unknown_version_commits_shown(self):
        ranges = [self._range("0.1.0", "aaa0000", "bbb1111")]
        commits = [
            {"sha": "zzz9999", "subject": "mystery commit", "version": "unknown"},
        ]
        output = format_debug_log(ranges, commits)
        assert "--- start vunknown (unknown) [1 commits] ---" in output
        assert "zzz9999 mystery commit" in output

    def test_end_line_after_last_version(self):
        ranges = [self._range("0.1.0", "aaa0000", "bbb1111")]
        commits = [{"sha": "bbb1111", "subject": "commit", "version": "0.1.0"}]
        output = format_debug_log(ranges, commits)
        assert "--- end 0.1.0 ---" in output

    def test_color_true_adds_ansi_codes(self):
        ranges = [self._range("0.1.0", "aaa0000", "bbb1111")]
        commits = [{"sha": "bbb1111", "subject": "commit", "version": "0.1.0"}]
        output = format_debug_log(ranges, commits, color=True)
        assert "\033[" in output

    def test_color_false_no_ansi_codes(self):
        ranges = [self._range("0.1.0", "aaa0000", "bbb1111")]
        commits = [{"sha": "bbb1111", "subject": "commit", "version": "0.1.0"}]
        output = format_debug_log(ranges, commits, color=False)
        assert "\033[" not in output

    def test_color_applies_to_boundary_lines_not_commits(self):
        ranges = [self._range("0.1.0", "aaa0000", "bbb1111")]
        commits = [{"sha": "bbb1111", "subject": "fix bug", "version": "0.1.0"}]
        output = format_debug_log(ranges, commits, color=True)
        lines = output.splitlines()
        # Commit lines should not have ANSI codes
        commit_lines = [ln for ln in lines if ln.startswith("bbb1111")]
        assert len(commit_lines) == 1
        assert "\033[" not in commit_lines[0]


class TestDetectVersionRangesRollingMain:
    """Tests for rolling-main era semantics in detect_version_ranges."""

    def _mock_run(self, responses: dict):
        """Build a mock _run that returns preset responses keyed by cmd tuple."""

        def mock_run(cmd, *, check=True):
            key = tuple(cmd)
            if key in responses:
                result = MagicMock()
                result.stdout, result.returncode = responses[key]
                return result
            # Default: success with empty stdout
            result = MagicMock()
            result.stdout = ""
            result.returncode = 0
            return result

        return mock_run

    def test_rolling_main_oldest_era_uses_root_as_from(self):
        """Oldest era: from_sha should be the root commit, to_sha the next bump."""
        sha_010 = "a" * 40
        sha_020 = "b" * 40
        sha_root = "c" * 40

        responses = {
            ("git", "log", "--format=%H %aI", "--", "pyproject.toml"): (
                f"{sha_020} 2026-02-01T00:00:00Z\n{sha_010} 2026-01-01T00:00:00Z\n",
                0,
            ),
            ("git", "show", f"{sha_020}:pyproject.toml"): ('version = "0.2.0"\n', 0),
            ("git", "show", f"{sha_010}:pyproject.toml"): ('version = "0.1.0"\n', 0),
            ("git", "rev-list", "--max-parents=0", sha_010): (sha_root + "\n", 0),
            ("git", "log", "-1", "--format=%aI", sha_root): ("2026-01-01T00:00:00Z\n", 0),
            ("git", "rev-parse", "HEAD"): (sha_020 + "\n", 0),
        }
        with patch("tools.dev.release_notes._run", side_effect=self._mock_run(responses)):
            ranges = detect_version_ranges(rolling_main=True)

        version_map = {r.version: r for r in ranges}
        r010 = version_map["0.1.0"]
        r020 = version_map["0.2.0"]

        # 0.1.0 era: from=root, to=0.2.0 bump
        assert r010.from_sha == sha_root
        assert r010.to_sha == sha_020
        # 0.2.0 era (newest): from=0.2.0 bump, to=0.2.0 bump (HEAD == latest bump)
        assert r020.from_sha == sha_020
        assert r020.to_sha == sha_020

    def test_no_rolling_main_traditional_semantics(self):
        """Traditional mode: each version's to_sha is its own bump."""
        sha_010 = "a" * 40
        sha_020 = "b" * 40
        sha_root = "c" * 40

        responses = {
            ("git", "log", "--format=%H %aI", "--", "pyproject.toml"): (
                f"{sha_020} 2026-02-01T00:00:00Z\n{sha_010} 2026-01-01T00:00:00Z\n",
                0,
            ),
            ("git", "show", f"{sha_020}:pyproject.toml"): ('version = "0.2.0"\n', 0),
            ("git", "show", f"{sha_010}:pyproject.toml"): ('version = "0.1.0"\n', 0),
            ("git", "rev-list", "--max-parents=0", sha_010): (sha_root + "\n", 0),
            ("git", "log", "-1", "--format=%aI", sha_root): ("2026-01-01T00:00:00Z\n", 0),
            ("git", "rev-parse", "HEAD"): (sha_020 + "\n", 0),
        }
        with patch("tools.dev.release_notes._run", side_effect=self._mock_run(responses)):
            ranges = detect_version_ranges(rolling_main=False)

        version_map = {r.version: r for r in ranges}
        r010 = version_map["0.1.0"]
        r020 = version_map["0.2.0"]

        # Traditional 0.1.0: to=0.1.0 bump, from=root
        assert r010.to_sha == sha_010
        assert r010.from_sha == sha_root
        # Traditional 0.2.0: to=0.2.0 bump, from=0.1.0 bump
        assert r020.to_sha == sha_020
        assert r020.from_sha == sha_010


class TestParseBreadCloseCommitsFromDiff:
    def _jline(self, **kwargs) -> str:
        return json.dumps(kwargs)

    def test_basic_open_to_closed(self):
        log = "\n".join(
            [
                "COMMIT:abc1234",
                f"-{self._jline(id='mvp-aaa', status='open')}",
                f"+{self._jline(id='mvp-aaa', status='closed')}",
            ]
        )
        assert parse_bead_close_commits_from_diff(log) == {"mvp-aaa": "abc1234"}

    def test_compaction_same_status_not_recorded(self):
        log = "\n".join(
            [
                "COMMIT:abc1234",
                f"-{self._jline(id='mvp-aaa', status='closed')}",
                f"+{self._jline(id='mvp-aaa', status='closed')}",
            ]
        )
        assert parse_bead_close_commits_from_diff(log) == {}

    def test_new_bead_immediately_closed(self):
        log = "\n".join(
            [
                "COMMIT:abc1234",
                f"+{self._jline(id='mvp-aaa', status='closed')}",
            ]
        )
        assert parse_bead_close_commits_from_diff(log) == {"mvp-aaa": "abc1234"}

    def test_multiple_beads_same_commit(self):
        log = "\n".join(
            [
                "COMMIT:abc1234",
                f"-{self._jline(id='mvp-aaa', status='open')}",
                f"+{self._jline(id='mvp-aaa', status='closed')}",
                f"-{self._jline(id='mvp-bbb', status='in_progress')}",
                f"+{self._jline(id='mvp-bbb', status='closed')}",
            ]
        )
        assert parse_bead_close_commits_from_diff(log) == {
            "mvp-aaa": "abc1234",
            "mvp-bbb": "abc1234",
        }

    def test_invalid_json_skipped(self):
        log = "COMMIT:abc1234\n-not valid json\n+also not json"
        assert parse_bead_close_commits_from_diff(log) == {}

    def test_file_header_lines_skipped(self):
        log = "\n".join(
            [
                "COMMIT:abc1234",
                "--- a/.beads/issues.jsonl",
                "+++ b/.beads/issues.jsonl",
                f"+{self._jline(id='mvp-aaa', status='closed')}",
            ]
        )
        assert parse_bead_close_commits_from_diff(log) == {"mvp-aaa": "abc1234"}

    def test_first_close_commit_wins(self):
        log = "\n".join(
            [
                "COMMIT:first111",
                f"-{self._jline(id='mvp-aaa', status='open')}",
                f"+{self._jline(id='mvp-aaa', status='closed')}",
                "COMMIT:second222",
                f"-{self._jline(id='mvp-aaa', status='closed')}",
                f"+{self._jline(id='mvp-aaa', status='closed')}",
            ]
        )
        assert parse_bead_close_commits_from_diff(log) == {"mvp-aaa": "first111"}

    def test_empty_input(self):
        assert parse_bead_close_commits_from_diff("") == {}


class TestAssignBeadsToVersions:
    def _vrange(self, version, *, from_date=None, to_date="2026-02-01T00:00:00Z"):
        return VersionRange(
            version=version,
            from_sha="sha_from",
            to_sha="sha_to",
            from_date=from_date,
            to_date=to_date,
        )

    def test_bead_assigned_by_close_commit(self):
        beads = [{"id": "mvp-abc", "closed_at": "2026-01-15T00:00:00Z"}]
        ranges = [self._vrange("0.1.0")]

        with (
            patch("tools.dev.release_notes.fetch_bead_close_commits") as mock_close,
            patch("tools.dev.release_notes.build_sha_to_version") as mock_sha_ver,
        ):
            mock_close.return_value = {"mvp-abc": "sha_commit"}
            mock_sha_ver.return_value = {"sha_commit": "0.1.0"}
            result = assign_beads_to_versions(beads, ranges)

        assert result == {"mvp-abc": "0.1.0"}

    def test_fallback_to_date_when_commit_not_in_range(self):
        beads = [{"id": "mvp-abc", "closed_at": "2026-01-15T00:00:00Z"}]
        ranges = [self._vrange("0.1.0", from_date=None, to_date="2026-02-01T00:00:00Z")]

        with (
            patch("tools.dev.release_notes.fetch_bead_close_commits") as mock_close,
            patch("tools.dev.release_notes.build_sha_to_version") as mock_sha_ver,
        ):
            mock_close.return_value = {"mvp-abc": "unknown_sha"}
            mock_sha_ver.return_value = {}
            result = assign_beads_to_versions(beads, ranges)

        assert result == {"mvp-abc": "0.1.0"}

    def test_bead_without_close_commit_uses_date_fallback(self):
        beads = [{"id": "mvp-xyz", "closed_at": "2026-01-20T00:00:00Z"}]
        ranges = [self._vrange("0.2.0", from_date=None, to_date="2026-02-01T00:00:00Z")]

        with (
            patch("tools.dev.release_notes.fetch_bead_close_commits") as mock_close,
            patch("tools.dev.release_notes.build_sha_to_version") as mock_sha_ver,
        ):
            mock_close.return_value = {}  # no close commit found at all
            mock_sha_ver.return_value = {}
            result = assign_beads_to_versions(beads, ranges)

        assert result == {"mvp-xyz": "0.2.0"}


class TestGenerateNotesForRange:
    def _vrange(self):
        return VersionRange(
            version="0.1.0",
            from_sha="sha_from_1234",
            to_sha="sha_to_5678",
            from_date="2026-01-01T00:00:00Z",
            to_date="2026-02-01T00:00:00Z",
        )

    def test_writes_markdown_file(self, tmp_path):
        beads = [
            {
                "id": "mvp-abc",
                "title": "My feature",
                "issue_type": "feature",
                "priority": 2,
                "close_reason": "Done",
                "closed_at": "2026-01-15T00:00:00Z",
            }
        ]
        bead_id_to_version = {"mvp-abc": "0.1.0"}

        with patch("tools.dev.release_notes.fetch_git_commits") as mock_commits:
            mock_commits.return_value = [
                {"sha": "abc1234", "date": "2026-01-10", "subject": "Add feature (mvp-abc)"}
            ]
            generate_notes_for_range(
                self._vrange(), beads, tmp_path, bead_id_to_version, force=True
            )

        out = (tmp_path / "v0.1.0.md").read_text()
        assert "My feature" in out
        assert "mvp-abc" in out

    def test_skips_when_file_exists_no_force(self, tmp_path):
        existing = tmp_path / "v0.1.0.md"
        existing.write_text("existing content")

        with patch("tools.dev.release_notes.fetch_git_commits") as mock_commits:
            mock_commits.return_value = []
            generate_notes_for_range(self._vrange(), [], tmp_path, {}, force=False)

        assert existing.read_text() == "existing content"

    def test_only_beads_in_version_included(self, tmp_path):
        beads = [
            {"id": "mvp-v1", "title": "V1 bead", "issue_type": "task", "priority": 2},
            {"id": "mvp-v2", "title": "V2 bead", "issue_type": "task", "priority": 2},
        ]
        bead_id_to_version = {"mvp-v1": "0.1.0", "mvp-v2": "0.2.0"}

        with patch("tools.dev.release_notes.fetch_git_commits") as mock_commits:
            mock_commits.return_value = []
            generate_notes_for_range(
                self._vrange(), beads, tmp_path, bead_id_to_version, force=True
            )

        out = (tmp_path / "v0.1.0.md").read_text()
        assert "V1 bead" in out
        assert "V2 bead" not in out
