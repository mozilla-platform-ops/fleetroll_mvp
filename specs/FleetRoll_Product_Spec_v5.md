# FleetRoll

## Overview

**FleetRoll** is an operations-first host fleet manager that provides inventory, visibility, and safe, staged rollouts of persistent configuration overrides.

FleetRoll is designed for environments where:
- Hosts are long-lived and stateful.
- Configuration is applied via masterless systems such as Puppet.
- Overrides are applied directly on-host and persist until explicitly removed.
- Rolling changes safely matters more than speed.

FleetRoll treats the fleet as both:
- A **roll call**, answering “what hosts exist, and what state are they in?”
- A **rollout surface**, answering “how do we apply this change safely and incrementally?”

It unifies inventory, health visibility, and controlled change into a single system, replacing ad-hoc SSH loops, manual spreadsheets, and implicit tribal knowledge.

---

## Problem statement

Managing host-level overrides across a fleet is currently error-prone and opaque.

Common failure modes:
- No reliable inventory of which hosts have overrides applied.
- Rollouts performed manually with inconsistent pacing.
- Overrides lingering indefinitely after their original purpose.
- Limited visibility into fleet health during a change.
- Rollbacks that rely on memory or fragile scripts.

FleetRoll exists to make host-level change **observable, repeatable, auditable, and reversible**.

---

## Design principles

- **Operations-first**: optimized for RelOps workflows.
- **Inventory before orchestration**: visibility precedes change.
- **Slow is safe**: gradual rollouts are the default.
- **Minimal host assumptions**: SSH and existing primitives only.
- **Explicit over implicit**: human intent is always recorded.

---

## Intended operators and access model

- **Write-capable users**: Release Operations (RelOps) only.
- **Read-only users**: oncall and stakeholders via UI/API.
- All mutating actions are CLI-only.

---

## Environment assumptions

- Hosts are persistent and reachable via SSH.
- `/etc/puppet_role` defines default role.
- `/etc/puppet/ronin_settings` may exist; if present, it overrides defaults.
- Puppet is applied at boot; correctness is validated under real workload.

---

## User interaction model

- **CLI-first** for all write operations.
- **Read-only Web UI** for visibility and shared context.
- **API-backed** for future automation.

---

## Approval and authorization model

FleetRoll uses a **single-approver** model.

Each mutating action records:
- `actor`
- `action`
- `timestamp`
- `parameters`
- `approval_mode = self`

Some actions require explicit confirmation (`--confirm`).

---

## Rollout model (explicit, operator-driven)

FleetRoll supports **explicit staged rollouts** with manual progression. Operators decide when to advance stages. FleetRoll enforces health gating but does not auto-advance.

### Rollout plan contents

A rollout plan specifies:
- Target population (query)
- Canary size
- Default batch percentage (suggestion only)
- Change to apply (for example `PUPPET_BRANCH=feature_xyz`)
- Health gate configuration

The plan does **not** require predefining all rollout stages.

---

## Health gating

Health gates evaluate **observed workload and outcomes**.

### Workload gates (examples)
- `min_jobs_per_host`
- `min_jobs_total`
- `min_hosts_with_jobs_fraction`

### Outcome gates (examples)
- `tc_success_rate_drop`
- `tc_online_drop_pct`
- `sshable_min_pct`

Gates are configurable per rollout. FleetRoll records the **effective gate configuration** and the **gate state at each advance decision** in the audit log.

---

## Stage lifecycle

Each stage follows this lifecycle:
- `Applied`
- `Waiting for assessment`
- `Paused` (gates failed)
- `Advanced`
- `Rolled back` (if applicable)

FleetRoll remains in `Waiting for assessment` until the operator advances.

---

## Advancing stages (normal and forced)

### Normal advance

```bash
fleetroll rollout advance <rollout-id> --batch-size <N>
```

- Allowed only when gates are satisfied.
- FleetRoll applies the next batch and returns to `Waiting for assessment`.

### Forced advance

```bash
fleetroll rollout advance <rollout-id> --batch-size <N> --force --reason "text"
```

