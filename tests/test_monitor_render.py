from datetime import UTC, datetime, timedelta

from fleetroll.commands.monitor import (
    age_seconds,
    build_row_values,
    clip_cell,
    compute_columns_and_widths,
    detect_common_fqdn_suffix,
    format_ts_with_age,
    humanize_age,
    humanize_duration,
    render_monitor_lines,
    render_row_cells,
)
from fleetroll.humanhash import humanize


def test_render_row_cells_alignment() -> None:
    record = {
        "ok": True,
        "ts": "2026-01-21T21:52:57+00:00",
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "os_type": "Linux",
            "override_present": True,
            "override_sha256": "0328af8c9d6f",
            "vault_sha256": "abcdef1234567890",
            "override_meta": {"mtime_epoch": "1768983854"},
            "uptime_s": 3600,
        },
    }
    hosts = ["t-linux64-ms-001.test.releng.mdc1.mozilla.com"]
    latest = {hosts[0]: record}
    latest_ok = {hosts[0]: record}
    columns, widths = compute_columns_and_widths(
        hosts=hosts,
        latest=latest,
        latest_ok=latest_ok,
        max_width=200,
        cap_widths=False,
        sep_len=3,
    )
    for col in ("role", "sha", "vlt_sha"):
        if col in widths:
            widths[col] += 2
    labels = {
        "host": "HOST",
        "uptime": "UPTIME",
        "role": "ROLE",
        "os": "OS",
        "sha": "OVR_SHA",
        "vlt_sha": "VLT_SHA",
        "tc_quar": "TC_QUAR",
        "tc_act": "TC_ACT",
        "tc_j_sf": "TC_J_SF",
        "pp_last": "PP_LAST",
        "pp_exp": "PP_EXP",
        "pp_sha": "PP_SHA",
        "pp_match": "PP_MATCH",
        "healthy": "HEALTHY",
        "data": "DATA",
    }
    header_cells = render_row_cells(labels, columns=columns, widths=widths, include_marker=False)
    values = build_row_values(hosts[0], record, last_ok=record)
    row_cells = render_row_cells(values, columns=columns, widths=widths)

    assert [len(cell) for cell in header_cells] == [len(cell) for cell in row_cells]

    header_line = " | ".join(header_cells)
    row_line = " | ".join(row_cells)
    assert _sep_positions(header_line) == _sep_positions(row_line)


def test_clip_cell_truncation() -> None:
    assert clip_cell("abcdef", 2) == "ab"
    assert clip_cell("abcdef", 3) == "abc"
    assert clip_cell("abcdef", 4) == "a..."
    assert clip_cell("abcdef", 6) == "abcdef"


def test_build_row_values_includes_humanhash() -> None:
    sha = "a" * 64
    vlt = "b" * 64
    record = {
        "ok": True,
        "ts": "2026-01-21T21:52:57+00:00",
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": True,
            "override_sha256": sha,
            "vault_sha256": vlt,
            "override_meta": {"mtime_epoch": "1768983854"},
            "uptime_s": 3600,
        },
    }
    values = build_row_values("host1", record, last_ok=record)
    assert values["sha"].startswith(sha[:8])
    assert humanize(sha, words=2) in values["sha"]
    assert values["vlt_sha"].startswith(vlt[:8])
    assert humanize(vlt, words=2) in values["vlt_sha"]


def test_compute_columns_drops_sha_columns_when_narrow() -> None:
    record = {
        "ok": True,
        "ts": "2026-01-21T21:52:57+00:00",
        "observed": {
            "role_present": True,
            "role": "role1",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": "1768983854"},
            "uptime_s": 3600,
        },
    }
    hosts = ["host1", "host2"]
    latest = {hosts[0]: record, hosts[1]: record}
    columns, _ = compute_columns_and_widths(
        hosts=hosts,
        latest=latest,
        latest_ok={hosts[0]: record, hosts[1]: record},
        max_width=20,
        cap_widths=True,
        sep_len=1,
    )
    assert "vlt_sha" not in columns
    assert "sha" not in columns


