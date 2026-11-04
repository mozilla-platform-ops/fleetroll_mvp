# Compact host configuration history

Follow-up to the rollout report's conservative attribution model. Preserve
enough configuration evidence for normal reports after the rolling ten full
observations have been pruned. Do not increase the shared DB retention limit or
create a Taskcluster task-history store.

## Policy

- Retain compact configuration history for 30 days, using UTC wall-clock age.
- Preserve the existing latest-ten full-observation policy and last successful
  observation exception. Other SQLite table policies remain unchanged.
- A stable, continuously observed configuration extends its last-observed
  endpoint instead of appending another full snapshot every polling cycle.
- Changes to role, applied repository/revision, configured/applied override or
  vault hashes, dirty/success state, or effective worker identity are material.
  Uptime, polling time, durations, and ordinary repeated Puppet runs are not
  configuration changes. Preserve initial activation separately from the latest
  successful run timestamp.

## Evidence contract

Preserve first/last observation bounds, successful application bounds where
established, configuration identity, verification state, observation continuity
information, and reasons for uncertainty. Keep only allowlisted metadata and
hashes: no vault/override contents, raw command output, or credentials.

An override write/removal or vault write records changed intent and breaks
continuity; it never establishes Puppet convergence. Persist observation-derived
evidence atomically with observation insertion before pruning. Integrate relevant
operation events without changing the success/failure audit semantics. Failed
operations that may have partially changed a host must not imply unchanged state.

Failed observations, unverified/mismatching Puppet state, worker identity changes,
conflicting evidence, and excessive observation gaps create explicit boundaries.
Do not merge matching configurations across a known unknown interval, even when
their state hashes are identical on either side. Never extend coverage beyond a
successful observation or infer unseen manual actions. Changes that are made and
reverted entirely between observations remain fundamentally undetectable.

The compact representation must retain the continuity bounds needed to honor the
rollout report's configurable maximum observation gap. If a stricter query cannot
be supported from compact evidence, classify the affected span as unknown rather
than silently relaxing the requested bound. The schema may use bounded evidence
anchors, interval continuity metadata, or an equivalent representation; document
the tradeoff and test equivalence to raw history for supported queries.

Retain sufficient override descriptors and master/revision provenance for the
reader to work after referenced cache files or GitHub snapshots expire. Do not
relabel an old unknown revision as master merely because its override is absent.
Provenance must identify the source and observation/reference timestamps.

## Storage lifecycle

Use an additive, versioned, idempotent SQLite migration. Repeated observations
and replayed audit events are idempotent. Concurrent collection must not lose a
transition. Late or out-of-order contradictory evidence must conservatively
invalidate/split the affected coverage; it must not extend an interval through
uncertainty. Future source timestamps must not drive retention pruning.

Expire intervals whose last evidence predates the 30-day cutoff. Retain a bounded
anchor for an interval crossing that cutoff, and clip exposed report coverage to
the retained horizon. Preserve gaps at the boundary. Integrate cleanup with
existing maintenance and remove unreferenced configuration descriptors. Report
logical retention separately from physical database size; normal SQLite page
reuse and vacuum behavior still apply.

Bootstrap from existing retained observations and audit evidence where possible.
Missing historical state stays missing; migration cannot recover previously
pruned observations. Saved observation files remain a supported supplement.

## Report integration and validation

Read compact history in the rollout report, deduplicating overlap with full
observations and saved snapshots. Surface history coverage, retention cutoff,
unknown intervals, and provenance in human and JSON output. Collection owns
persistence; report execution remains read-only.

Tests must cover stable scans, ordinary Puppet reruns, manual changes observed
later, deployment/removal before convergence, failed collection, mismatches,
role/revision/identity changes, gaps, stricter query bounds, duplicate/conflicting
timestamps, late events, retention clipping, cache/ref expiry, migration/replay,
concurrent writers, and compact-versus-raw attribution equivalence.

Measure sustained stable polling and state-change/outage workloads for roughly
930 hosts. Report rows and bytes, indexes, write/maintenance cost, and query
latency. Stable polling should update bounded rows rather than grow with scan
count; storage should follow real transitions and uncertainty segments. The
earlier tens-of-MB estimate is a hypothesis, not an acceptance budget or promise.