- Allowed regardless of gate state.
- `--reason` is required.
- Audit log records:
  - `force = true`
  - `force_reason`
  - Gate configuration and gate evaluation state at time of advance

Forced advances do not disable ongoing monitoring.

---

## Policy: existing overrides block rollout start

A rollout will not start if any target host already has `/etc/puppet/ronin_settings` present.

Remediation must be performed outside FleetRoll. Hosts may be skipped explicitly.

---

## Converge and reboot (manual, out-of-band)

- `fleetroll converge host <host>` runs the local converge command (for example `run-puppet.sh`), no reboot.
- `fleetroll reboot host <host>` performs a managed reboot.

These commands are:
- CLI-only
- Explicit
- Never part of rollout execution

---

## Rollback and finalize

- **Rollback**: abort a rollout and return to role-only target; override removal and convergence are manual and verified.
- **Finalize**: after a successful rollout and merge to master, RelOps may invoke finalize to remove overrides and return the fleet to a clean baseline. Finalize is a **one-shot** operation by default: operators may remove overrides from the entire target population in a single action. Finalize is irreversible within FleetRoll; to reintroduce an override, operators must create a new rollout.

---

## Finalize: one-shot behavior (operator-driven, irreversible)

FleetRoll supports an explicit **one-shot finalize** workflow designed for teams that prefer to remove overrides across the entire fleet at once. This workflow is operator-driven and irreversible within FleetRoll.

Key points:
- Finalize requires the rollout to be in `SUCCEEDED` state and is only allowed after the operator confirms the change has been merged to master (operator responsibility).
- By default, `fleetroll rollout finalize <id> --all --confirm` will remove `/etc/puppet/ronin_settings` from all target hosts in the rollout target population in a single operation.
- FleetRoll takes pre-removal snapshots for forensic purposes and records them in the audit log, but does not offer an automated restore operation.
- After removal, FleetRoll enters a post-finalize observation phase and evaluates workload/outcome gates; if severe regressions are detected, FleetRoll will pause and notify RelOps, but will not re-apply overrides automatically.
- If operators need to reintroduce the override after finalize, they must create a **new rollout** that applies the override across desired hosts.

---

## Host inventory: seeds, discovery, and probes

FleetRoll builds an observed inventory via SSH discovery starting from configured seeds. It treats seeds as authoritative starting points and materializes a view of reality; FleetRoll does not act as a CMDB.

### Seeds

- Seeds are managed via CLI:
  - `fleetroll inventory seed add <hostname>`
  - `fleetroll inventory seed remove <hostname>`
  - `fleetroll inventory seed list`
- For initial bootstrap there may be a static seed file (for example `/etc/fleetroll/seeds.txt`) which can be synced into FleetRoll via `fleetroll inventory seed sync --from-file`.

**Important:** seeds create discovered host records but do **not** implicitly assign hosts to populations or expected roles. Operators must **manually assign a fleet/population and expected role** to discovered hosts before those hosts are eligible for rollouts.

### Discovery & probes

For every candidate host FleetRoll performs best-effort probes:
- DNS/IP resolution
- SSH connect probe (`sshable`)
- **Sudo/root probe** (`sudoable`) via a non-interactive check (for example `sudo -n true`)
- Read `/etc/puppet_role`
- Detect `/etc/puppet/ronin_settings` and capture `override_contents` (if present)
- Correlate Taskcluster identity and last-seen metrics

All probe results are recorded per-host. Failures are visible and do not remove hosts from inventory.

### Host states

Hosts carry lifecycle states and metadata:
- `discovered_at`, `last_seen`
- `sshable`, `sudoable`
- `role` (observed), `expected_role`
- `override_present`, `override_snapshot_id`
- `disabled` flag with `disabled_reason`, `disabled_by`, `disabled_at`

Disabled hosts remain in inventory but are excluded from targeting by default.

### Drift detection and queries

FleetRoll computes drift as differences between observed state and baseline expectations (for example presence of `ronin_settings`). Example queries:
- `fleetroll host list --drift-type override` — hosts with override drift
- `fleetroll host list --drift-type tc-missing` — hosts missing from Taskcluster
- `fleetroll host list --drift-type any` — any detected drift

