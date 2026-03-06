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

### Windows Host SSH Access

Use the win_audit key out of 1password and ssh adminstrator@. It will be powershell shell, so you should be able to invoke the script by the path.


### Windows HW Pupppet Flow

Hosts netboot/PXE boot and then install their OS. After that, on first boot they do a puppet apply / converge.

They regularly check if the pool hash value is changed here: https://github.com/mozilla-platform-ops/worker-images/blob/main/provisioners/windows/MDC1Windows/pools.yml If it does the workers will reimage themselves.

## Host List

The host list is stored at `configs/host-lists/windows/all.list` and generated from `pools.yml`. To regenerate from the latest pools:

```bash
uv run tools/generate_windows_host_list.py
```

The script fetches `pools.yml` via `gh api`, extracts nodes and domain suffixes per pool, and writes the file with pool grouping comments. Known-BAD hosts are included with annotating comments. The file uses the `# fqdn:` directive so `parse_host_list()` auto-expands short names to FQDNs.

## Implementation Details

### Audit Command Flow

Host detection happens in `fleetroll/ssh.py:is_windows_host()`: it strips any `user@` prefix and checks for the `"wintest"` substring, which covers all current Windows hosts (`*.wintest2.releng.mdc1.mozilla.com`).

In `fleetroll/commands/audit.py` (around line 286), each host branches before the SSH call:

```python
if is_windows_host(host):
    remote_cmd = remote_windows_audit_script()
    ssh_host = windows_ssh_host(host)
else:
    remote_cmd = remote_audit_script(include_content=include_content)
    ssh_host = host
```

`windows_ssh_host()` prepends `administrator@` if no user is already specified. Both paths feed into the same `run_ssh()` + `process_audit_result()` pipeline.

### SSH Approach

The PowerShell script body (`windows_audit_script_body()`) is encoded as UTF-16LE, then base64-encoded, and invoked via:

```
powershell -EncodedCommand <base64-blob>
```

This avoids all SSH/shell quoting issues. Windows OpenSSH defaults to PowerShell as the shell, so no wrapper is needed. See `fleetroll/ssh.py:remote_windows_audit_script()`.

### Data Collected

The collect script at `C:\management_scripts\fleetroll_mvp_collect.ps1` generates `C:\management_scripts\ronin_puppet_run.json` (path constants in `fleetroll/constants.py`: `WIN_COLLECT_SCRIPT_PATH`, `WIN_METADATA_JSON_PATH`).

The remote audit script (`windows_audit_script_body()`) in `fleetroll/ssh.py`:

1. Runs the collect script if `ronin_puppet_run.json` does not yet exist.
2. Reads the JSON file, base64-encodes it (UTF-8), and emits key=value lines:

```
OS_TYPE=Windows
ROLE_PRESENT=1
ROLE=<role from JSON>
VLT_PRESENT=0
OVERRIDE_PRESENT=0
PP_STATE_JSON=<base64-encoded JSON content>
```

Vault and override detection are always `0` for Windows hosts — these concepts don't apply.

### Output Normalization

`_normalize_na()` in `fleetroll/audit.py` converts Windows JSON `"NA"` string values (used for fields like `success`, `exit_code`, `git_dirty`, etc.) to Python `None` for standard null handling downstream:

```python
def _normalize_na(value: Any) -> Any:
    if value == "NA":
        return None
    return value
```

This is called for every field extracted from `PP_STATE_JSON` in `process_audit_result()`.

### Monitor Display

In `fleetroll/commands/monitor/data.py:build_ok_row_values()`:

- OS type `"Windows"` maps to the single-character abbreviation `"W"`.
- Puppet columns (`pp_last`, `pp_exp`, `pp_match`, `healthy`) are suppressed with `"-"` for Windows hosts (Windows does not run Puppet the same way):

```python
if os_type == "W":
    pp_last = "-"
    pp_exp = "-"
    pp_match = "-"
    healthy = "-"
```

In `fleetroll/commands/monitor/types.py`:

- `cycle_os_filter()` cycles: `None → "L" → "M" → "W" → None`
- `os_filter_label()` maps `"W"` → `"Windows"` for the status bar label
