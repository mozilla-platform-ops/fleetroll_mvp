# Recorded worker processes and disk alerts

`fleetroll gather host` attempts read-only `sudo -n gwhc --json` on Linux,
bounded to 20 seconds plus a two-second kill deadline. Optional diagnostic
failure does not fail an otherwise successful host gather. Valid unhealthy
reports (upstream exit 1) retain every check and metric. Missing gwhc or timeout,
unsupported OS/version, denied execution, malformed reports and timeout remain
explicit collection failures and unknown signals.

| Column | Recorded meaning |
| --- | --- |
| `GW_RUN` | Verified generic-worker process: running, stopped, unknown or stale |
| `DISK` | Supplied filesystem paths, available GiB and percentage; ALERT/UNKNOWN/STALE explicit |
| `GWHC_AGE` | Age of the oldest report, check-start or host-record timestamp |

Zombie/exited-only processes count as stopped. Unreadable process evidence
without a verified live process is unknown. Worker-runner state, aggregate
health and TC activity never supply generic-worker process state. Provisioning
hosts can legitimately have a stopped worker.

Disk alerts use **less than 5 GiB available or less than 10% free**, matching
upstream thresholds. Equality is not an alert. Upstream values are rounded to
one decimal, so alerts use that reported precision. Despite upstream `free_gb`
naming, units are GiB (2^30 bytes). Absolute-only evidence evaluates only the
absolute threshold; percentages are never inferred from free space alone.
Confirmed low space remains an alert with an active cleaner or another
unreadable path. Otherwise incomplete path evidence is unknown.

The producer checks `/`, `/var`, `/home`, skipping missing paths, zero-sized
filesystems and devices already represented by an earlier path. Displayed paths
describe the filesystem actually sampled, not coverage of every mount. Missing
values never become zero.

Evidence becomes stale at **15 minutes**. Missing/invalid timestamps or times
more than 60 seconds ahead of the local clock make freshness unknown. Retained
evidence after a failed host gather is explicitly stale. A successful gather
with failed optional diagnostics shows unknown and does not reuse older
diagnostics. Legacy observations, macOS and Windows show unknown. Normalized
metrics require supported upstream integer schema v1.

## Queries and provenance

All monitor query consumers support these string fields:

| Field | Meaning |
| --- | --- |
| `gwhc_worker` | `running`, `stopped`, `unknown`, `stale` |
| `gwhc_disk` | Formatted path values with `OK`, `ALERT`, `UNKNOWN`, `STALE` prefix |
| `gwhc_disk_alert` | `ok`, `alert`, `unknown`, `stale` |
| `gwhc_age` | Duration age, or missing if unknown |
| `gwhc_ts` | Original upstream report timestamp, or `-` |
| `gwhc_source` | `sudo -n gwhc --json` |

Examples: `gwhc_worker=stopped gwhc_age<15m`, `gwhc_disk_alert=alert`,
`gwhc_worker=unknown|stale`, `sort:gwhc_age:desc,host`.
Web tooltips expose source and report timestamp. The interactive TUI supports
the query fields above. JSON exposes exact timestamps.

## JSON and storage

Gather results and stored observations add `observed.gwhc`, using the existing
[diagnosis envelope v2](host-diagnose.md), independently of upstream schema v1.
It retains collection status/reason, remote check-start time, upstream report
time/version, compatibility, health, checks and raw parsed source evidence.
Additions are `source`, bounded `stdout`, `gwhc_worker` and `gwhc_disks`.
Reports over 256 KiB are rejected with `gwhc_output_too_large`; invalid JSON text
is retained within that bound. Normalized disk fields are `path`, `free_gib`,
`free_pct`, `total_gib`, `status`; worker fields are `status` and `reason`.

Monitor JSON preserves the stored envelope and adds `gwhc_evidence` schema v1:
source, `observation_ts`, `checked_at`, collection status/reason, freshness,
underlying `worker` and `disks`, and qualified `worker_status`/`disk_status`.
Use qualified statuses for freshness-sensitive presentation; underlying metrics
remain historical evidence. This is additive to worker-evidence v3. Follow mode
emits freshness transitions without continuously emitting ticking ages.
Existing JSON persistence needs no migration or old-observation rewrite.

These signals do not change HEALTHY, establish worker availability or task
progress, or authorize cleanup/reboot. Live diagnosis remains separate and does
not persist observations. See the [design](../design/recorded-gwhc.md) for the
verified producer contract and implementation decisions.