def test_render_monitor_lines_sorted_and_sliced() -> None:
    record = {
        "ok": True,
        "ts": "2026-01-21T21:52:57+00:00",
        "observed": {
            "role_present": True,
            "role": "role1",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": "1768983854"},
            "uptime_s": 3600,
        },
    }
    hosts = sorted(["host-b", "host-a", "host-c"])  # Sort before passing to render
    latest = dict.fromkeys(hosts, record)
    latest_ok = dict.fromkeys(hosts, record)
    header, lines = render_monitor_lines(
        hosts=hosts,
        latest=latest,
        latest_ok=latest_ok,
        max_width=0,
        cap_widths=False,
        col_sep="  ",
        start=1,
        limit=1,
    )
    assert "HOST" in header
    assert len(lines) == 1
    assert "host-b" in lines[0]


# test_record_matches_vault_path removed - path filtering no longer exists
# Records no longer contain path fields after CLI path arguments were removed


def test_quarantine_status_checks_future():
    """TC_QUAR should only show YES if quarantine time is in the future."""
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    past_time = (now - timedelta(hours=1)).isoformat()
    future_time = (now + timedelta(hours=1)).isoformat()

    record = {
        "ok": True,
        "ts": "2026-01-21T21:52:57+00:00",
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {},
            "uptime_s": 3600,
        },
    }

    # Past quarantine - should show "-"
    tc_data_past = {"quarantine_until": past_time}
    values = build_row_values("host1", record, tc_data=tc_data_past)
    assert values["tc_quar"] == "-"

    # Future quarantine - should show "YES"
    tc_data_future = {"quarantine_until": future_time}
    values = build_row_values("host1", record, tc_data=tc_data_future)
    assert values["tc_quar"] == "YES"

    # No quarantine - should show "-"
    tc_data_none = {"quarantine_until": None}
    values = build_row_values("host1", record, tc_data=tc_data_none)
    assert values["tc_quar"] == "-"


def test_puppet_columns_pp_match_healthy():
    """Test PP_LAST, PP_MATCH, HEALTHY columns with SHA-based logic."""
    from datetime import datetime

    from fleetroll.commands.monitor.cache import ShaInfoCache
    from fleetroll.constants import DEFAULT_GITHUB_REPO

    now = datetime.now(UTC)
    now_epoch = int(now.timestamp())

    # TC data with fresh last_date_active
    tc_data_fresh = {
        "ts": now.isoformat(),
        "last_date_active": now.isoformat(),
    }

    # Test 1: SHA-based comparison - puppet SHA matches GitHub branch SHA -> PP_MATCH=Y
    github_refs = {
        "testuser/ronin_puppet:test-branch": {
            "sha": "test_git_sha_1234",  # pragma: allowlist secret
            "url": "https://api.github.com/repos/testuser/ronin_puppet/git/refs/heads/test-branch",
        }
    }
    sha_cache = ShaInfoCache(overrides_dir="/nonexistent", vault_dir="/nonexistent")
    sha_cache.override_cache["abc123"] = {
        "user": "testuser",
        "repo": "ronin_puppet",
        "branch": "test-branch",
    }

    record_sha_match = {
        "ok": True,
        "ts": now.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": True,
            "override_sha256": "abc123",
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": str(now_epoch - 3600)},
            "uptime_s": 3600,
            "puppet_git_sha": "test_git_sha_1234",
            "puppet_success": True,
        },
    }
    values = build_row_values(
        "host1",
        record_sha_match,
        tc_data=tc_data_fresh,
        sha_cache=sha_cache,
        github_refs=github_refs,
    )
    assert values["pp_match"] == "Y"
    assert values["healthy"] == "Y"

    # Test 2: SHA-based comparison - puppet SHA mismatch -> PP_MATCH=N
    record_sha_mismatch = {
        "ok": True,
        "ts": now.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": True,
            "override_sha256": "abc123",
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": str(now_epoch - 3600)},
            "uptime_s": 3600,
            "puppet_git_sha": "different_sha",
            "puppet_success": True,
        },
    }
    values = build_row_values(
        "host1",
        record_sha_mismatch,
        tc_data=tc_data_fresh,
        sha_cache=sha_cache,
        github_refs=github_refs,
    )
    assert values["pp_match"] == "N"
    assert values["healthy"] == "N"

    # Test 3: No override - check against master branch
    github_refs_master = {
        f"{DEFAULT_GITHUB_REPO}:master": {
            "sha": "master_sha_123",
            "url": f"https://api.github.com/repos/{DEFAULT_GITHUB_REPO}/git/refs/heads/master",
        }
    }
    record_no_override_match = {
        "ok": True,
        "ts": now.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {},
            "uptime_s": 3600,
            "puppet_git_sha": "master_sha_123",
            "puppet_success": True,
        },
    }
    values = build_row_values(
        "host1", record_no_override_match, tc_data=tc_data_fresh, github_refs=github_refs_master
    )
    assert values["pp_match"] == "Y"
    assert values["healthy"] == "Y"

    # Test 4: No SHA data (no github_refs) → pp_match=dash, healthy=dash
    record_no_sha = {
        "ok": True,
        "ts": now.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": True,
            "override_sha256": "abc123",
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": str(now_epoch - 3600)},  # 1 hour ago
            "uptime_s": 3600,
            "puppet_last_run_epoch": now_epoch - 1800,  # 30 min ago
            "puppet_success": True,
        },
    }
    values = build_row_values("host1", record_no_sha, tc_data=tc_data_fresh, github_refs=None)
    assert values["pp_match"] == "-"
    assert values["healthy"] == "-"

    # Test 5: Puppet failed with SHA data -> PP_MATCH=N
    record_puppet_failed = {
        "ok": True,
        "ts": now.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": True,
            "override_sha256": "abc123",
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": str(now_epoch - 3600)},
            "uptime_s": 3600,
            "puppet_git_sha": "test_git_sha_1234",
            "puppet_success": False,
        },
    }
    values = build_row_values(
        "host1",
        record_puppet_failed,
        tc_data=tc_data_fresh,
        sha_cache=sha_cache,
        github_refs=github_refs,
    )
    assert values["pp_match"] == "N"
    assert values["healthy"] == "N"

    # Test 6: No puppet_git_sha (e.g., Mac host) -> PP_MATCH=-
    record_no_puppet_sha = {
        "ok": True,
        "ts": now.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_osx_1015",
            "override_present": True,
            "override_sha256": "abc123",
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": str(now_epoch - 3600)},
            "uptime_s": 3600,
            "puppet_git_sha": None,
            "puppet_success": None,
        },
    }
    values = build_row_values(
        "host1",
        record_no_puppet_sha,
        tc_data=tc_data_fresh,
        sha_cache=sha_cache,
        github_refs=github_refs,
    )
    assert values["pp_match"] == "-"  # Unknown
    assert values["healthy"] == "-"  # Unknown


