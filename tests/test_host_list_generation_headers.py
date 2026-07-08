from pathlib import Path

from tools import host_list_headers
from tools.generate_all_host_lists import format_source_revision_lines
from tools.generate_mac_host_list import generate_group_file
from tools.generate_windows_host_list import generate_host_list
from tools.host_list_headers import content_changed, source_revision_from_content, write_if_changed


def test_content_changed_replaces_legacy_generated_header() -> None:
    old = (
        "# THIS FILE IS AUTO-GENERATED. DO NOT EDIT MANUALLY.\n"
        "# Generated: 2026-07-08 18:23 UTC\n"
        "# Source:    configs/host-lists/linux/all_moonshots.list\n"
        "\n"
        "host-1.example.com\n"
    )
    new = (
        "# THIS FILE IS AUTO-GENERATED. DO NOT EDIT MANUALLY.\n"
        "# Source revision: branch=main commit=test_sha_1234 dirty\n"
        "# Source:    configs/host-lists/linux/all_moonshots.list\n"
        "\n"
        "host-1.example.com\n"
    )

    assert content_changed(old, new)


def test_write_if_changed_skips_volatile_header_only_update(tmp_path: Path) -> None:
    output = tmp_path / "hosts.list"
    output.write_text("# Source revision: branch=main commit=old clean\nhost-1\n", encoding="utf-8")

    wrote = write_if_changed(
        output,
        "# Source revision: branch=feature commit=new dirty\nhost-1\n",
    )

    assert not wrote
    assert output.read_text(encoding="utf-8") == (
        "# Source revision: branch=main commit=old clean\nhost-1\n"
    )


def test_write_if_changed_force_overwrites_volatile_header_only_update(tmp_path: Path) -> None:
    output = tmp_path / "hosts.list"
    output.write_text("# Source revision: branch=main commit=old clean\nhost-1\n", encoding="utf-8")
    new_content = "# Source revision: branch=feature commit=new dirty\nhost-1\n"

    wrote = write_if_changed(output, new_content, force=True)

    assert wrote
    assert output.read_text(encoding="utf-8") == new_content


def test_source_revision_from_content_reads_generated_header() -> None:
    content = (
        "# THIS FILE IS AUTO-GENERATED. DO NOT EDIT MANUALLY.\n"
        "# Source revision: repo=mozilla-platform-ops/ronin_puppet branch=main commit=test_sha_1234\n"
        "# Source:    mozilla-platform-ops/ronin_puppet inventory.d/mac.yaml\n"
    )

    assert source_revision_from_content(content) == (
        "repo=mozilla-platform-ops/ronin_puppet branch=main commit=test_sha_1234"
    )


def test_local_source_revision_ignores_untracked_files(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_git(args: list[str], *, cwd: Path) -> str | None:
        calls.append(args)
        if args == ["branch", "--show-current"]:
            return "main"
        if args == ["rev-parse", "--short", "HEAD"]:
            return "test_sha_1234"
        if args == ["status", "--porcelain", "--untracked-files=no"]:
            return None
        msg = f"unexpected git args: {args}"
        raise AssertionError(msg)

    monkeypatch.setattr(host_list_headers, "_run_git", fake_run_git)

    assert host_list_headers.local_source_revision(cwd=tmp_path, repo="example/repo") == (
        "repo=example/repo branch=main commit=test_sha_1234 clean"
    )
    assert ["status", "--porcelain", "--untracked-files=no"] in calls


def test_format_source_revision_lines_reports_multiple_input_revisions() -> None:
    lines = format_source_revision_lines(
        {
            "mac.list": "repo=mozilla-platform-ops/ronin_puppet branch=main commit=test_sha_1234",
            "linux.list": None,
        }
    )

    assert lines == [
        "# Source revision: multiple",
        "# Source revision: linux.list: not available in source file",
        "# Source revision: mac.list: repo=mozilla-platform-ops/ronin_puppet "
        "branch=main commit=test_sha_1234",
    ]


def test_windows_host_list_uses_source_revision_header() -> None:
    raw_yaml = """
pools:
  - name: gecko-t-win
    domain_suffix: example.test
    nodes:
      - t-nuc12-10
      - t-nuc12-2
Known-BAD: {}
"""

    content = generate_host_list(
        raw_yaml,
        source_revision="repo=mozilla-platform-ops/worker-images branch=main commit=test_sha_1234",
    )

    assert "# Generated:" not in content
    assert (
        "# Source revision: repo=mozilla-platform-ops/worker-images "
        "branch=main commit=test_sha_1234"
    ) in content
    assert content.index("t-nuc12-2") < content.index("t-nuc12-10")


def test_mac_host_list_uses_source_revision_header() -> None:
    content = generate_group_file(
        {
            "name": "gecko-t-osx",
            "targets": ["mac-10.example.test", "mac-2.example.test"],
            "facts": {"puppet_role": "gecko_t_osx"},
        },
        inventory_name="mac.yaml",
        source_revision="repo=mozilla-platform-ops/ronin_puppet branch=main commit=test_sha_1234",
    )

    assert content is not None
    assert "# Generated:" not in content
    assert (
        "# Source revision: repo=mozilla-platform-ops/ronin_puppet branch=main commit=test_sha_1234"
    ) in content
    assert content.index("mac-2.example.test") < content.index("mac-10.example.test")
