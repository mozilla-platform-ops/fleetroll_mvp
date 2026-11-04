"""Release boundaries and attribution against real, isolated Git histories."""

import json
import os
import shutil
import subprocess

import pytest
from tools.dev import release_notes as notes


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    beads = []
    git_path = shutil.which("git")
    assert git_path is not None

    def git(*args, hour=9):
        stamp = f"2026-01-01T{hour:02}:00:00+00:00"
        env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        return subprocess.run(
            [git_path, "-c", "user.name=Test User", "-c", "user.email=test@example.com", *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()

    git("init")

    def commit(subject, *, hour=9, version=None, bead=None):
        if version:
            (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
        if bead:
            beads.append(
                {
                    "id": bead,
                    "status": "closed",
                    "title": subject,
                    "issue_type": "feature",
                    "closed_at": "2026-01-01T23:00:00Z",
                    "priority": 2,
                }
            )
            directory = tmp_path / ".beads"
            directory.mkdir(exist_ok=True)
            (directory / "issues.jsonl").write_text("".join(json.dumps(b) + "\n" for b in beads))
        git("add", ".")
        git("commit", "--allow-empty", "-m", subject, hour=hour)
        return git("rev-parse", "HEAD")

    monkeypatch.setattr(notes, "fetch_closed_beads", lambda: beads)
    return commit, git, beads


def test_root_release_at_head_is_retained_and_unreleased_is_empty(history):
    commit, _, beads = history
    root = commit("initial release", version="0.1.0", bead="mvp-root")
    ranges = notes.detect_version_ranges()
    assert [r.version for r in ranges] == ["unreleased", "0.1.0"]
    assert notes.fetch_git_commits(ranges[0].from_sha, ranges[0].to_sha) == []
    assert notes.build_sha_to_version(ranges) == {root: "0.1.0"}
    assert notes.assign_beads_to_versions(beads, ranges) == {"mvp-root": "0.1.0"}
    assert notes.build_debug_annotated_commits(ranges)[0]["version"] == "0.1.0"


def test_same_day_bumps_and_post_release_work_partition_identically(history, tmp_path):
    commit, _, beads = history
    root = commit("initial release", version="0.1.0", bead="mvp-root")
    feature = commit("first feature (mvp-first)", hour=10, bead="mvp-first")
    with (tmp_path / "pyproject.toml").open("a") as file:
        file.write('description = "non-version edit"\n')
    edit = commit("edit project metadata", hour=11)
    bump = commit("release 0.2.0", hour=12, version="0.2.0")
    latest = commit("next feature (mvp-next)", hour=13, bead="mvp-next")
    ranges = notes.detect_version_ranges()
    assert [r.version for r in ranges] == ["unreleased", "0.2.0", "0.1.0"]
    expected = {root: "0.1.0", feature: "0.2.0", edit: "0.2.0", bump: "0.2.0", latest: "unreleased"}
    assert notes.build_sha_to_version(ranges) == expected
    debug = notes.build_debug_annotated_commits(ranges)
    assert {c["sha"]: c["version"] for c in debug} == {
        sha[:7]: version for sha, version in expected.items()
    }
    assert notes.assign_beads_to_versions(beads, ranges) == {
        "mvp-root": "0.1.0",
        "mvp-first": "0.2.0",
        "mvp-next": "unreleased",
    }
    legacy = notes.detect_version_ranges(rolling_main=True)
    assert legacy[0].version == "0.2.0-IN_PROGRESS"


@pytest.mark.parametrize("flag", [[], ["--no-rolling-main"]])
def test_cli_defaults_to_unreleased_and_keeps_release_drafts_separate(
    history, tmp_path, monkeypatch, flag
):
    commit, _, _ = history
    commit("initial", version="0.1.0")
    commit("feature", hour=10)
    commit("release", hour=12, version="0.2.0")
    monkeypatch.setattr("sys.argv", ["release-notes", *flag])
    assert notes.main() == 0
    generated = tmp_path / "docs/release-notes/generated"
    assert [p.name for p in generated.iterdir()] == ["unreleased.md"]
    assert "# Unreleased Release Notes" in (generated / "unreleased.md").read_text()
    monkeypatch.setattr("sys.argv", ["release-notes", "--version", "0.2.0", *flag])
    assert notes.main() == 0
    assert "# v0.2.0 Release Notes" in (generated / "v0.2.0.md").read_text()
    assert "feature" in (generated / "v0.2.0.md").read_text()
    (generated / "v0.2.0.md").write_text("keep existing draft")
    reviewed = generated.parent / "v0.2.0.md"
    reviewed.write_text("reviewed history")
    monkeypatch.setattr("sys.argv", ["release-notes", "--all"])
    assert notes.main() == 0
    assert (generated / "v0.2.0.md").read_text() == "keep existing draft"
    assert reviewed.read_text() == "reviewed history"


def test_timestamp_fallback_uses_time_and_timezone_not_just_day(capsys, mocker):
    ranges = [
        notes.VersionRange("0.2.0", "old", "new", "2026-01-01T10:00:00Z", "2026-01-01T12:00:00Z")
    ]
    mocker.patch.object(notes, "fetch_bead_close_commits", return_value={})
    mocker.patch.object(notes, "build_sha_to_version", return_value={})
    beads = [{"id": "mvp-fallback", "closed_at": "2026-01-01T03:00:00-08:00"}]
    assert notes.assign_beads_to_versions(beads, ranges) == {"mvp-fallback": "0.2.0"}
    assert "timestamp fallback" in capsys.readouterr().err


def test_uncommitted_version_does_not_define_boundary(history, tmp_path):
    commit, _, _ = history
    commit("initial", version="0.1.0")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    assert [r.version for r in notes.detect_version_ranges()] == ["unreleased", "0.1.0"]


def test_legacy_cli_keeps_progress_and_release_filenames_distinct(history, tmp_path, monkeypatch):
    commit, _, _ = history
    commit("initial", version="0.1.0")
    commit("second release", hour=12, version="0.2.0")
    monkeypatch.setattr("sys.argv", ["release-notes", "--rolling-main"])
    assert notes.main() == 0
    generated = tmp_path / "docs/release-notes/generated"
    assert (generated / "v0.2.0-IN_PROGRESS.md").exists()
    assert not (generated / "v0.2.0.md").exists()


def test_explicit_unreleased_selector_and_debug_label(history, tmp_path, monkeypatch, capsys):
    commit, _, _ = history
    commit("initial", version="0.1.0")
    commit("feature", hour=12)
    monkeypatch.setattr("sys.argv", ["release-notes", "--version", "Unreleased"])
    assert notes.main() == 0
    assert (tmp_path / "docs/release-notes/generated/unreleased.md").exists()
    monkeypatch.setattr("sys.argv", ["release-notes", "--debug"])
    assert notes.main() == 0
    assert "--- start Unreleased" in capsys.readouterr().out
