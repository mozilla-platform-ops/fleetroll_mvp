# Windows Support

Beads epic mvp-3jw.

## Pre-Design Notes (3/5/26)

### Metadata Script and Output

The fleetroll script is located at C:\management_scripts\fleetroll_mvp_collect.ps1

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

#### Investigation of Deployed Host

Observations:
- metadata file doesn't exist, we have to run the script to create
  - the timestamp emitted is when the script ran, not when the host was converged

```bash
$ ssh administrator@t-nuc12-005.wintest2.releng.mdc1.mozilla.com
PS C:\Users\Administrator> cd C:\management_scripts\
PS C:\management_scripts> dir


    Directory: C:\management_scripts


Mode                 LastWriteTime         Length Name
----                 -------------         ------ ----
-a----          3/3/2026   9:43 PM           8997 fleetroll_mvp_collect.ps1
-a----          3/3/2026   9:43 PM           2962 force_pxe_install.ps1
-a----          3/3/2026   9:43 PM           3993 pool_audit.ps1


PS C:\management_scripts> .\fleetroll_mvp_collect.ps1
Wrote manifest: C:\management_scripts\ronin_puppet_run.json
Resolved repo path: C:\ronin
bootstrap_stage: complete
bootstrap_complete: True
git_sha: 9af2bb80e5d8bea3f92d6e3d726280ae6b46fc22
PS C:\management_scripts>
PS C:\management_scripts> ls


    Directory: C:\management_scripts


Mode                 LastWriteTime         Length Name
----                 -------------         ------ ----
-a----          3/3/2026   9:43 PM           8997 fleetroll_mvp_collect.ps1
-a----          3/3/2026   9:43 PM           2962 force_pxe_install.ps1
-a----          3/3/2026   9:43 PM           3993 pool_audit.ps1
-a----          3/5/2026   9:53 PM            549 ronin_puppet_run.json


PS C:\management_scripts> cat .\ronin_puppet_run.json
{
    "schema_version":  1,
    "ts":  "2026-03-05T21:53:18Z",
    "duration_s":  0,
    "success":  "NA",
    "exit_code":  "NA",
    "role":  "win116424h2hwref",
    "git_repo":  "https://github.com/mozilla-platform-ops/ronin_puppet.git",
    "git_branch":  "master",
    "git_sha":  "deadbeef",
    "git_dirty":  "NA",
    "vault_path":  "NA",
    "vault_sha":  "NA",
    "override_path":  "NA",
    "override_sha":  "NA",
    "bootstrap_stage":  "complete",
    "bootstrap_complete":  true
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


### Windows HW Pupppet Flow

Hosts netboot/PXE boot and then install their OS. After that, on first boot they do a puppet apply / converge.

They regularly check if the pool hash value is changed here: https://github.com/mozilla-platform-ops/worker-images/blob/main/provisioners/windows/MDC1Windows/pools.yml If it does the workers will reimage themselves.