### Refresh schedule

- Fast refresh: minutes — update reachability and Taskcluster liveness
- Full refresh: hourly — SSH probes, sudo checks, file reads
- On-demand refresh via CLI: `fleetroll host refresh <host>`

### Operational notes

- FleetRoll does not automatically delete hosts; administrators may disable hosts via CLI.
- Discovery is idempotent and tolerant of transient failures.

---

## Role assignment and populations (manual assignment)

FleetRoll distinguishes between the observed role (read from `/etc/puppet_role`) and the expected role (what Rollouts target). Seeds **must** be assigned to a fleet/population manually before being considered for rollouts.

### Recommended onboarding workflow

1. Add seeds (CLI or file)
2. Run discovery/refresh
3. Inspect discovered hosts:
   `fleetroll host list --filter "expected_role:null"`
4. Create population:
   `fleetroll population create gecko-t-linux-talos --expected-role gecko_t_linux_talos --description "talos Linux hosts"`
5. Assign hosts to population manually:
   `fleetroll host assign-population --filter "role:gecko_t_linux_talos AND expected_role:null" --population gecko-t-linux-talos --confirm`
6. Verify:
   `fleetroll host list --filter "expected_role:gecko_t_linux_talos"`

### CLI primitives

- `fleetroll population create <name> --expected-role <role> --target-query "<query>"`
- `fleetroll population list`
- `fleetroll host assign-population <host> --population <name>`
- `fleetroll host assign-population --filter "<filter>" --population <name> --confirm`
- `fleetroll host set-expected-role <host> --role <role> --confirm`
- `fleetroll host clear-expected-role <host> --confirm`

All assignments are auditable and require RelOps privileges.

---

## Drift types

FleetRoll classifies drift into explicit types. Drift is derived from observed host attributes and contextual expectations.

Supported drift types:

- `override` — override file present when not expected (for example no active rollout, or branch mismatch)
- `role` — observed `/etc/puppet_role` differs from expected role
- `unreachable` — host unreachable via SSH/liveness probes beyond threshold
- `tc-missing` — host expected in Taskcluster but not present within the expected window
- `tc-mismatch` — host present in Taskcluster but with mismatched metadata (for example workerType)
- `disabled-active` — host marked disabled but still reachable or executing jobs
- `any` — any detected drift

Query via `fleetroll host list --drift-type <type>`.

---

## Audit logging

Every mutating action records:
- Actor
- Timestamp
- Affected hosts
- Gate configuration
- Gate evaluation state
- Whether the action was forced
- Operator-supplied reason (if any)

Audit logs are immutable.

---

## Architecture (high level)

- Controller (API, scheduler, UI)
- SSH connector
- Taskcluster connector
- Postgres datastore
- Audit and snapshot storage

---

## Runbook example: onboarding a 100-host pool (manual assignment)

### Scenario

- Initial seed file with 100 hostnames is available at `/etc/fleetroll/seeds.txt`.

### Steps

1. **Bootstrap seeds**
```bash
fleetroll inventory seed sync --from-file /etc/fleetroll/seeds.txt
```

2. **Run discovery**
```bash
fleetroll inventory refresh --seed all
```

3. **Inspect unassigned hosts**
```bash
fleetroll host list --filter "expected_role:null"
```

4. **Create population**
```bash
fleetroll population create gecko-t-linux-talos --expected-role gecko_t_linux_talos --description "talos Linux hosts"
```

5. **Assign discovered hosts to population**
```bash
fleetroll host assign-population --filter "role:gecko_t_linux_talos AND expected_role:null" --population gecko-t-linux-talos --confirm
```

6. **Verify assignment**
```bash
fleetroll host list --filter "expected_role:gecko_t_linux_talos"
```

Hosts are now eligible for rollouts targeting `expected_role:gecko_t_linux_talos`.

---

## Summary

- Seeds bootstrap discovery but do not auto-claim roles.
- Operators must manually assign seeds to populations / expected roles before hosts are targeted by rollouts.
- Discovery includes a sudo/root probe to validate operator access.
- Hosts can be disabled with reasons and remain in inventory for auditing and historical context.
