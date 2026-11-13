"""Execute the generated shell against fake sudo/wrappers, without managed hosts."""

import os
import shlex
import subprocess

import pytest
from fleetroll.puppet_outcome import parse_run_observation
from fleetroll.ssh import remote_run_puppet_script


@pytest.mark.parametrize("platform", ["Linux", "Darwin"])
@pytest.mark.parametrize("code", [0, 2, 4, 6])
@pytest.mark.parametrize("write_metadata", [True, False])
def test_normalized_wrapper_exit_keeps_detailed_metadata(
    tmp_path,
    monkeypatch,
    platform,
    code,
    write_metadata,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "state.json"
    read_paths = tmp_path / "reads"
    if not write_metadata:
        state_path.write_text('{"schema_version":1,"exit_code":0,"ts":"2020-01-01T00:00:00Z"}')
    scripts = {
        "uname": f"#!/bin/sh\nprintf '%s\\n' '{platform}'\n",
        "sudo": """#!/bin/sh
shift
case "$1" in
  cat)
    printf '%s\\n' "$2" >> "$TEST_READ_PATHS"
    exec cat "$TEST_STATE" ;;
  test) exec test ! -e "$TEST_STATE" ;;
  *) exec "$@" ;;
esac
""",
        "run-puppet.sh": f"""#!/bin/sh
[ "$FLEETROLL_SINGLE_RUN" = 1 ] || exit 99
printf 'wrapper output without trailing newline'
if [ "$TEST_WRITE_METADATA" = 1 ]; then
success={str(code in {0, 2}).lower()}
timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{{"schema_version":1,"exit_code":{code},"success":%s,"duration_s":0,"ts":"%s"}}' \\
  "$success" "$timestamp" > "$TEST_STATE"
fi
exit {0 if code in {0, 2} else 1}
""",
    }
    for name, content in scripts.items():
        path = bin_dir / name
        path.write_text(content)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TEST_STATE", str(state_path))
    monkeypatch.setenv("TEST_READ_PATHS", str(read_paths))
    monkeypatch.setenv("TEST_WRITE_METADATA", "1" if write_metadata else "0")
    completed = subprocess.run(
        shlex.split(remote_run_puppet_script()),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = parse_run_observation(completed.stdout)
    assert result["wrapper_exit"] == (0 if code in {0, 2} else 1)
    if write_metadata:
        assert result["puppet_exit"] == code
        assert result["puppet_outcome"] == {0: "unchanged", 2: "changed"}.get(code, "failed")
    else:
        assert result["puppet_exit"] is None
        assert result["outcome_reason"] == "metadata_unchanged"
    expected_path = (
        "/etc/puppet/last_run_metadata.json"
        if platform == "Linux"
        else "/opt/puppet_environments/last_run_metadata.json"
    )
    assert read_paths.read_text().splitlines() == [expected_path, expected_path]
