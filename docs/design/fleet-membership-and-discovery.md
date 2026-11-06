# Fleet membership, discovery, and coverage

Status: proposal. This document describes planned behavior, not an implemented
command or API contract. Implementation tasks and completion status live in Beads.

## The problem

Fleetroll has a fleet discovery and membership problem: today, the host list
supplied to a command defines its scope. Fresh observations can make that scope
look trustworthy even when the list is incomplete.

For example, an operator moving hosts between Ubuntu 18.04 and 24.04 pools might
monitor `configs/host-lists/linux/1804.list` with an override branch filter, then
run `data freshness` against the same file. Both commands can operate correctly
while missing relevant hosts:

- Hosts moved into 18.04 but absent from the file are invisible to both checks.
- Hosts moved out of 18.04 can remain listed; the filename does not enforce their
  current OS or pool.
- The branch filter further hides hosts whose recorded override does not match,
  including hosts with stale observations.

`data freshness` checks observation age for selected hosts. It does not validate
inventory completeness or pool membership. Its default policy also permits 20%
of selected hosts to lack fresh data. `--all` covers hosts already known to the
local database, which does not establish a complete fleet inventory.

Linux `all.list` currently derives from the manually maintained
`all_moonshots.list`. Regenerating the aggregate cannot discover omissions from
that source. The README explicitly lists fleet management, integration with
other data sources, and ingestion of hosts among the MVP's missing features.

## What defines the fleet?

The fleet should be the hosts we intend Fleetroll to manage, backed by an
authoritative inventory. The choice of authoritative source still needs to be
made; this proposal does not declare an existing file or service complete.

"Accepts our SSH key" is a useful connectivity check, but a poor membership rule:
it excludes broken or rebuilding hosts precisely when we need to track them,
and may include unrelated machines.

| Source | Useful for | Limitation |
| --- | --- | --- |
| Managed inventory | Expected fleet membership | Must be maintained and reconciled |
| Taskcluster pools | Registered workers and pool membership | Can contain old registrations; rebuilding hosts may be absent |
| Hostname ranges | Finding candidate hosts and gaps | Numbers do not establish existence, ownership, or current OS |
| IP ranges | Finding reachable systems | Addresses can change; network boundaries do not establish ownership |
| SSH observations | Actual OS, role, and configuration | Unreachable hosts still belong to the fleet |

Hostname ranges are useful as discovery seeds, especially for a bounded Moonshot
estate. They should not determine OS pool membership. A hostname should retain
its identity while its OS and pool change. The identity model must also account
for hostname reuse or hardware replacement rather than silently merging their
histories.

## Separate membership, intent, and evidence

Fleetroll should distinguish three things:

1. **Expected membership:** which physical hosts belong to our fleet, including
   hosts that are currently unreachable or rebuilding.
2. **Intended assignment:** which OS and pool each host should join.
3. **Observed assignment:** what the latest host and Taskcluster observations
   report, with timestamps and source provenance.

Moving a host from 18.04 to 24.04 changes its assignment without removing it from
monitoring. Fleetroll can explicitly report migration pending, assignment
mismatch, unreachable, or unknown. Old evidence must remain visibly old, and
disagreement between sources must remain visible rather than being resolved by
silently selecting the most convenient source.

Temporary disappearance from SSH or Taskcluster must not remove an expected
host. Retirement should be an explicit lifecycle transition with retained
history. Discovery of a candidate should not automatically authorize writes to
that host.

## Coverage and freshness are separate checks

Freshness answers: how recent is our evidence for the selected hosts?
Coverage answers: which expected hosts and relevant candidates did we account
for, and how do we know the expected inventory is current?

Complete accounting does not mean that every host is reachable, has fresh
observations, or is healthy. A report can account for every expected host while
explicitly identifying several as unreachable or unobserved. Report inventory
reconciliation, observation availability and freshness, reachability, and health
separately. An unreachable expected host stays in the denominator; an explicit
unknown result accounts for its presence without satisfying an evidence or
health requirement. Coverage assertions must name which of these requirements
they enforce rather than hiding them behind one percentage.

Reports should make their scope visible: inventory source and revision or sync
time, expected and selected counts, filters, excluded or unobserved hosts, and
unresolved discovery differences. An unavailable or incomplete inventory source
must yield unknown coverage, not a claim of complete coverage. A successful
partial sync must not silently retire hosts missing from that response.

An explicit host-list report can remain useful and valid, but its conclusions
must remain scoped to that list. A healthy filtered result cannot establish
fleet-wide health. Machine-readable output should expose coverage separately
from observation freshness, with an explicit assertion mode for consumers that
require complete coverage. Intent-based and observation-based selectors need
distinct semantics so a migrating host does not silently disappear from both
views.

