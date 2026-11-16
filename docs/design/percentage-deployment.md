# Role percentage deployment design

Implementation design for `mvp-3vc.2`; the operator contract is
[percentage deployment](../percentage-deployment.md).

Use the shared recorded-host selector without narrowing away unknown evidence.
Plan from the same transaction's monitor rows, latest and last-successful
observations, and retained override-write boundaries. Reuse the population
removal target's typed evidence and freshness interpretation. Keep role
membership, current exact-file matches, eligibility, and unknown membership
separate. The denominator includes all known-role members; eligibility limits
additions, never the denominator.

`percentage_deployment.py` owns versioned plans, deterministic natural-hostname
selection, decimal ceiling arithmetic, input validation, and execution preflight.
Additions are a deficit toward total coverage, preserving arbitrary existing
canaries. Pending writes require newer collection before a new plan can select
another wave. Saved plans freeze their scope/additions and desired checksum.
Preflight reuses recorded selection, tolerates selected hosts' own desired-file
progress, and refuses other relevant state changes or expired addition evidence.

`commands/percentage_set.py` supplies preview/reporting and dispatches additions
through the existing `set_override_for_host` worker. Local validation, worker-type
checks, transport, audit records, and automatic refresh remain shared with
explicit deployment. Extend the worker only for checked outcome/provenance
fields and exception handling in this mode. Preserve legacy explicit output.

The checked `remote_set_script` path reads the live role, validates current and
incoming file identity, normalizes metadata on a same-directory temporary file,
then uses POSIX `link` for atomic absent-path installation. It never overwrites a
conflicting file or follows a directory destination as `ln` can. An identical
concurrent file becomes an idempotent no-op. Since additions require absence,
there is no old file to back up. Role and population checks remain snapshots;
the command is not a fleet transaction or a Puppet convergence evaluator.
