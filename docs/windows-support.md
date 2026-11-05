# Windows hosts

Fleetroll gathers Windows hardware-worker metadata over SSH/PowerShell and
normalizes it for the monitor (epic `mvp-3jw`). Windows provisioning uses PXE
installation and initial convergence; pool-hash changes trigger reimaging.
Override and vault management do not apply to these hosts.

## SSH access

Hosts under `*.wintest2.releng.mdc1.mozilla.com` use the shared
`id_rsa.win_audit_2021_02-17` key from the team's password safe (search
`win_audit`). Save it under `~/.ssh/` with mode `0600`. The remote user is
`administrator`, added automatically when no user is specified.

Configure SSH once:

```sshconfig
Host *.wintest2.releng.mdc1.mozilla.com
    User administrator
    IdentityFile ~/.ssh/id_rsa.win_audit_2021_02-17
    IdentitiesOnly yes
```

Or pass the key per collection:

```bash
uv run fleetroll gather host t-nuc12-005.wintest2.releng.mdc1.mozilla.com \
  --ssh-option "-i ~/.ssh/id_rsa.win_audit_2021_02-17" \
  --ssh-option "-o IdentitiesOnly=yes"
```

`IdentitiesOnly=yes` prevents attempts with other agent keys from exhausting the
server's authentication limit. Check key selection and permissions if collection
reports `Permission denied (publickey)`.

## Inventory

`configs/host-lists/windows/all.list` is generated from the worker-images
repository's `provisioners/windows/MDC1Windows/pools.yml`. Regenerate it with:

```bash
uv run tools/generate_windows_host_list.py
```

The generator uses `gh api`, preserves pool grouping and known-BAD annotations,
and emits a `# fqdn:` directive for short-name expansion.

## Metadata collection

The remote script `C:\management_scripts\fleetroll_mvp_collect.ps1` writes
`C:\management_scripts\ronin_puppet_run.json`. Fleetroll runs it if that JSON
file is absent, then reads the file. Its `ts` records metadata-script execution,
not the time the host converged; an existing file is not regenerated on every
scan.

The metadata contains schema version, timestamp, role, repository/branch/revision,
and bootstrap stage/completion. Fields unavailable on Windows, such as Puppet
success/exit status and override/vault details, may contain `"NA"`; the parser
normalizes those values to null. Bootstrap completion is not a Puppet run-success
signal.

`fleetroll/ssh.py` detects Windows by the `wintest` hostname substring after
removing any `user@` prefix. It sends a UTF-16LE/base64 PowerShell body using
`-EncodedCommand`. That body returns the JSON as UTF-8/base64 `PP_STATE_JSON`
alongside `OS_TYPE=Windows`, role, and absent override/vault flags. The shared
`fleetroll/audit.py` parser handles the result.

## Monitor behavior

Windows rows show `OS=W` and suppress `PP_LAST`. Where recorded Windows pool
metadata resolves the role, `PP_EXP` shows the pool hash and `PP_MATCH` compares
it with the reported revision. Explicit Puppet failure prevents a match; missing
success does not, because Windows normally does not report it. Without pool data,
the comparison remains unavailable. `HEALTHY` combines that match with TC activity
under one hour, as described in [rollout verification](rollout-health-verification.md).

This is a pool-image comparison, not proof of a recent Puppet run. Linux/macOS
[worker identity collection](reference/worker-identity.md) does not replace Windows's
legacy pool discovery.
