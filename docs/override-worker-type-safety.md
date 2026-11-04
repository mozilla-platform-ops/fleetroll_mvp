# Worker type safety for override deployment

`override set` checks worker type compatibility before deployment
(implementation tracked by mvp-wdlt).

When an override sets `WORKER_TYPE_OVERRIDE`, compare it for each target host
against the normal worker type from the host's latest successful recorded role
observation and Fleetroll's role-to-worker-type mapping. Accept the base worker
type itself, or the base followed by `-` and a nonempty suffix. For example,
`gecko-t-linux-talos-2404-tc-v115` matches the 2404 Talos base type.

The dry run should show each host's compatibility, expected and requested worker
types, and role observation timestamp or age. A mismatch, missing role, or unknown
mapping should block deployment by default. Provide an explicit bypass for
intentional exceptions (`--allow-worker-type-mismatch`), and make its use visible
in the preview and write audit records. Overrides with
no `WORKER_TYPE_OVERRIDE` do not need this check. Recheck compatibility when
deploying; do not rely on an earlier preview's result.

The check runs even with `--force`, `--ignore-state`, or `--no-validate`. Any
failed check blocks the whole batch before writes. JSON previews include
`worker_type_checks` and `allow_worker_type_mismatch`. Literal quoted, unquoted,
and exported assignments are supported; duplicate assignments use the last value.
Active references that cannot be parsed as standalone literal assignments require
the bypass. The file is never executed locally to discover its worker type. This
is a sanity check for ordinary override files, not a shell interpreter or a
security boundary for arbitrary shell programs.

## Freshness and limits

The check uses recorded observations. Show their age so the operator can decide
whether to refresh them with `gather host`; do not impose a freshness cutoff or
automatically refresh before deployment. A stale role can lead to approval for
the wrong pool, and a role can change between observation and writing the file.
The check does not prove that a suffixed pool exists or restrict the Puppet branch
or other override settings. Checking the live role as part of the remote write
could narrow the race in a future enhancement.

An alternative is a `WORKER_TYPE_OVERRIDE_SUFFIX` interpreted on the host by
`run-puppet`, deriving the pool from each host's base worker type. That requires
on-host support and is outside this proposal. Explicit target metadata in the
override is another possible future safeguard for the entire override.
