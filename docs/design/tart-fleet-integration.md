# Tart fleet integration proposal

Proposed integration, not an implemented Fleetroll command set.
[`tart-fleet`](https://github.com/rcurranmoz/tart-fleet) manages the macOS Tart VM
tester pool `gecko-t-osx-1500-m-vms`. The design was based on eight M4 Mac minis
with two VMs each; confirm inventory and source behavior before implementation.

Absorb inspection into existing gather/monitor/inspect paths. Reserve a temporary
`fleetroll tart` namespace for orchestration or capabilities with no existing
home, then reconsider placement when they generalize.

## Capability mapping

| tart-fleet capability | Proposed Fleetroll home |
| --- | --- |
| `overview` | `tart overview`: OCI/queue preamble followed by a Tart monitor view |
| `status` | Tart monitor view: running VM count, sleep, disk, flags; no TC/OCI fetch required |
| `vms`, `host HOST` | Per-host inspection section with VM MAC, worker ID, activity, latest task |
| `audit` | Tart-gated audit rules below |
| `ssh` | Keep using SSH; no wrapper |
| `randomize-mac` | `tart randomize-mac HOST VM` |
| `rollout` | `tart rollout HOST...` |
| `build` | `tart build` for image-build workflow dispatch; long-term scope undecided |

These names are design targets; existing CLI help defines available commands.

## Collected data and checks

Host observations would add `tart_version`, raw `sleep_setting`, `console_user`,
integer `disk_used_pct`, boolean `runbuildbot_present`, and `cached_oci_digest`
(the cache fallback when a VM sidecar is absent).

Each VM would retain:

| Fields | Source |
| --- | --- |
| `vm_name`, `vm_state` | Tart inspection |
| `vm_mac` | `~/.tart/vms/<vm>/config.json` |
| `worker_id` | `mac-` plus the final six MAC hex digits, without colons |
| `image_digest`, `image_source_url`, `cloned_at` | Lines 1–3 of `~/.tart/vms/<vm>/.image-source` |
| `tc_last_active`, `tc_latest_task_id` | TC worker activity and latest task |

Fleet-wide OCI data would include `oci_prod_latest_digest` and `oci_tags`.

| Audit rule | Severity | Condition |
| --- | --- | --- |
| `pmset-sleep` | Error | Sleep is not 0 |
| `run-buildbot-semaphore` | Error | `/var/tmp/semaphore/run-buildbot` exists; `gw_checker.sh` would reboot every 30 minutes |
| `console-user` | Error | User is not `admin`, preventing LaunchAgent autostart |
| `vm-count` | Error | Running count is not 2 |
| `disk-pressure` | Warning/error | Disk at least 75% / 90% full |
| `mac-collision` | Error | VMs on different hosts share a MAC and thus TC worker ID |
| `stalled-worker` | Warning/error | VM running, queued work present, TC inactivity over 30 / 60 minutes |
| `high-uptime` | Warning | Uptime over 90 days |

Gate checks on Tart-host identification. The initial hostname proposal is
`macmini-m4-*`; role-based or explicit configuration remains an open decision.

## Implementation boundaries

- `fleetroll/tart.py`: build a single-round-trip inspection script, parse into
  typed host/VM data, and extract pure VM-health and MAC-to-worker-ID functions.
  Reuse tart-fleet's inspection/parsing logic where compatible.
- `fleetroll/commands/gather_host.py`: collect and persist Tart observations;
  `fleetroll/audit.py`: evaluate the eight rules above.
- `fleetroll/taskcluster.py`: reuse worker queries and whole-pool pending counts;
  check existing support before adding helpers.
- `fleetroll/oci.py` (or Tart-local module): registry tags/digests, with an explicit
  decision on SSH fallback when direct registry access fails.
- `fleetroll/commands/tart/`: orchestration adapters using existing Fleetroll SSH
  and CLI conventions. Rebuild display code using Fleetroll's rendering patterns.

## Proposed image rollout

Process hosts sequentially and stop on the first failed phase:

1. **Preflight:** SSH reachable, Tart installed, two running VMs, disk below 90%.
2. **Drain:** unload LaunchAgents, stop/delete both VMs (90-second timeout).
3. **Pull:** `tart pull --insecure <source>` (600-second timeout).
4. **Clone/MAC:** clone two VMs, randomize MACs, verify distinct addresses.
5. **Sidecar:** record digest, source URL, and clone timestamp in `.image-source`.
6. **Start:** load LaunchAgents and confirm both VMs are running.
7. **Verify:** poll TC for both new worker IDs (five-minute timeout).

The design includes a dry run without host writes. Lifecycle/safety behavior must
be established before implementation; the phase name “drain” alone does not prove
that deleting a VM cannot interrupt work.

## Delivery and open decisions

Start with read-only gathering, audit rules, and a Tart monitor view. Add rollout,
MAC changes, and build dispatch after the data path works. Then review whether
rebuild orchestration generalizes to other hosts or belongs in the proposed
[central service](client-server-design.md).

Resolve these questions before committing to the write interface:

- Does tart-fleet remain an experimental tool or retire after cutover, and who
  maintains the integrated capability?
- Should image-build dispatch remain in Fleetroll or use a documented `gh`
  workflow instead?
- Should orchestration stay local or require a separately designed server write
  capability? The current server proposal keeps host writes client-side.
- Should host identification use role, hostname, or explicit configuration?
- Must operators reach the internal OCI registry directly over VPN, or should
  Fleetroll retain tart-fleet's SSH fallback?
