# Monitor JSON worker evidence (version 3)

`fleetroll host monitor HOST_OR_FILE --once --json` emits a host-keyed object.
Each value preserves the latest raw host audit, including `observed.role` as
the Puppet role, and adds `worker_evidence_schema_version: 3` plus four objects.
A host without an audit now has an object with `host` and explicit missing
evidence instead of `null`. Existing audit fields are unchanged when present.
All timestamps are the original source timestamps; generating a report does not
refresh observations or query Taskcluster.

Without `--once`, JSON emits the same initial host-keyed object, followed by
one complete per-host object per update (including its `host` field), as before.
The objects now have the same evidence contract in snapshots and updates.
Updates occur on host audit changes, TC-only changes (including failed lookup attempts), override metadata changes,
and transitions to stale evidence. Polling runs every half second and can coalesce
intermediate observations; this is a latest-state feed, not an event audit trail.
Snapshot and follow output retain their existing different outer framing.

Version 3 adds `historical_worker_identity` for unreachable-host investigation,
with durable timestamps, contradiction reasons, and an explicit historical TC
lookup path. See the [historical identity contract](historical-worker-identity.md)
for its full shape, retention, override rules, examples, and consumer migration.
Historical evidence never resolves or refreshes current-evidence fields below.

For schema or evidence-semantic changes (including timestamps, provenance, and
failure behavior), check the [integration inventory](tool-integrations.md) for
affected consumers and migration requirements.

## Configured worker type

`configured_worker_type` describes intent recorded by the latest host audit:

- `provisioner`, `worker_type`: the role default with a literal active
  `WORKER_TYPE_OVERRIDE` applied. Resolution reuses gather-tc's role mapping and
  override precedence, independently of the host-observed identity.
- `status`: `resolved` or `unresolved`; `source`: `override`, `role_default`, or
  `unknown`. `reason` is null on resolution, otherwise `host_audit_missing`,
  `host_audit_failed`, `override_metadata_unavailable`, or `unmapped_role`.
  Missing/unparseable stored override metadata prevents role fallback.
- `host_audit_status`: `ok`, `failed`, or `missing`; `observation_ts` and
  `freshness` qualify the source observation.
- `override_sha256`: the deployed override content SHA, if observed.
- `application_status`: `applied` requires matching deployed/applied override
  SHAs and a successful Puppet run; `pending` means the SHAs differ; `failed`
  means the recorded Puppet run failed; otherwise `unknown`. For no override,
  a successful run with a null/empty applied SHA counts as applied. This checks
  override convergence, not complete host health or current branch revision.

An unresolved pool has null provisioner and worker type. Failed audits do not
fall back to an older successful audit. Cached metadata only supports the literal
override assignments supported by gather-tc; shell expressions are never executed.
Unmapped roles (including Windows roles without a static mapping) remain unresolved
unless an override supplies the pool. Host-observed and TC evidence remain separate.

## Host-observed identity

`observed_worker_identity` contains `provisioner`, `worker_type`, `worker_group`,
and `worker_id` from the host configuration, with `observation_ts`, `freshness`,
and `host_audit_status`. This does not establish that a worker process is running.

`collection_status` preserves the collector's status verbatim (for example `ok`,
`missing`, or `invalid`); older observations use `not_collected`. `status` is
`resolved` only when the latest audit succeeded, collection status is `ok`, and
all four identity fields are present. No role-derived fallback fills this identity.
Partial evidence remains visible with `status: unresolved`.
`configured_pool_match` is `match`, `mismatch`, or `unknown`.

## Taskcluster evidence

`taskcluster.identity` contains the provisioner, worker type, group, and worker
ID queried for the stored successful worker observation. `observation_ts` is when
that observation was collected; `last_date_active` is the independent TC activity
timestamp. `record` preserves the complete stored TC record, including task and
quarantine fields, or is null.

- `record_status`: `stored_success` or `missing`.
- `latest_lookup_status`: the result of the latest persisted per-host attempt,
  also available as `latest_lookup.status`. Legacy data with no attempt uses
  `unknown`; a retained successful observation never synthesizes an attempt.
- `latest_lookup`: the independent latest attempt described below. Failed
  attempts leave all successful-observation fields above unchanged. A missing
  observation is not evidence of inactivity. A fresh successful observation is
  not proof that the latest attempt succeeded.
- `configured_pool_match`: compares provisioner and worker type to intent.
- `observed_identity_match`: compares all four identity fields to valid
  host-collected identity. Different groups/IDs count as mismatches even within
  the same pool. Missing or unresolved inputs yield `unknown`.

Each evidence object's `freshness` is `fresh` for an observation age under one
hour, `stale` at or beyond one hour, or `unknown` for missing, invalid, naive, or
future timestamps. This describes collection age, not recent task activity.
Pool matching is independent of freshness: a stale record can still match, and
a fresh record can describe another pool. Old/mismatched records retain their
original identity and timestamps.

### Latest lookup attempt

`gather tc` stores a result for each requested host after pool queries complete,
including unresolved hosts and partial batch failures. Successful observations
and attempts commit together. Storage retains **one latest attempt per full
hostname** in `tc_lookup_attempts`, independently of the existing worker history.
An older completed timestamp cannot replace a newer one. Equal timestamps use
the last write; timestamps use the existing second-resolution gather clock.
This is latest-state evidence, not attempt history or an in-progress indicator.
An interrupted gather or global setup failure (for example unreadable credentials
before collection starts) does not produce a completed attempt.

The attempt includes:

