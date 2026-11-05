# Historical worker identity (mvp-7xfx)

Monitor JSON version 3 supplies historical evidence for investigating an
SSH-unreachable worker. It does not authorize resetting a host. Medic owns
inactivity thresholds, baseline age limits, and recovery policy.

## Durable state

SQLite `worker_identity_history` stores one bounded summary per hostname:
the latest complete successful identity audit, latest successful audit,
latest override write and observed override-change boundary, and outstanding
missing-configuration or conflicting-observation markers. Audit fields are
whitelisted; credentials and configuration contents are excluded. Updates share
the host-observation transaction and happen before raw pruning.

Only a successful audit with collection status `ok` and four nonempty string
identity fields qualifies. A newer complete collection replaces the baseline,
including when its identity is unchanged. Baselines have no automatic expiry.
Failed SSH audits do not replace or refresh them or add identity uncertainty.
Newer unusable successful collections remain separate uncertainty evidence.
Explicitly missing configuration is contradictory; parsing/permission failures
do not prove configuration removal.

Initialization bootstraps from recoverable raw audits, compact history endpoints,
and the audit log. Compact timestamps are actual observed endpoints, not
interpolated dates. Evidence already removed from every source cannot be recovered.
Later initialization replays override writes idempotently; ordinary raw/compact
history cleanup never removes the baseline. Short-host writes apply to matching
FQDNs, including when a matching FQDN is first observed later.

Override boundaries remain separate from baseline replacement. Clearing requires
a later successful complete collection, confirmed override application, and
identity provisioner/pool matching the resolved intended destination. Unset must
match the role-default pool. Pending/failed/unknown application, unresolved intent,
and old-pool identities cannot clear a block. Remotely observed override changes
also establish a boundary. When a write records its target SHA, the later audit
must observe that SHA; unset must observe the override absent. A later scan of the
old, internally consistent configuration cannot verify a different recorded target.
Confirmed no-op writes do not create one; other recorded
mutation attempts are conservatively boundaries because failed SSH cannot establish
no change. Boundaries remain retained after clearance for idempotent replay.
Same-timestamp conflicting successful observations add uncertainty until a later
complete collection.

## JSON contract

`historical_worker_identity` contains:

| Field | Meaning |
| --- | --- |
| `status` | `present` or `missing` |
| `provisioner`, `worker_type`, `worker_group`, `worker_id` | Complete collected identity, otherwise nulls |
| `observation_ts` | Original collection timestamp, otherwise null |
| `freshness` | Existing one-hour source-age convention, not a recovery age limit |
| `collection_status` | `ok` for a baseline, otherwise `not_collected` |
| `source` | `host_configuration`, otherwise `unknown` |
| `provenance` | Source identity, timestamp, and override/application context; otherwise null |
| `contradiction_status` | `none_known`, `uncertain`, `contradicted`; `unknown` when baseline missing |
| `reasons` | Array of `{code, status, evidence_ts}` |

`provenance` contains `identity` (the same four normalized identity fields),
`observation_ts`, `source: host_configuration`, `override_present`, and
`configured_worker_type`. That last object follows the configured-pool contract,
including override SHA and application status, without derived `freshness`.
Resolution uses the retained source audit and currently available cached override
metadata. Missing metadata is uncertainty; role-derived intent never fills in the
historical identity.

Fixed reason codes:

| Code | Status | Meaning |
| --- | --- | --- |
| `identity_collection_unusable` | `uncertain` | Newer successful audit has unusable or absent identity collection |
| `worker_configuration_missing` | `contradicted` | Explicit configuration absence after baseline, retained across later collection errors |
| `configured_destination_unresolved` | `uncertain` | Intended provisioner/pool unresolved, including unavailable metadata |
| `configured_destination_mismatch` | `contradicted` | Complete identity names a different provisioner/pool than intended |
| `override_application_unconfirmed` | `uncertain` | Application pending, failed, or unknown |
| `override_change_unverified` | `contradicted` | Override boundary lacks later complete, applied, destination-matching evidence |
| `observation_conflict` | `uncertain` | Conflicting successful source states share a timestamp |

All applicable reasons remain visible; contradiction takes precedence over
uncertainty. `none_known` never means confirmed current identity.

Both `taskcluster` and `taskcluster.latest_lookup` add:

- `historical_identity_match`: `match`, `mismatch`, or `unknown`, comparing all
  four fields to the selected baseline independently of current identity.
- `historical_baseline`: exact pinned provenance used by a historical lookup;
  null for ordinary/pre-migration evidence or unavailable baseline.
- `historical_baseline_match`: equality of pinned and selected provenance,
  using `match`, `mismatch`, or `unknown`. Identity equality alone is insufficient.

Snapshot, initial follow output, and follow updates share this contract. Follow
updates include historical-state/metadata changes without requiring a new audit.
Time passing changes freshness without changing source timestamps or provenance.
Metadata changes can change interpreted source context; a lookup's pinned context
stays unchanged and may consequently mismatch.

## Explicit lookup

```bash
uv run fleetroll gather tc --use-historical-identity worker.example.test
uv run fleetroll host monitor worker.example.test --once --json
```

The option pins each baseline before querying its exact provisioner, pool, group,
and ID. It never falls back to role/default pool or short ID. Missing baseline
produces `identity_unresolved`. Diagnostic queries are allowed despite uncertainty
or contradiction, and success never clears those signals. Normal lookup behavior
is unchanged.

Attempts use `resolution_source: historical_worker_identity` and retain pinned
`historical_baseline`. Successful observations retain the same provenance;
`successful_observation_ts` links the attempt to its produced observation.
Failures preserve the old successful record and timestamp. Pool query failure,
missing worker, ambiguous identity, task lookup failure, and success remain
distinct. Duplicate complete identities are refused as ambiguous. Baseline
replacement during a query never relabels the result as validating the replacement.

## Example and migration

Abbreviated version 3 fields after an SSH failure following an applied override:

```json
{
  "worker_evidence_schema_version": 3,
  "ok": false,
  "observed_worker_identity": {"status": "unresolved"},
  "historical_worker_identity": {
    "status": "present",
    "provisioner": "releng-hardware",
    "worker_type": "gecko-t-linux-talos-2404-test",
    "worker_group": "mdc1",
    "worker_id": "configured-id",
    "observation_ts": "2026-11-01T00:00:00Z",
    "freshness": "stale",
    "collection_status": "ok",
    "source": "host_configuration",
    "contradiction_status": "none_known",
    "reasons": []
  },
  "taskcluster": {
    "historical_identity_match": "match",
    "historical_baseline_match": "match",
    "latest_lookup": {
      "resolution_source": "historical_worker_identity",
      "status": "success",
      "freshness": "fresh",
      "historical_identity_match": "match",
      "historical_baseline_match": "match"
    }
  }
}
```

Consumers must accept version 3. Version 2 current-evidence fields retain their
meanings; unresolved current evidence is never resolved by the baseline. For
historical recovery evidence, check source age and contradiction assessment,
fresh latest lookup success, full identity and exact provenance matches for both
attempt and retained success, and
`latest_lookup.successful_observation_ts == taskcluster.observation_ts`.
Evaluate `last_date_active` separately: null activity, missing workers, and failed
lookups do not prove inactivity. Medic still owns the recovery decision.
