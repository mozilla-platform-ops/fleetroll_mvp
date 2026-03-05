# Windows Support

## Pre-Design Notes (3/5/26)

### Metadata Script and Output

The fleetroll script is located at C:\management_scripts\ fleetroll_mvp_collect.ps1

It produces ronin_puppet_run.json in the same dir. It will overwrite the json if it exists.

#### Metadata format (early prototype)

Need to verify against a live host for the actual format.

```yaml
{
  "schema_version": 1,
  "ts": "2026-02-09T21:03:49Z",
  "duration_s": 0,
  "success": "NA",
  "exit_code": "NA",
  "role": "win116424h2hwalhpa",
  "git_repo": "https://github.com/mozilla-platform-ops/ronin_puppet.git",
  "git_branch": "RELOPS-1768",
  "git_sha": "deadbeef",
  "git_dirty": "NA",
  "vault_path": "NA",
  "vault_sha": "NA",
  "override_path": "NA",
  "override_sha": "NA",
  "bootstrap_stage": "complete",
  "bootstrap_complete": true
}
```

### Windows Hosts / Hostnames

All of the host names can be pulled from here: https://github.com/mozilla-platform-ops/worker-images/blob/main/provisioners/windows/MDC1Windows/pools.yml

pools.yml:

```yaml
pools:
  - name: "win11-64-24h2-hw-ref"
    openvox_version: "8.19.2"
    puppet_version: "8.10.0"
```

TODO: Store the script we use to generate the hostlist for this so we can later incorporate into the host list generator (mvp-1u6).

### Windows Host SSH Access

Use the win_audit key out of 1password and ssh adminstrator@. It will be powershell shell, so you should be able to invoke the script by the path.