def test_pp_exp_column():
    """Test PP_EXP column shows expected puppet SHA."""
    from datetime import datetime

    from fleetroll.commands.monitor.cache import ShaInfoCache
    from fleetroll.constants import DEFAULT_GITHUB_REPO

    now = datetime.now(UTC)
    now_epoch = int(now.timestamp())

    # Test 1: Override present - PP_EXP shows branch HEAD SHA
    github_refs = {
        "testuser/ronin_puppet:test-branch": {
            "sha": "test_branch_sha_1234",
        }
    }
    sha_cache = ShaInfoCache(overrides_dir="/nonexistent", vault_dir="/nonexistent")
    sha_cache.override_cache["abc123"] = {
        "user": "testuser",
        "repo": "ronin_puppet",
        "branch": "test-branch",
    }

    record = {
        "ok": True,
        "ts": now.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": True,
            "override_sha256": "abc123",
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": str(now_epoch)},
            "uptime_s": 3600,
            "puppet_git_sha": "test_branch_sha_1234",
            "puppet_success": True,
        },
    }
    values = build_row_values("host1", record, sha_cache=sha_cache, github_refs=github_refs)
    assert values["pp_exp"] == "test_br"

    # Test 2: No override - PP_EXP shows master HEAD SHA
    github_refs_master = {
        f"{DEFAULT_GITHUB_REPO}:master": {
            "sha": "test_master_sha_5678",
        }
    }
    record_no_ovr = {
        "ok": True,
        "ts": now.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {},
            "uptime_s": 3600,
            "puppet_git_sha": "test_master_sha_5678",
            "puppet_success": True,
        },
    }
    values = build_row_values("host1", record_no_ovr, github_refs=github_refs_master)
    assert values["pp_exp"] == "test_ma"

    # Test 3: No github_refs - PP_EXP shows dash
    values = build_row_values("host1", record_no_ovr, github_refs=None)
    assert values["pp_exp"] == "-"

    # Test 4: No record (unknown host) - PP_EXP shows "?"
    values = build_row_values("host1", None)
    assert values["pp_exp"] == "?"

    # Test 5: Failed audit with no last_ok - PP_EXP shows "-"
    failed_record = {
        "ok": False,
        "ts": now.isoformat(),
        "error": "ssh timeout",
    }
    values = build_row_values("host1", failed_record)
    assert values["pp_exp"] == "-"


