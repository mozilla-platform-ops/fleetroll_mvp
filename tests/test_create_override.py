"""Run the override creator with offline remote lookup stubs."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def override_tool(tmp_path, monkeypatch):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    overrides = tmp_path / "configs" / "overrides"
    overrides.mkdir(parents=True)
    script = tools_dir / "create_override.sh"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "tools/create_override.sh", script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = shutil.which("git")
    assert git is not None
    monkeypatch.setenv("REAL_GIT", git)
    monkeypatch.setenv("LOOKUP_LOG", str(tmp_path / "lookup.log"))
    monkeypatch.setenv("LOOKUP_STATUS", "0")
    monkeypatch.setenv(
        "PR_REPLY",
        "42\tTest PR\tfeature/fix\ttrue\tcontributor\tronin_puppet\thttps://github.com/mozilla-platform-ops/ronin_puppet/pull/42",
    )
    stubs = {
        "git": """#!/bin/sh
if [ "$1" = check-ref-format ]; then
  exec "$REAL_GIT" "$@"
fi
printf '%s\\n' "$@" > "$LOOKUP_LOG"
exit "$LOOKUP_STATUS"
""",
        "gh": '#!/bin/sh\nprintf "%s\\n" "$PR_REPLY"\n',
    }
    for name, content in stubs.items():
        executable = bin_dir / name
        executable.write_text(content)
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return script, overrides, tmp_path / "lookup.log"


def run_creator(script, *, branch):
    return subprocess.run(
        ["/bin/bash", str(script), branch], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("branch", ["my-branch", "feature/fix"])
def test_existing_branch_is_verified_before_creation(override_tool, branch):
    script, overrides, log = override_tool
    result = run_creator(script, branch=branch)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "ls-remote",
        "--exit-code",
        "--heads",
        "https://github.com/mozilla-platform-ops/ronin_puppet.git",
        f"refs/heads/{branch}",
    ]
    content = (overrides / f"{branch.replace('/', '_')}.override").read_text()
    assert f'PUPPET_BRANCH="{branch}"' in content


@pytest.mark.parametrize(
    ("status", "message"), [("2", "Branch not found"), ("128", "Failed to verify branch")]
)
def test_lookup_failure_does_not_create_override(override_tool, monkeypatch, status, message):
    script, overrides, _ = override_tool
    monkeypatch.setenv("LOOKUP_STATUS", status)
    result = run_creator(script, branch="missing-branch")
    assert result.returncode != 0
    assert message in result.stderr
    assert not list(overrides.iterdir())


@pytest.mark.parametrize("branch", ["feature/*", "feature/../fix"])
def test_invalid_branch_cannot_match_other_refs(override_tool, branch):
    script, overrides, log = override_tool
    result = run_creator(script, branch=branch)
    assert result.returncode != 0
    assert "Invalid branch name" in result.stderr
    assert not log.exists()
    assert not list(overrides.iterdir())


@pytest.mark.parametrize(
    "pr", ["42", "https://github.com/mozilla-platform-ops/ronin_puppet/pull/42"]
)
def test_pr_checks_branch_in_fork(override_tool, pr):
    script, overrides, log = override_tool
    result = run_creator(script, branch=pr)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[-2:] == [
        "https://github.com/contributor/ronin_puppet.git",
        "refs/heads/feature/fix",
    ]
    assert 'PUPPET_BRANCH="feature/fix"' in (overrides / "pr-42-test-pr.override").read_text()


def test_existing_file_is_preserved_without_lookup(override_tool):
    script, overrides, log = override_tool
    existing = overrides / "my-branch.override"
    existing.write_text("original content\n")
    result = run_creator(script, branch="my-branch")
    assert result.returncode != 0
    assert existing.read_text() == "original content\n"
    assert not log.exists()
