"""Tests for tools/dev/release_notes.py - pure utility functions."""

from __future__ import annotations

from tools.dev.release_notes import (
    VersionRange,
    classify_commits,
    extract_bead_id,
    extract_version_from_toml,
    filter_beads_by_date,
    group_beads_by_type,
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
        bead = {
            "id": "mvp-1ab",
            "title": "Add ovr_info column",
            "close_reason": "Completed successfully",
        }
        result = render_bead_line(bead)
        assert result == "- **Add ovr_info column** (mvp-1ab) — Completed successfully"

    def test_truncates_long_reason(self):
        bead = {
            "id": "mvp-1ab",
            "title": "Feature",
            "close_reason": "x" * 200,
        }
        result = render_bead_line(bead)
        assert len(result) < 300
        assert result.endswith("...")

    def test_no_close_reason(self):
        bead = {"id": "mvp-1ab", "title": "Feature"}
        result = render_bead_line(bead)
        assert "(no reason provided)" in result

    def test_exact_120_chars_not_truncated(self):
        bead = {
            "id": "mvp-x",
            "title": "T",
            "close_reason": "a" * 120,
        }
        result = render_bead_line(bead)
        assert "..." not in result


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
        md = render_markdown("0.2.3", self._range(), {}, [], 10)
        assert "# v0.2.3 Release Notes (DRAFT)" in md

    def test_contains_range_info(self):
        md = render_markdown("0.2.3", self._range(), {}, [], 10)
        assert "aaa1111" in md
        assert "bbb2222" in md

    def test_features_section(self):
        beads = [{"id": "mvp-x", "title": "My feature", "close_reason": "Done", "priority": 2}]
        grouped = {"feature": beads}
        md = render_markdown("0.2.3", self._range(), grouped, [], 5)
        assert "## Features" in md
        assert "My feature" in md

    def test_orphan_section_present_when_exists(self):
        orphans = [{"sha": "abc1234", "subject": "Fix without bead"}]
        md = render_markdown("0.2.3", self._range(), {}, orphans, 1)
        assert "## Orphan Commits" in md
        assert "abc1234" in md

    def test_orphan_section_absent_when_empty(self):
        md = render_markdown("0.2.3", self._range(), {}, [], 5)
        assert "## Orphan Commits" not in md

    def test_empty_sections_skipped(self):
        md = render_markdown("0.2.3", self._range(), {}, [], 0)
        assert "## Features" not in md
        assert "## Bug Fixes" not in md

    def test_section_order(self):
        grouped = {
            "chore": [{"id": "c", "title": "chore", "priority": 2}],
            "feature": [{"id": "f", "title": "feature", "priority": 2}],
            "bug": [{"id": "b", "title": "bug", "priority": 2}],
        }
        md = render_markdown("0.2.3", self._range(), grouped, [], 3)
        feat_pos = md.index("## Features")
        bug_pos = md.index("## Bug Fixes")
        chore_pos = md.index("## Chores")
        assert feat_pos < bug_pos < chore_pos

    def test_from_sha_none_shows_initial(self):
        vrange = VersionRange(
            version="0.1.0",
            from_sha=None,
            to_sha="abc1234",
            from_date=None,
            to_date="2026-01-01T00:00:00Z",
        )
        md = render_markdown("0.1.0", vrange, {}, [], 0)
        assert "initial" in md
