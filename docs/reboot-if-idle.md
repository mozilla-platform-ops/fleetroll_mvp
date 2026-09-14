# Reboot hosts only when idle

## Intended behavior

Add a one-shot `host-reboot-if-idle` operation for Linux generic-worker hosts.
Missing or incompatible `gwhc` must refuse reboot; do not fall back to cached
Fleetroll observations or process/load heuristics.

1. Run privileged `gwhc --json` on the host. Continue only with explicit,
   trustworthy idle evidence.
2. Quarantine the actual Taskcluster worker for five minutes by default
   (configurable), and confirm success. Quarantine prevents new task claims.
   Preserve an existing longer quarantine; never shorten or clear it.
3. Run `gwhc` again using evidence collected after quarantine confirmation.
   If a task was claimed between the first check and quarantine, refuse reboot.
   Busy, unknown, stale, malformed, missing, or failed checks all refuse reboot.
4. Request reboot only while quarantine is still effective with sufficient time
   remaining. If the operation has outlasted that window, refuse or renew and
   repeat verification; never reboot using expired protection.
5. Report reboot requested separately from host returned. Verify a new boot and
   worker availability when reporting recovery; quarantine expiry is not proof.

Busy hosts are skipped immediately rather than queued until idle. If the second
check refuses, let the short quarantine expire naturally. Audit both checks,
worker identity, quarantine expiry, refusal reason, and reboot/recovery results.

## Required gwhc changes in ronin_puppet

Source: `modules/linux_generic_worker/files/generic-worker-health-check` in
`~/git/ronin_puppet` (reviewed 2026-09-10).

Current `_gw_task_state()` scans the last 50 journal lines backward. It recognizes
`No task claimed` as idle and `Starting task feature` or `Executing command` as
busy. It does not explicitly recognize claims, enforce evidence freshness, scope
the journal to the current worker process/boot, or check journalctl return codes.
An older idle line can therefore mask a newly claimed task before startup logs.
`summarize()` also maps unknown task state to top-level `IDLE`. Exit zero means
health checks passed or warned, and a running task is only a warning; exit zero
does not authorize reboot.

Prerequisites before enabling automated reboot:

- Represent unknown task state explicitly, never as `IDLE`. Keep health status
  separate from reboot eligibility.
- Inspect generic-worker claim/start/finish handling to identify authoritative
  evidence covering claimed, starting, executing, and finishing/uploading tasks.
  Treat every outstanding task as busy through final completion. The exact
  evidence source is still to be determined; adding a guessed log pattern is
  insufficient.
- Provide fresh idle evidence after a caller-specified boundary (such as a journal
  cursor or timestamp recorded after quarantine confirmation). Scope evidence to
  the current boot and worker instance. Old idle lines, log truncation, worker
  restarts, and missing evidence must yield unknown rather than idle.
- Validate journal access and subprocess exit status; permissions failures,
  timeouts, unavailable logs, and parsing errors must yield unknown/failure.
- Expose a versioned machine-readable contract with task state, evidence time and
  identity, and refusal reasons. Fleetroll must reject legacy reports that cannot
  provide these guarantees. Do not authorize from top-level `state` or `ok` alone.
- Establish that the post-quarantine evidence accounts for any claim already in
  flight. Repeating the current check or inserting a fixed sleep is not proof;
  if the evidence cannot establish this, refuse reboot.

## Validation

Test the detector with real claim/start/finish log sequences or authoritative
worker-state fixtures, beyond the existing rendered-report snapshots. Cover:

- Old idle evidence followed by a claim with no startup message yet.
- Claimed/starting, executing, and finishing tasks all refusing reboot.
- Unknown state, journal errors, stale evidence, previous-boot logs, truncated
  logs, worker restarts, and unsupported gwhc versions refusing reboot.
- Idle before quarantine followed by busy after quarantine: skip, no reboot.
- Fresh confirmed idle after quarantine: exactly one reboot request.
- Quarantine failure/expiry and preservation of an existing longer quarantine.
- Missing gwhc, SSH failures, malformed JSON, and unconfirmed reboot/recovery.

The Fleetroll feature depends on implementing and deploying the stronger gwhc
contract in ronin_puppet. This document records that prerequisite; it does not
change gwhc or reboot any hosts.
