# Live host diagnosis JSON (version 2)

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

Fleetroll supports upstream gwhc integer `schema_version: 1` and legacy reports
with no version. Both require boolean `ok`, nonempty host and state, timezone-aware
timestamp, and a nonempty checks list. Every check must have a nonempty name,
`pass`/`warn`/`fail` status, string detail, and object data. An unhealthy diagnosis
can include a check that failed to execute; consumers must examine its evidence
rather than interpreting aggregate health as a specific diagnosis.

Other explicit versions, including null, strings, booleans, and floating-point
`1.0`, are unsupported; Fleetroll retains their raw report. Version recognition
sets `contract_status: supported`; required field validation must also succeed
before `ok` becomes true. The upstream and Fleetroll envelope versions are
independent of each other and of the software release.

## Structured zombie evidence

The upstream contract is documented in
[ronin_puppet gwhc](https://github.com/mozilla-platform-ops/ronin_puppet/blob/2d576c2bf6676e05eddf70ed5e0b548a56951710/modules/linux_generic_worker/tests/gwhc/README.md).
For supported upstream v1 reports, Fleetroll normalizes exactly one
`generic_worker_process` check using its `data.zombie_pids` and `data.processes`.
PID arrays must contain distinct positive integers. Each process requires a
distinct positive integer `pid`, nonnegative integer `ppid`, and a recognized
single-character Linux process state. Booleans are not integers for these fields.
The zombie PID set must exactly match the process records with state `Z`.
Optional `unverified_pids` must be a valid PID array disjoint from verified records.

| `zombie.status` | Required evidence | `zombie.reason` |
| --- | --- | --- |
| `present` | Nonempty, consistent zombie evidence and a failing process check. Other unverified PIDs do not negate a confirmed finding. | `confirmed_zombie_processes` |
| `absent` | Empty zombie array, at least one verified process, no unverified PIDs or exited (`X`/`x`) processes, and a passing or warning process check. | `verified_process_inspection` |
| `unknown` | Missing process check or required evidence. | `missing_zombie_evidence` |
| `unknown` | Malformed, duplicate, or contradictory evidence. | `invalid_zombie_evidence` |
| `unknown` | Failed inspection, unverified PIDs, no verified processes, or exited processes without a confirmed zombie. | `unverified_process_inspection` |

Incomplete zombie evidence does not invalidate an otherwise valid general
diagnosis: `ok` can be true while `zombie.status` is unknown. A different failed
check can coexist with verified zombie absence. `DEGRADED`, detail text, and
aggregate health never establish zombie presence or absence. Legacy reports always
produce `unknown` with reason `unversioned_gwhc_schema`; unsupported versions use
`unsupported_gwhc_schema`; failed collection/validation uses `diagnosis_unavailable`.

Zombie findings describe process evidence, not task activity or recovery eligibility.
`HELD` and operator-hold check data remain preserved independently of health and
zombie status. Recovery consumers must honor holds even with confirmed zombies.
Automatic reboot/quarantine policy belongs to `mvp-fw8r`; Medic integration remains
separate. This command never authorizes or performs recovery.

## Envelope

| Field | Meaning |
| --- | --- |
| `schema_version` | Fleetroll diagnosis envelope version, currently integer 2. |
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
| `contract_status` | `supported`, `legacy`, `unsupported`, or `unknown`. Version recognition alone does not validate the report. |
| `health` | `healthy`, `unhealthy`, or `unknown`. |
| `gwhc_state` | Validated upstream state string, or null. Never an action gate. |
| `gwhc_ts` | Validated upstream observation time, or null. |
| `reported_host` | Hostname reported by gwhc, or null. Distinct from requested target. |
| `checks` | All validated check objects, including nested data and additive fields; otherwise empty. |
| `gwhc` | Complete parsed upstream object, including invalid/unsupported reports, or null. |
| `zombie` | Object with `status: present`, `absent`, or `unknown` and a machine-readable `reason`; see evidence rules above. |
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

## Migration

Envelope v1 rejected all explicit upstream versions and always emitted unknown
zombie status. Envelope v2 accepts upstream v1, adds `contract_status: supported`,
and introduces normalized `present`/`absent` statuses. Consumers must explicitly
accept envelope v2 and handle all three zombie statuses; older consumers should
reject it until upgraded. Existing field names, collection/health separation,
raw evidence preservation, and command exit semantics remain the same. Legacy
upstream reports still work for diagnosis with unknown zombie status.

For example, a completed v2 diagnosis of a zombie worker has `ok: true`,
`collection_status: completed`, `health: unhealthy`, `gwhc_exit_code: 1`,
`gwhc_schema_version: 1`, `contract_status: supported`, and
`zombie: {"status": "present", "reason": "confirmed_zombie_processes"}`.
The Fleetroll command exits 0 because diagnosis completed.

Use `host diagnose --json` to inspect unhealthy gwhc findings. Unlike `host idle`,
it preserves valid exit 1 reports and every structured check rather than returning
early as `gwhc_failed`. Do not transfer idle command exit-code assumptions:
a completed unhealthy diagnosis exits 0. Keep idle/action policy separate.