- `ts`: batch completion timestamp; `started_ts`: start of the pool-query phase.
- `identity`: queried `provisioner`, `worker_type`, `worker_group`, and `worker_id`.
  Host-configured identities have all four fields. Legacy lookup knows the pool
  and short worker ID but leaves group null; it never invents a group. Unresolved
  identities have all four fields null and do not reuse an older pool.
- `resolution_source`: `host_configuration`, `historical_worker_identity`, `legacy_override`, `legacy_role`,
  or `unknown`. This describes the query's resolution method, not Puppet intent.
  A failed host audit blocks legacy fallback even if older role data exists.
- `status` and a concise `error` from fixed messages. Raw API exceptions,
  credential values, and URLs are not persisted in attempt errors.
- `successful_observation_ts`: the worker observation timestamp written by this
  attempt on success, otherwise null. It links a success to `taskcluster.record`.
- `freshness`: age of the completion timestamp, independent of the successful
  observation's freshness and of `last_date_active`.
- `configured_pool_match` and `observed_identity_match`: comparisons using the
  attempted identity. These use the same rules as successful-evidence matching;
  incomplete legacy identities yield `unknown` for full identity matching.

| Status | Meaning |
| --- | --- |
| `success` | A complete worker observation was written. |
| `identity_unresolved` | No usable query identity; no old-pool fallback. |
| `pool_query_failed` | The worker-list request failed; recorded for every affected host. |
| `worker_missing` | No worker matched the queried pool/group/ID. |
| `worker_ambiguous` | The full identity or legacy short ID matched multiple workers. |
| `task_lookup_failed` | Worker matched, but its latest task run could not be collected. |
| `unknown` | No persisted attempt exists, including pre-migration observations. |

Windows pool discovery failures leave affected unresolved identities explicit;
they do not block successful collection for other pools. Per-host attempt success
does not imply the entire batch succeeded; gather's exit-status behavior remains
the batch outcome. A status lookup failure affects only the relevant workers.

## Example and consumer migration

An abbreviated applied override example:

```json
{
  "ms017.example.test": {
    "host": "ms017.example.test",
    "ts": "2026-11-05T04:00:00Z",
    "ok": true,
    "observed": {"role": "gecko_t_linux_2404_talos"},
    "worker_evidence_schema_version": 3,
    "configured_worker_type": {
      "worker_type": "gecko-t-linux-talos-2404-tc-v115",
      "source": "override",
      "status": "resolved",
      "application_status": "applied"
    },
    "observed_worker_identity": {
      "worker_type": "gecko-t-linux-talos-2404-tc-v115",
      "collection_status": "ok",
      "status": "resolved",
      "configured_pool_match": "match"
    },
    "taskcluster": {
      "identity": {"worker_type": "gecko-t-linux-talos-2404-tc-v115"},
      "observation_ts": "2026-11-05T04:00:00Z",
      "last_date_active": "2026-11-05T03:59:00Z",
      "record_status": "stored_success",
      "latest_lookup_status": "success",
      "latest_lookup": {
        "ts": "2026-11-05T04:00:05Z",
        "started_ts": "2026-11-05T04:00:00Z",
        "status": "success",
        "resolution_source": "host_configuration",
        "identity": {"worker_type": "gecko-t-linux-talos-2404-tc-v115"},
        "successful_observation_ts": "2026-11-05T04:00:00Z",
        "freshness": "fresh"
      },
      "freshness": "fresh",
      "configured_pool_match": "match",
      "observed_identity_match": "match"
    }
  }
}
```

If the override is deployed but not applied and the worker still uses the base
pool, configured intent stays `...-tc-v115`, application becomes `pending`, and
the host identity's configured-pool match becomes `mismatch`. A TC observation
from that base pool also has `configured_pool_match: mismatch`, even if it is
fresh. If host identity collection fails, its status becomes `unresolved` and
TC's observed-identity match becomes `unknown`; the stored TC evidence stays visible.

For **success then failure**, suppose the example's next query fails at
04:10:00. `latest_lookup.status` becomes `pool_query_failed`, its `ts` becomes
04:10:00, and `successful_observation_ts` becomes null. The successful
`taskcluster.observation_ts` remains 04:00:00 and activity remains 03:59:00.
The failure's freshness cannot refresh those older timestamps.

For **pool change then failure**, suppose the host config changes from the base
pool to `...-tc-v115`, but that pool query fails. The attempt's identity names
`...-tc-v115` and its status is `pool_query_failed`. The retained successful
`taskcluster.identity` still names the base pool. Those are separate facts;
the base pool's activity says nothing about inactivity in the new pool.

Version 2 adds `latest_lookup` and allows `latest_lookup_status` to report known
results instead of always `unknown` as in version 1. Existing successful-record
fields and timestamps retain their meanings. Database initialization adds the
attempt table idempotently without backfilling invented attempts; run a new gather
to obtain known results. Consumers pinned to version 1 must accept version 2 and
the enumerated result statuses before using lookup evidence.

Automation should check the version, accept additional fields, and handle a
missing-audit object instead of assuming every value is either a successful audit
or null. Stop deriving worker pools from `observed.role`. Use configured intent
and the full host identity, then require matching TC evidence and suitable source
freshness. Evaluate `last_date_active` separately and handle null activity as
unknown. Do not infer inactivity in the intended pool from missing, stale, failed,
or mismatched evidence. These fields supply evidence, not reset authorization;
Medic's reset policy is outside this contract.

When requiring the latest lookup to have succeeded, check its `status`, freshness,
and matching identity as well as the retained observation, and require
`latest_lookup.successful_observation_ts == taskcluster.observation_ts`. Check
gather's exit status separately when requiring whole-batch success.