def _sep_positions(line: str) -> list[int]:
    positions = []
    idx = 0
    while True:
        idx = line.find(" | ", idx)
        if idx == -1:
            return positions
        positions.append(idx)
        idx += 3


def test_detect_common_fqdn_suffix_all_same():
    """All hosts share the same FQDN suffix."""
    hosts = [
        "host1.example.com",
        "host2.example.com",
        "host3.example.com",
    ]
    assert detect_common_fqdn_suffix(hosts) == ".example.com"


def test_detect_common_fqdn_suffix_different():
    """Hosts have different FQDN suffixes."""
    hosts = [
        "host1.example.com",
        "host2.different.org",
    ]
    assert detect_common_fqdn_suffix(hosts) is None


def test_detect_common_fqdn_suffix_no_fqdn():
    """Hosts without FQDN (no dots)."""
    hosts = ["host1", "host2", "host3"]
    assert detect_common_fqdn_suffix(hosts) is None


def test_detect_common_fqdn_suffix_mixed():
    """Mix of FQDN and non-FQDN hosts."""
    hosts = ["host1.example.com", "host2"]
    assert detect_common_fqdn_suffix(hosts) is None


def test_detect_common_fqdn_suffix_empty():
    """Empty host list."""
    assert detect_common_fqdn_suffix([]) is None


def test_detect_common_fqdn_suffix_single():
    """Single host with FQDN."""
    assert detect_common_fqdn_suffix(["host.example.com"]) == ".example.com"


def test_detect_common_fqdn_suffix_long():
    """Long FQDN suffix."""
    hosts = [
        "host1.test.releng.mdc1.mozilla.com",
        "host2.test.releng.mdc1.mozilla.com",
    ]
    assert detect_common_fqdn_suffix(hosts) == ".test.releng.mdc1.mozilla.com"


def test_humanize_age_recent():
    """Recent timestamps (less than 1 minute)."""
    now = datetime.now(UTC)
    recent = (now - timedelta(seconds=30)).isoformat()
    assert humanize_age(recent) == "<1m ago"


def test_humanize_age_minutes():
    """Timestamps in minutes range."""
    now = datetime.now(UTC)
    two_min = (now - timedelta(minutes=2)).isoformat()
    four_min = (now - timedelta(minutes=4)).isoformat()
    ten_min = (now - timedelta(minutes=10)).isoformat()

    assert humanize_age(two_min) == "<3m ago"
    assert humanize_age(four_min) == "<5m ago"
    assert humanize_age(ten_min) == "<15m ago"


def test_humanize_age_hours():
    """Timestamps in hours range."""
    now = datetime.now(UTC)
    one_hour = (now - timedelta(hours=1)).isoformat()
    three_hours = (now - timedelta(hours=3)).isoformat()
    six_hours = (now - timedelta(hours=6)).isoformat()

    assert humanize_age(one_hour) == "<2h ago"
    assert humanize_age(three_hours) == "<4h ago"
    assert humanize_age(six_hours) == "<8h ago"


def test_humanize_age_days():
    """Timestamps in days range."""
    now = datetime.now(UTC)
    one_day = (now - timedelta(days=1)).isoformat()
    two_days = (now - timedelta(days=2)).isoformat()
    five_days = (now - timedelta(days=5)).isoformat()

    assert humanize_age(one_day) == "<2d ago"
    assert humanize_age(two_days) == "<3d ago"
    assert humanize_age(five_days) == "<1w ago"


def test_humanize_age_weeks():
    """Timestamps in weeks range."""
    now = datetime.now(UTC)
    ten_days = (now - timedelta(days=10)).isoformat()
    twenty_days = (now - timedelta(days=20)).isoformat()

    assert humanize_age(ten_days) == "<2w ago"
    assert humanize_age(twenty_days) == "<1mo ago"


def test_humanize_age_months():
    """Timestamps in months range."""
    now = datetime.now(UTC)
    two_months = (now - timedelta(days=60)).isoformat()
    four_months = (now - timedelta(days=120)).isoformat()
    seven_months = (now - timedelta(days=210)).isoformat()

    assert humanize_age(two_months) == "<3mo ago"
    assert humanize_age(four_months) == "<6mo ago"
    assert humanize_age(seven_months) == "<1y ago"


