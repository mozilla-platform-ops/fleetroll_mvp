# Live host diagnosis JSON (version 1)

`uv run fleetroll host diagnose HOST --json` runs one live Linux
`sudo -n gwhc --json` check over SSH. It neither changes the host nor writes
recorded observations. Host files and multiple hosts are unsupported. SSH options,
connection timeout (10 seconds), and overall timeout (30 seconds) are configurable;
see `host diagnose --help`.

This interface supplies diagnostic evidence for consumers such as Medic. Medic
integration is separate. Existing `host idle` and reboot behavior are unchanged.

## Exit semantics and trust

Fleetroll exits 0 when a diagnostic report was collected and validated, whether
the worker is healthy or unhealthy. It exits 1 on unavailable, malformed,
inconsistent, or unsupported evidence, and still emits the JSON envelope. Invalid
CLI arguments use Click's normal usage errors. Consumers must inspect both the
exit status and result fields; `ok: true` means diagnosis completed, not worker
healthy or safe to reboot.

The upstream gwhc exit code is independent: 0 means all checks pass/warn, and 1
means at least one check failed. Both are accepted when the report agrees with
the exit code and per-check statuses. Other gwhc exits are execution failures.
SSH nonzero exits invalidate partial output; SSH timeout is distinguished from
other transport failures. Invalid output after an exit 1 (for example, denied
sudo with no report) is unavailable diagnosis, not evidence of an unhealthy worker.

Current gwhc reports have no schema version. Fleetroll validates their existing
shape for diagnostics: boolean `ok`, nonempty host and state, timezone-aware
timestamp, and a nonempty checks list. Every check must have a nonempty name,
`pass`/`warn`/`fail` status, string detail, and object data. An unhealthy diagnosis
can include a check that failed to execute; consumers must examine its evidence
rather than interpreting aggregate health as a specific diagnosis.

No versioned upstream schema is supported yet. Any explicit `schema_version`,
including null or an unexpected type, is rejected as unsupported and its raw
report retained. Upstream versioning and agreed structured zombie semantics are
tracked in `mvp-6xh4`. Do not assume upstream version 1 is supported just because
the Fleetroll envelope is version 1.

**The normalized `zombie.status` is currently always `unknown`.** Explicit legacy
evidence, such as `generic_worker_process.data.zombie_pids` and process state
records, is preserved in full for inspection. It does not qualify for automated
zombie recovery. `DEGRADED`, detail text, empty/missing evidence, and unknown
schema versions never imply either a zombie or its absence. Normalizing confirmed
zombies requires a separately agreed upstream contract and consumer tests.

## Envelope

| Field | Meaning |
| --- | --- |
| `schema_version` | Fleetroll diagnosis envelope version, currently integer 1. |
| `host` | Requested SSH target, expanded to an FQDN; may include a user prefix. |
| `ts` | Local UTC time when Fleetroll emitted the result. |
| `checked_at` | Remote UTC time immediately before collection; null if unavailable. |
| `os_type` | Remote OS or null. Only Linux is supported. |
| `ok` | Whether collection and diagnostic validation completed. |
| `collection_status` | `completed` or `failed`. |
| `reason` | Machine-readable completion/failure reason. |
| `ssh_exit_code` | SSH return code, or null on a local execution exception. |
| `gwhc_exit_code` | Upstream return code, or null if unavailable. |
| `gwhc_schema_version` | Original upstream schema value, or null when absent. |
| `contract_status` | `legacy`, `unsupported`, or `unknown`. |
| `health` | `healthy`, `unhealthy`, or `unknown`. |
| `gwhc_state` | Validated upstream state string, or null. Never an action gate. |
| `gwhc_ts` | Validated upstream observation time, or null. |
| `reported_host` | Hostname reported by gwhc, or null. Distinct from requested target. |
| `checks` | All validated check objects, including nested data and additive fields; otherwise empty. |
| `gwhc` | Complete parsed upstream object, including invalid/unsupported reports, or null. |
| `zombie` | Object with `status: unknown` and a machine-readable `reason`. |
| `stderr` | SSH/check diagnostic stderr when execution returned normally. |
| `error` | Optional local execution exception description. |

Raw `gwhc` is preserved evidence, not a validated fallback when `ok` is false.
Missing upstream versions are distinguished from explicit null using
`contract_status` and the raw report. Source timestamps are retained unchanged;
local `ts` does not refresh them. Requested and reported hostnames are not asserted
to be equivalent. These fields do not establish Taskcluster worker identity.

Completion reason is `diagnosis_completed`. Failure reasons include
`ssh_timeout`, `ssh_failed`, `ssh_error`, `unsupported_os`, `gwhc_missing`,
`check_timestamp_invalid`, `gwhc_exit_missing`, `invalid_gwhc_json`,
`gwhc_execution_failed`, `unsupported_gwhc_schema`, `invalid_gwhc_report`,
`invalid_gwhc_checks`, and `inconsistent_gwhc_result`.

Consumers must explicitly accept envelope versions they implement, tolerate
additive fields, and retain unknown values. This is a snapshot, not a reservation,
freshness guarantee, recovery authorization, or proof of recovery.

## Migration from idle diagnostics

Use `host diagnose --json` to inspect unhealthy gwhc findings. Unlike `host idle`,
it preserves valid exit 1 reports and every structured check rather than returning
early as `gwhc_failed`. Do not transfer idle command exit-code assumptions:
a completed unhealthy diagnosis exits 0. Keep idle/action policy separate.
