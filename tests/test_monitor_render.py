from fleetroll.commands.monitor import (
    build_row_values,
    clip_cell,
    compute_columns_and_widths,
    record_matches,
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
        "sha": "OVR_SHA",
        "vlt_sha": "VLT_SHA",
        "tc_quar": "TC_QUAR",
        "tc_last": "TC_LAST",
        "tc_j_sf": "TC_J_SF",
        "pp_last": "PP_LAST",
        "applied": "APPLIED",
        "healthy": "HEALTHY",
        "data": "DATA",
    }
    header_cells = render_row_cells(
        labels, columns=columns, widths=widths, include_marker=False
    )
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
    assert values["sha"].startswith(sha[:12])
    assert humanize(sha, words=2) in values["sha"]
    assert values["vlt_sha"].startswith(vlt[:12])
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
    hosts = ["host-b", "host-a", "host-c"]
    latest = {host: record for host in hosts}
    latest_ok = {host: record for host in hosts}
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


def test_record_matches_vault_path() -> None:
    record = {
        "action": "host.audit",
        "host": "h1",
        "override_path": "/etc/override",
        "role_path": "/etc/role",
        "vault_path": "/root/vault.yaml",
    }
    assert record_matches(
        record,
        hosts={"h1"},
        override_path="/etc/override",
        role_path="/etc/role",
        vault_path="/root/vault.yaml",
    )
    assert not record_matches(
        record,
        hosts={"h1"},
        override_path="/etc/override",
        role_path="/etc/role",
        vault_path="/root/other.yaml",
    )
    legacy = {
        "action": "host.audit",
        "host": "h1",
        "override_path": "/etc/override",
        "role_path": "/etc/role",
    }
    assert record_matches(
        legacy,
        hosts={"h1"},
        override_path="/etc/override",
        role_path="/etc/role",
        vault_path="/root/vault.yaml",
    )


def test_quarantine_status_checks_future():
    """TC_QUAR should only show YES if quarantine time is in the future."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
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


def test_puppet_columns_applied_healthy():
    """Test PP_LAST, APPLIED, HEALTHY columns."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    now_epoch = int(now.timestamp())

    # Override present, puppet ran after mtime, succeeded -> APPLIED=Y
    record_applied = {
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
            "puppet_last_run_epoch": now_epoch - 1800,  # 30 min ago (after mtime)
            "puppet_success": True,
        },
    }
    # TC data with fresh last_date_active
    tc_data_fresh = {
        "ts": now.isoformat(),
        "last_date_active": now.isoformat(),
    }
    values = build_row_values("host1", record_applied, tc_data=tc_data_fresh)
    assert values["applied"] == "Y"
    assert values["healthy"] == "Y"
    assert "FAIL" not in values["pp_last"]

    # Override present, puppet ran before mtime -> APPLIED=N
    record_not_applied = {
        "ok": True,
        "ts": now.isoformat(),
        "observed": {
            "role_present": True,
            "role": "gecko_t_linux_talos",
            "override_present": True,
            "override_sha256": "abc123",
            "vault_sha256": None,
            "override_meta": {"mtime_epoch": str(now_epoch - 1800)},  # 30 min ago
            "uptime_s": 3600,
            "puppet_last_run_epoch": now_epoch - 3600,  # 1 hour ago (before mtime)
            "puppet_success": True,
        },
    }
    values = build_row_values("host1", record_not_applied, tc_data=tc_data_fresh)
    assert values["applied"] == "N"
    assert values["healthy"] == "N"

    # No override -> APPLIED=-, HEALTHY=-
    record_no_override = {
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
            "puppet_last_run_epoch": now_epoch - 1800,
            "puppet_success": True,
        },
    }
    values = build_row_values("host1", record_no_override, tc_data=tc_data_fresh)
    assert values["applied"] == "-"
    assert values["healthy"] == "-"

    # Puppet failed -> PP_LAST shows FAIL, APPLIED=N
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
            "puppet_last_run_epoch": now_epoch - 1800,  # After mtime but failed
            "puppet_success": False,
        },
    }
    values = build_row_values("host1", record_puppet_failed, tc_data=tc_data_fresh)
    assert "FAIL" in values["pp_last"]
    assert values["applied"] == "N"
    assert values["healthy"] == "N"


def _sep_positions(line: str) -> list[int]:
    positions = []
    idx = 0
    while True:
        idx = line.find(" | ", idx)
        if idx == -1:
            return positions
        positions.append(idx)
        idx += 3