def test_humanize_age_year():
    """Timestamps over a year old."""
    now = datetime.now(UTC)
    two_years = (now - timedelta(days=730)).isoformat()
    assert humanize_age(two_years) == ">=1y ago"


def test_humanize_age_empty():
    """Empty or missing timestamp."""
    assert humanize_age("") == "?"
    assert humanize_age("?") == "?"


def test_humanize_age_invalid():
    """Invalid timestamp format."""
    assert humanize_age("invalid-timestamp") == "invalid-timestamp"
    assert humanize_age("2024-13-45") == "2024-13-45"


def test_age_seconds_valid():
    """Calculate age in seconds for valid timestamp."""
    now = datetime.now(UTC)
    past = (now - timedelta(seconds=120)).isoformat()
    age = age_seconds(past)
    assert age is not None
    assert 118 <= age <= 122  # Allow small timing variance


def test_age_seconds_recent():
    """Age should never be negative (clamped to 0)."""
    # Future timestamp should return 0
    now = datetime.now(UTC)
    # The function clamps to 0, but for past times it should be positive
    past = (now - timedelta(seconds=5)).isoformat()
    age = age_seconds(past)
    assert age is not None
    assert age >= 0


def test_age_seconds_empty():
    """Empty or missing timestamp."""
    assert age_seconds("") is None
    assert age_seconds("?") is None


def test_age_seconds_invalid():
    """Invalid timestamp format."""
    assert age_seconds("invalid") is None


def test_format_ts_with_age():
    """Format timestamp with age."""
    now = datetime.now(UTC)
    recent = (now - timedelta(minutes=2)).isoformat()
    result = format_ts_with_age(recent)
    assert recent in result
    assert "<3m ago" in result
    assert "(" in result
    assert ")" in result


def test_format_ts_with_age_empty():
    """Empty timestamp."""
    assert format_ts_with_age("") == "?"
    assert format_ts_with_age("?") == "?"


def test_humanize_duration_seconds():
    """Durations in seconds."""
    assert humanize_duration(0) == "0s"
    assert humanize_duration(30) == "30s"
    assert humanize_duration(59) == "59s"


def test_humanize_duration_minutes():
    """Durations in minutes."""
    assert humanize_duration(60) == "1m"
    assert humanize_duration(120) == "2m"
    assert humanize_duration(3599) == "59m"


def test_humanize_duration_hours():
    """Durations in hours."""
    assert humanize_duration(3600) == "1h 00m"
    assert humanize_duration(7325) == "2h 02m"
    assert humanize_duration(86399) == "23h 59m"


def test_humanize_duration_days():
    """Durations in days."""
    assert humanize_duration(86400) == "1d 00h"
    assert humanize_duration(172800) == "2d 00h"
    assert humanize_duration(90061) == "1d 01h"


def test_humanize_duration_none():
    """None duration."""
    assert humanize_duration(None) == "-"


def test_humanize_duration_min_unit_minutes():
    """Duration with min_unit='m' shows '<1m' for values under 60s."""
    assert humanize_duration(30, min_unit="m") == "<1m"
    assert humanize_duration(59, min_unit="m") == "<1m"
    assert humanize_duration(60, min_unit="m") == "1m"
    assert humanize_duration(120, min_unit="m") == "2m"


def test_humanize_duration_negative_clamped():
    """Negative durations are clamped to 0."""
    assert humanize_duration(-10) == "0s"


def test_pp_last_from_puppet_state_ts():
    """PP_LAST calculated from puppet_state_ts with fixed audit time."""
    now = datetime.now(UTC)
    audit_time = now
    puppet_time = now - timedelta(minutes=30)

    record = {
        "ok": True,
        "ts": audit_time.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {},
            "uptime_s": 3600,
            "puppet_state_ts": puppet_time.isoformat(),
            "puppet_success": True,
        },
    }

    values = build_row_values("host1", record, last_ok=record)
    assert values["pp_last"] == "30m"


def test_pp_last_fail_suffix_with_state_ts():
    """PP_LAST with FAIL suffix when puppet_success is False."""
    now = datetime.now(UTC)
    audit_time = now
    puppet_time = now - timedelta(minutes=15)

    record = {
        "ok": True,
        "ts": audit_time.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {},
            "uptime_s": 3600,
            "puppet_state_ts": puppet_time.isoformat(),
            "puppet_success": False,
        },
    }

    values = build_row_values("host1", record, last_ok=record)
    assert "FAIL" in values["pp_last"]
    assert values["pp_last"].startswith("15m")


