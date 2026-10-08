# Operator-triggered reboot when gwhc reports idle

## Initial version

Add a one-host-at-a-time `host-reboot-if-idle` operation for Linux
generic-worker hosts. This is a best-effort operator convenience that follows
the existing manual workflow: run `gwhc`, inspect its task state, and reboot
when it reports `IDLE`.

This version does not quarantine the worker or perform a second check. A task
may be claimed after the idle check, and the current `gwhc` log heuristic may
report stale or unknown state as `IDLE`. This risk is accepted for this
operator-triggered version. Do not use it as an unattended or fleet-wide reboot
policy.

For each invocation:

1. Connect to exactly one host. Confirm from live host state that it is Linux
   and that `gwhc` is installed; do not use cached Fleetroll observations to
   authorize reboot.
2. Run `sudo gwhc --json` once in the same SSH session as the reboot request.
   Require a successful command exit and valid JSON object with a top-level
   `state` value exactly equal to `IDLE`. Missing fields, any other state,
   invalid JSON, command failure, or SSH failure refuse reboot. Do not infer
   idle from the health-check exit code or from cached observations.
3. If the check passes, issue `sudo systemctl --no-block reboot` immediately.
   Report “reboot requested” only if the command returns success. If SSH
   disconnects before the request result is known, report an unknown outcome;
   do not claim that the host rebooted or recovered.

The command must clearly state that `IDLE` is the report from the current
best-effort `gwhc` implementation and does not close the task-claim race.

## Acceptance cases for the initial version

- One Linux host with `gwhc` reporting top-level `state: IDLE` receives one
  reboot request and the output says “reboot requested.”
- Non-Linux hosts, missing `gwhc`, nonzero check exits, invalid JSON, missing or
  non-`IDLE` state, and SSH failures do not receive a reboot request.
- A reboot-command failure is reported as failed; a disconnect before its
  result is known is reported as unknown.
- No quarantine, second `gwhc` check, cached-state fallback, or recovery claim
  is part of the initial command.
- The tool targets one host per invocation and records the check and request
  outcome in Fleetroll's audit log.

## Safer follow-up (`mvp-fw8r`)

The follow-up work will harden `gwhc` in ronin_puppet and add a safer Fleetroll
reboot flow. It must:

- Represent unknown task state explicitly and separately from health status.
- Use authoritative task-lifecycle evidence covering claims through final
  completion; the exact source must be established rather than guessed from log
  patterns.
- Scope fresh evidence to the current boot and worker instance, and treat old,
  truncated, missing, or inaccessible evidence as unknown.
- Validate journal access and subprocess exit status, and provide a versioned
  JSON contract with task state, evidence time and identity, and refusal
  reasons.
- In Fleetroll, quarantine the actual Taskcluster worker, preserve longer
  existing quarantines, recheck with evidence collected after quarantine, and
  reboot only while quarantine remains effective.
- Distinguish reboot requested from host returned; verify a new boot and worker
  availability before reporting recovery.

Until this follow-up is complete, the initial command remains best-effort and
operator-triggered only.