## Incremental delivery

### 1. Detect gaps using existing lists

Define an explicit expected hardware population independent of OS and reconcile
the current OS lists against it. Report missing hosts, unexpected hosts, and
conflicting OS assignments. Preserve legitimate overlap in canary and batch
lists; only mutually exclusive assignment lists should be treated as conflicts.
Include source provenance and state the limits of the selected baseline.

Compare listed OS/pool assignments against recorded host and Taskcluster
observations as well. A host present in exactly one list can still be assigned
to the wrong list after a migration. Preserve host-configured worker identity
and Taskcluster registration as separate evidence; neither alone establishes a
running worker. Show evidence timestamps and distinguish a fresh mismatch from
stale, missing, or contradictory evidence. This report should use recorded data
and explain when a Fleetroll collection refresh is needed.

This milestone provides value before introducing a full inventory service. It
does not prove that a manually supplied baseline is complete.

### 2a. Decide authority, identity, and lifecycle rules

Choose the source of authority and document ownership and lifecycle rules.
Decide where intended assignment is maintained, how identity survives reimages
and replacements, and how inventory snapshot completeness is established.
Produce a concrete schema and import contract before implementing persistence.
This is a bounded design task; it does not implement storage or source adapters.

### 2b. Persist durable fleet membership

Implement the authority and lifecycle contract from the preceding design task.
Introduce records for membership, intended assignment, identity, and provenance
separate from host observations. Import existing inventories without treating
successful SSH access as enrollment or failed access as retirement.

### 3. Reconcile discovery sources

Compare expected membership with Taskcluster registrations and configured,
bounded hostname seeds. Surface unknown candidates, missing expected hosts,
duplicates, conflicting assignments, source age, and partial-source failures.
Define identity normalization and stale worker-registration handling. Broad IP
scanning is not required for the first implementation.

### 4a. Select against inventory

Build inventory-backed selectors and generated OS views. Make intended and
observed assignment explicit and preserve visibility through migrations.
Unknown observed assignment must be surfaced in the scope summary instead of
silently disappearing from an OS/pool view. Define unknown handling and selection
semantics in CLI and JSON. Keep explicit host-list workflows compatible and
visibly scoped.

### 4b. Report coverage separately from freshness and health

Expose inventory reconciliation and evidence coverage alongside freshness in
CLI and JSON reports, with a path for TUI/web consumers. Define denominators,
unknown states, source completeness requirements, and explicit assertions.
Assertions must distinguish accounting for all expected hosts from obtaining
fresh observations or confirming health for all of them. Keep selection scope
visible even after display filters hide rows. Coordinate with the existing
health/freshness design work rather than redesigning health within this task.

### 5. Migrate operational workflows

Replace manually maintained OS lists with generated views or saved selectors
where practical. Document the migration workflow and the role of the hardware
baseline. Roll out the previously validated behavior to operational workflows;
this milestone is adoption and documentation, not the first integration test.

## Validate migrations throughout delivery

Start with a repeatable scenario in the first milestone: a known host is listed
under 18.04, its intended assignment changes to 24.04, it rebuilds and becomes
temporarily unreachable, and it returns with a changed worker identity while an
old Taskcluster registration may remain. Membership stays constant throughout.
Unknown or stale evidence remains visible until observations catch up.

Each implementation task must extend this scenario at its boundary: list versus
observation reconciliation, persistence/import, discovery reconciliation,
selection, and reporting/assertions. Validate retirement and hostname reuse as
separate transitions. Use fixtures and meaningful integration checks without
requiring production host changes. The final adoption task verifies the
documented workflow against behavior already exercised during implementation.

## Decisions to resolve

- Which source owns expected hardware membership, and who maintains it?
- Where is intended OS/pool assignment recorded during migrations?
- What identifies a machine across reimages, hostname changes, and replacements?
- Which sources and revisions are required before coverage can be asserted?
- How should local inventory snapshots work with the planned shared server?

This is a substantial feature spanning inventory, collection, selection, and
reporting. The first reconciliation milestone can ship independently; a central
server and broad network discovery are not prerequisites for getting that value.

## Related work

- `mvp-d0kx`: health, freshness, and automation safety semantics. Coordinate the
  coverage contract with this spike rather than duplicating its health redesign.
- `mvp-si5u`: shared scanning and the client/server architecture. Membership and
  provenance should fit local and shared storage without requiring server rollout
  for the first milestone.