def test_pp_last_no_puppet_data():
    """PP_LAST shows '--' when no puppet data available."""
    now = datetime.now(UTC)

    record = {
        "ok": True,
        "ts": now.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {},
            "uptime_s": 3600,
            # No puppet_state_ts, no puppet_last_run_epoch
        },
    }

    values = build_row_values("host1", record, last_ok=record)
    assert values["pp_last"] == "--"


def test_header_layout_fits_one_line():
    """Left + right fit on one line."""
    from fleetroll.commands.monitor.display import compute_header_layout

    left = "fleetroll 1.0.0"
    right = "hosts=5"
    usable_width = 50
    result = compute_header_layout(left, right, usable_width)
    assert result == 1


def test_header_layout_requires_two_lines():
    """Left + right overlap requires two lines."""
    from fleetroll.commands.monitor.display import compute_header_layout

    left = "fleetroll 1.0.0: very long status line with lots of information"
    right = "fqdn=example.com, hosts=100, updated=5m ago"
    usable_width = 50
    result = compute_header_layout(left, right, usable_width)
    assert result == 2


def test_header_layout_exactly_fits():
    """Exactly fits (left + 1 + right == usable_width) uses one line."""
    from fleetroll.commands.monitor.display import compute_header_layout

    left = "a" * 20
    right = "b" * 29
    usable_width = 50  # 20 + 1 + 29 = 50
    result = compute_header_layout(left, right, usable_width)
    assert result == 1


def test_header_layout_narrow_terminal():
    """Extremely narrow terminal uses two lines."""
    from fleetroll.commands.monitor.display import compute_header_layout

    left = "fleetroll 1.0.0"
    right = "hosts=5"
    usable_width = 20  # Too narrow for both
    result = compute_header_layout(left, right, usable_width)
    assert result == 2


def test_os_column_ok_record():
    """Test OS column shows os_type for OK records."""
    record = {
        "ok": True,
        "ts": "2026-01-21T21:52:57+00:00",
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "os_type": "Linux",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": "1768983854"},
            "uptime_s": 3600,
        },
    }
    values = build_row_values("host1", record, last_ok=record)
    assert values["os"] == "Linux"


def test_os_column_darwin():
    """Test OS column shows Darwin for macOS hosts."""
    record = {
        "ok": True,
        "ts": "2026-01-21T21:52:57+00:00",
        "observed": {
            "role_present": True,
            "role": "gecko_t_osx_1400_r8",
            "os_type": "Darwin",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": "1768983854"},
            "uptime_s": 3600,
        },
    }
    values = build_row_values("host1", record, last_ok=record)
    assert values["os"] == "Darwin"


def test_os_column_unknown_host():
    """Test OS column shows ? for unknown hosts."""
    values = build_row_values("host1", None)
    assert values["os"] == "?"


def test_os_column_failed_host():
    """Test OS column shows - for failed hosts."""
    failed_record = {
        "ok": False,
        "ts": "2026-01-21T21:52:57+00:00",
        "error": "SSH connection failed",
    }
    values = build_row_values("host1", failed_record)
    assert values["os"] == "-"


def test_os_column_missing_in_observed():
    """Test OS column shows - when os_type is missing from observed."""
    record = {
        "ok": True,
        "ts": "2026-01-21T21:52:57+00:00",
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": "1768983854"},
            "uptime_s": 3600,
        },
    }
    values = build_row_values("host1", record, last_ok=record)
    assert values["os"] == "-"


def test_os_column_in_columns_list():
    """Test that os column appears in compute_columns_and_widths output."""
    record = {
        "ok": True,
        "ts": "2026-01-21T21:52:57+00:00",
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "os_type": "Linux",
            "override_present": False,
            "override_sha256": None,
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": "1768983854"},
            "uptime_s": 3600,
        },
    }
    hosts = ["host1"]
    latest = {hosts[0]: record}
    latest_ok = {hosts[0]: record}
    columns, widths = compute_columns_and_widths(
        hosts=hosts,
        latest=latest,
        latest_ok=latest_ok,
        max_width=0,
        cap_widths=False,
        sep_len=3,
    )
    assert "os" in columns
    assert "os" in widths
    assert widths["os"] >= len("OS")  # At least header width
    assert widths["os"] >= len("Linux")  # At least data width
