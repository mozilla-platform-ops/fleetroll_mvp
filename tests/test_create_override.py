"""Run the override creator with offline remote lookup stubs."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli


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
        json.dumps(
            {
                "number": 42,
                "title": "Test PR",
                "headRefName": "feature/fix",
                "isCrossRepository": True,
                "headRepositoryOwner": {"login": "contributor"},
                "headRepository": {"name": "ronin_puppet"},
                "url": "https://github.com/mozilla-platform-ops/ronin_puppet/pull/42",
            }
        ),
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
    result = CliRunner().invoke(
        cli,
        [
            "create-override",
            branch,
            "--output-dir",
            str(script.parent.parent / "configs/overrides"),
        ],
    )
    detail = result.output + (str(result.exception) if result.exception else "")
    return subprocess.CompletedProcess([], result.exit_code, detail, detail)


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


def test_current_directory_default_and_empty_worker_type(override_tool, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["create-override", "my-branch"])
    assert result.exit_code == 0, result.output
    content = (tmp_path / "my-branch.override").read_text()
    assert "# WORKER_TYPE_OVERRIDE=''" in content
    assert "target role's base" in content
    assert "1804-staging" not in content


def test_explicit_output_and_escaped_branch(override_tool, tmp_path):
    output = tmp_path / "custom.override"
    result = CliRunner().invoke(cli, ["create-override", "feature/$test", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert 'PUPPET_BRANCH="feature/\\$test"' in output.read_text()
    assert subprocess.run(["/bin/bash", "-n", str(output)], check=False).returncode == 0


@pytest.mark.parametrize(
    ("source", "stem"), [("feature/fix", "feature_fix"), ("42", "pr-42-test-pr")]
)
def test_worker_type_sets_assignment_and_filename(override_tool, source, stem):
    _, overrides, _ = override_tool
    worker_type = "gecko-t-linux-talos-2404-tc-v115"
    result = CliRunner().invoke(
        cli,
        [
            "override",
            "create",
            source,
            "--output-dir",
            str(overrides),
            "--worker-type",
            worker_type,
        ],
    )
    assert result.exit_code == 0, result.output
    output = overrides / f"{stem}-wt-{worker_type}.override"
    assert f'\nWORKER_TYPE_OVERRIDE="{worker_type}"\n' in output.read_text()
    assert subprocess.run(["/bin/bash", "-n", str(output)], check=False).returncode == 0
    # The default variant can coexist with the worker-pool variant.
    plain = CliRunner().invoke(cli, ["override", "create", source, "--output-dir", str(overrides)])
    assert plain.exit_code == 0, plain.output
    assert "\nWORKER_TYPE_OVERRIDE=" not in (overrides / f"{stem}.override").read_text()
    duplicate = CliRunner().invoke(
        cli,
        [
            "override",
            "create",
            source,
            "--output-dir",
            str(overrides),
            "--worker-type",
            worker_type,
        ],
    )
    assert duplicate.exit_code != 0
    assert "already exists" in str(duplicate.exception)


def test_worker_type_respects_explicit_output(override_tool, tmp_path):
    output = tmp_path / "custom.override"
    result = CliRunner().invoke(
        cli,
        ["override", "create", "my-branch", "--worker-type", "test-pool", "--output", str(output)],
    )
    assert result.exit_code == 0, result.output
    assert '\nWORKER_TYPE_OVERRIDE="test-pool"\n' in output.read_text()


@pytest.mark.parametrize(
    "worker_type", ["", "../pool", "provisioner/pool", "$(id)", "pool\necho bad"]
)
def test_invalid_worker_type_leaves_no_file_or_lookup(override_tool, worker_type):
    _, overrides, log = override_tool
    result = CliRunner().invoke(
        cli,
        [
            "override",
            "create",
            "my-branch",
            "--output-dir",
            str(overrides),
            "--worker-type",
            worker_type,
        ],
    )
    assert result.exit_code != 0
    assert "Invalid worker type" in str(result.exception)
    assert not list(overrides.iterdir())
    assert not log.exists()


def test_pr_title_newlines_stay_in_comments(override_tool, monkeypatch):
    script, overrides, _ = override_tool
    pr = json.loads(os.environ["PR_REPLY"])
    pr["title"] = "Title\necho unexpected"
    monkeypatch.setenv("PR_REPLY", json.dumps(pr))
    result = run_creator(script, branch="42")
    assert result.returncode == 0, result.stderr
    content = next(overrides.iterdir()).read_text()
    assert "\n# echo unexpected\n" in content
    assert "\necho unexpected\n" not in content


def test_bad_pr_response_leaves_no_file(override_tool, monkeypatch):
    script, overrides, _ = override_tool
    monkeypatch.setenv("PR_REPLY", "{}")
    result = run_creator(script, branch="42")
    assert result.returncode != 0
    assert "Invalid GitHub PR response" in result.stderr
    assert not list(overrides.iterdir())


def test_wrapper_forwards_arguments_with_repository_default(override_tool, monkeypatch):
    script, overrides, log = override_tool
    uv = script.parent.parent / "bin" / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$LOOKUP_LOG"\n')
    uv.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(script), "my-branch"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "run",
        "--project",
        str(script.parent.parent),
        "fleetroll",
        "override",
        "create",
        "--output-dir",
        str(overrides),
        "my-branch",
    ]
