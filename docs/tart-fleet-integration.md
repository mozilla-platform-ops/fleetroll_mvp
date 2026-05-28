# tart-fleet Integration Design

## Context

[`tart-fleet`](https://github.com/rcurranmoz/tart-fleet) is an operator CLI
for Mozilla's macOS Tart VM tester fleet (`gecko-t-osx-1500-m-vms`). It
provides dashboard visibility, anomaly detection, TC worker mapping, and
rolling image rollout across 8 M4 Mac minis, each running 2 Tart VMs.

It overlaps fleetroll's domain: same SSH-and-inspect pattern, same Taskcluster
data source, same operator audience. The goal of this integration is to absorb
that functionality rather than maintain a parallel tool.

**Constraint**: no grafted-on parallel subcommand tree. Where fleetroll already
has an analog (audit, per-host inspect, monitor), tart-fleet's data gets folded
into those existing verbs. A `fleetroll tart` namespace is used only as a
staging area for capabilities fleetroll has no home for yet.

---

## Integration Shape

### Absorb rule

A tart-fleet capability is **absorbed** into an existing fleetroll verb when:
- fleetroll already does the same kind of thing for other host classes, **or**
- it is purely additive data (new columns, new audit rules)

### Namespace rule

A capability lives under **`fleetroll tart <sub>`** when:
- fleetroll has no equivalent verb yet, **or**
- it is write/orchestration (not inspection)

A namespaced subcommand graduates out when its pattern proves general enough
to apply to other host classes.

---

## Per-Capability Mapping

| tart-fleet command | Integration home | Notes |
|---|---|---|
| `overview` | `fleetroll tart overview` + `host-monitor` view mode | Command prints non-tabular preamble (OCI registry stats, TC pool pending count), then renders `host-monitor` in the `tart` view mode. The `tart` view mode is also accessible standalone via `host-monitor --view tart` |
| `status` | absorb → `host-monitor` `tart` view mode | The compact host-level table is the `tart` view mode: VM running count, sleep setting, disk%, flags. No TC/OCI data (faster) |
| `vms` | absorb → `host-inspect` | Per-VM MAC, workerId, TC last-active, latest task — new section in the single-host inspect output, role/OS-gated |
| `audit` | absorb → `audit.py` rules | 6 new rules (see below); gated on `is_tart_host()` predicate |
| `host HOST` | absorb → existing inspect path | Tart VM section appended to single-host output |
| `ssh HOST` | skip | Thin wrapper around `ssh admin@…`; not worth carrying |
| `randomize-mac HOST VM` | `fleetroll tart randomize-mac` | Targeted write op; useful but doesn't generalize yet |
| `rollout HOST...` | `fleetroll tart rollout` | 7-phase orchestrated rebuild; candidate to move server-side under `mvp-si5u` later |
| `build` | `fleetroll tart build` | GHA dispatch for image builds; arguably out of scope long-term; kept here for convenience |

---

## Audit Rules to Add (`fleetroll/audit.py`)

All rules are gated on the host being a Tart VM host (role or OS detection).

| Rule | Severity | Condition |
|---|---|---|
| `pmset-sleep` | error | `pmset sleep` ≠ 0 — host can sleep mid-task |
| `run-buildbot-semaphore` | error | `/var/tmp/semaphore/run-buildbot` present — `gw_checker.sh` will reboot host every 30m |
| `console-user` | error | Console user ≠ `admin` — breaks LaunchAgent VM autostart |
| `vm-count` | error | Running Tart VM count ≠ 2 |
| `disk-pressure` | warn/error | Disk ≥ 75% warn, ≥ 90% error |
| `mac-collision` | error | Same MAC address on VMs across two different hosts — causes TC `workerId` collision |
| `stalled-worker` | warn/error | VM running per Tart, TC pending > 0, TC last-active > 30m (warn) / > 60m (error) |
| `high-uptime` | warn | Host uptime > 90 days |

---

## Data Model Additions

New fields collected per Tart host during inspection and stored as host
observation metadata. These extend the existing observation record in
`fleetroll/db.py` and are collected in `fleetroll/commands/gather_host.py`.

**Host-level:**
- `tart_version` — string
- `sleep_setting` — raw `pmset` value; "0" is healthy
- `console_user` — should be "admin"
- `disk_used_pct` — integer
- `runbuildbot_present` — bool
- `cached_oci_digest` — sha256 from tart's local cache (fallback when no VM sidecar)

**Per-VM (new nested structure, one entry per VM per host):**
- `vm_name` — e.g. `sequoia-tester-1`
- `vm_state` — tart's reported state (`running`, `stopped`, …)
- `vm_mac` — MAC address from `~/.tart/vms/<vm>/config.json`
- `worker_id` — derived: `mac-` + last 6 hex digits of MAC (no colons)
- `image_digest` — sha256 from `~/.tart/vms/<vm>/.image-source` line 1
- `image_source_url` — OCI source URL, line 2 of `.image-source`
- `cloned_at` — ISO timestamp, line 3 of `.image-source`
- `tc_last_active` — ISO timestamp from TC `lastDateActive`
- `tc_latest_task_id` — from TC `latestTask.taskId`

**OCI registry (fleet-wide, not per-host):**
- `oci_prod_latest_digest` — sha256 of the `prod-latest` tag
- `oci_tags` — list of available tags

---

## Module Placement

### New: `fleetroll/tart.py`

Pure functions for SSH inspection and parsing. Mirrors the `INSPECT_SCRIPT` +
`inspect_host()` logic from tart-fleet but returns typed dataclasses compatible
with fleetroll's observation model. Unit-testable in isolation.

Key functions to extract:
- `tart_inspect_script() -> str` — builds the single-SSH-round-trip shell
  script (currently `INSPECT_SCRIPT` in tart-fleet)
- `parse_tart_inspect(output: str) -> TartHostData` — pure parser; no I/O
- `vm_health(vm, host_reachable, tc_data) -> tuple[str, str]` — health icon
  logic; already a pure function in tart-fleet, lifts as-is
- `worker_id_from_mac(mac: str) -> str` — derive TC workerId from MAC
- `is_tart_host(hostname: str) -> bool` — predicate for role/OS gating
  (initially: hostname matches `macmini-m4-*`)

### Extend: `fleetroll/taskcluster.py`

`fetch_workers()` already handles TC pool queries. Add:
- `tc_pending(pool: str) -> int` — pending task count for a pool
- Worker pool constant / config for the Tart pool (`TART_POOL`)

### New: `fleetroll/oci.py` (or section in `tart.py`)

OCI registry queries with SSH fallback:
- `oci_tags(registry, repo) -> list[str]`
- `oci_manifest_digest(registry, repo, tag) -> str`

Keep separate if OCI queries might be needed for non-Tart contexts; fold into
`tart.py` if they stay Tart-specific.

### Extend: `fleetroll/commands/gather_host.py`

Wire `tart.py` parsers into the host gather pipeline. Call
`tart_inspect_script()` for Tart hosts and store the parsed result alongside
existing puppet/override data.

### Extend: `fleetroll/audit.py`

Add the 8 audit rules listed above. Gate each on `is_tart_host()`. Follow
existing rule patterns.

### New: `fleetroll/commands/tart/`

CLI surface for write operations and not-yet-absorbed reads:
- `__init__.py`
- `rollout.py` — 7-phase rebuild: preflight → drain → pull → clone+mac →
  sidecar → start → verify. Phases are the same as tart-fleet; the SSH calls
  use `fleetroll/ssh.py` (`run_ssh`) instead of bespoke subprocess.
- `build.py` — GHA workflow dispatch via `gh` CLI
- `randomize_mac.py` — stop VM, regenerate MAC, reload LaunchAgent

---

## Rollout Phases (for reference)

The 7-phase `rollout` command runs sequentially per host, stopping on first
failure:

1. **preflight** — SSH reachability + 2 VMs running + tart installed + disk < 90%
2. **drain** — unload LaunchAgents, stop + delete both VMs (90s timeout)
3. **pull** — `tart pull --insecure <source>` (600s; OCI pull can be slow)
4. **clone+mac** — clone 2 VMs, randomize each MAC, verify MACs are distinct
5. **sidecar** — write `~/.tart/vms/<vm>/.image-source` (digest + URL + timestamp)
6. **start** — load LaunchAgents, confirm both VMs reach `running` state
7. **verify** — poll TC until both new workerIds appear (5-minute timeout)

Supports `--dry-run` to simulate without write operations. Multiple hosts run
sequentially (not parallel) to avoid draining the whole pool at once.

---

## Reuse Estimate

| Source | Disposition |
|---|---|
| `INSPECT_SCRIPT` shell heredoc | Lift as-is into `tart_inspect_script()` |
| `inspect_host()` parser logic | Lift as-is into `parse_tart_inspect()` |
| `vm_health()` | Lift as-is |
| `tc_workers()` / `tc_pending()` | Adapt to use existing `fleetroll/taskcluster.py`; logic identical |
| `oci_tags()` / `oci_manifest_digest()` | Lift as-is into `fleetroll/oci.py` |
| Rollout phases | Lift logic; replace `ssh()` calls with `run_ssh()` from `fleetroll/ssh.py` |
| Display / table code | Rewrite against fleetroll's Rich/curses patterns |
| CLI wiring | Rewrite against fleetroll's Click structure |

Rough split: ~75% parsing/logic reuse, ~25% adapter/integration work.

---

## Staging

**Phase 1 — Read-only data (no write ops)**
- Add `fleetroll/tart.py` with inspection parsers
- Extend `gather_host.py` to collect Tart data on matching hosts
- Add 8 audit rules to `audit.py`
- Add Tart VM columns to monitor display
- Add `tart` view mode to `host-monitor` (selects and orders Tart-relevant columns)

**Phase 2 — Write operations**
- `fleetroll tart rollout HOST...`
- `fleetroll tart randomize-mac HOST VM`
- `fleetroll tart build`

**Phase 3 — Graduation review**
- After Phase 2 is live: does `rollout` generalize to a `fleetroll host-rebuild`
  pattern for other host classes?
- Should `rollout` move server-side under `mvp-si5u` (centralized epic)?
- Should `build` (GHA dispatch) stay in fleetroll or become a documented
  `gh workflow run` one-liner?

---

## Open Questions

1. **Maintenance handoff**: Does rcurranmoz deprecate `tart-fleet` once
   Phase 1 lands, or keep it as a low-ceremony experimental space? Affects
   whether we coordinate the cutover or just let it coexist.

2. **`rollout` server-side?**: A 7-phase orchestrated write is the kind of
   operation that benefits from auditability and remote trigger. Does it belong
   in the local-write epic (`mvp-kaw5`) or the centralized epic (`mvp-si5u`)?

3. **`build` scope**: GHA workflow dispatch is a release trigger, not fleet
   ops. Is it in scope for fleetroll at all, or a documented `gh` one-liner?

4. **Host matching**: Initially `is_tart_host()` matches `macmini-m4-*`
   hostnames. Should this be role-based (puppet role), hostname pattern, or
   explicit config? Hostname pattern is sufficient now but role is more robust.

5. **OCI registry access**: The registry at `10.49.56.161:5000` is internal;
   fleetroll operators on VPN should reach it directly. The SSH fallback in
   tart-fleet handles the unreachable case — should fleetroll require VPN, or
   keep the fallback?
c
