# Independent Puppet and task-run attribution proof

Design and validation for `mvp-nvpj`. The supported operator/JSON behavior is in
[rollout report](../rollout-report.md) and
[compact configuration history](../reference/configuration-history.md).

## Separate the two requirements

A successful host audit can contain complete Puppet/file evidence while worker
identity collection is unavailable. Treating those as one proof discarded useful
configuration history. A failed overall host collection remains absence of new
configuration evidence; it does not supply trusted partial fields under the
legacy collector contract. Richer failed/partial collection statuses belong to
`mvp-257d`.

`PuppetConfiguration` holds role, repository/branch, applied revision, override,
and vault identity independently. `Configuration` adds a full worker identity.
Both require the same Puppet/file checks: successful clean application, matching
role, deployed/applied file evidence, supported master/override provenance, and
valid application/observation ordering. Unknown vault presence or explicit vault
absence contradicted by an applied vault is unverified. Legacy metadata/null
coherence and invocation-ID limitations are unchanged.

Normal configuration intervals and current status still require host identity.
The new task path requires a fresh successful host identity without legacy
mapping, plus Queue status for the exact task and unique integer run ID. Queue
provisioner, worker type, group, and worker ID must all match. The independently
checked run can complete identity proof over a matching Puppet interval.
Unavailable historical identity can participate; known identity conflicts,
ambiguous/invalid identity, malformed `ok` payloads, and contradictory identity
data remain barriers. A legacy inferred identity is not a contradiction to a
Queue identity, but it cannot supply the fresh host association for this new
completion path.

The fresh host association is specifically **configured** identity, not a new
claim about the running process. Per-host `query_identity_evidence` and combined
task `host_association` preserve that source and timestamp. Queue supplies exact
run ownership. This keeps runtime-identity policy under the separate `mvp-dx2b`
design rather than silently treating readable configuration as runtime proof.

The interval builder accepts a task identity context only after the caller has
established the query association. The public report uses that context only when
the returned job carries matching Queue evidence. Resulting identity coverage is
clipped to that run's start/end; it does not extend across the entire Puppet-only
interval. Existing full-proof attribution is preferred. Current configuration
and query freshness never derive from a historical job.

## Retention and compatibility

SQLite schema version 1 remains. New internal proof version 2 pins Puppet proof
and optional host identity separately; existing full version-1 descriptors remain
readable. Pinned proof retains original establishment/reference provenance and
can survive override-cache loss. State keys and span compaction continue to
include identity collection state, and all existing write/conflict/gap barriers
remain effective.

Some older unverified descriptors retained complete Puppet fields but no proof
because their original refusal concerned identity. Those endpoints can be
strictly rechecked using available hash-verified override contents or master
evidence. Revalidation requires application time within the retained write-history
window. It never overrides invalidation, prior Puppet/file failures, or pending
write-convergence reasons. Missing artifacts and unavailable reference proof
remain unknown. This rechecks evidence; it does not manufacture a pinned proof or
persist a descriptor during report reads.

Revalidation preserves actual first/last observation endpoints and maximum gaps.
It cannot synthesize an interior historical cutoff or restore discarded polling
anchors. Source `revalidated_compact` identifies this path. Saved observation
files, audit records, and raw snapshots cannot inject trusted underscore-prefixed
proof/continuity fields; those fields come only from the compact reader.

Report JSON version 1 gains per-job `identity_evidence` and `attribution_evidence`,
plus proof-version/identity-reason details on compact evidence. Qualified task
intervals identify `identity_source: queue.status`; current host status remains
separate. Cohort summaries, comparisons, and gap reporting use the qualified run
windows, preserving excluded transitions and original task outcomes. Verbose text
identifies the combined proof and its source.

## November 5 ms-017 validation

Read-only validation on November 16 used the retained local timeline and the
original session's captured task summary. No hosts were refreshed or changed.

- The retained revision sequence includes `af554ac` through November 5
  03:01:19 UTC and `c2d444c` from 03:04:24 onward. Revisions remain separate;
  unknown intervals around transitions are not bridged.
- The captured task `s9sYXzYHTmSeHJLAY_VadA`, run 0, started at
  04:44:06.847 and resolved at 05:04:11.596 UTC as `exception` /
  `claim-expired`, on `mdc1/t-linux64-ms-017` in
  `releng-hardware/gecko-t-linux-talos-2404-tc-v115`.
- Complete Puppet endpoints at 04:40:13, 04:45:06, 04:49:44, and 05:18:23,
  interspersed with failed gathers, retain revision `c2d444c` but lack host
  worker identity. Strict revalidation supports that revision across the
  captured run window, with source `revalidated_compact`.
- A fresh Queue lookup on November 16 returned 404 for the expired original
  task. The captured summary is archived validation evidence, not a fresh live
  task attribution or a new healthy result.
- The historical report horizon 09:31:40 falls inside a retained span from
  07:43:19 to 18:47:01. It still reports
  `historical_detail_unavailable`, with the actual 07:43:19 endpoint; it does not
  invent an observation at the requested horizon. The separately read current
  host observation was dated November 16 20:50:02 UTC and was not substituted
  for the historical evidence.

Regression fixtures reproduce the exact interrupted-task timestamps with fake
task IDs and revisions. They exercise old/new revisions independently and refuse
transition-crossing tasks, unverified Queue identities, identity conflicts,
writes, unknown files, excessive gaps, unsupported compact horizons, artifact
loss, and injected proofs. Public report JSON retains exception exit semantics.
