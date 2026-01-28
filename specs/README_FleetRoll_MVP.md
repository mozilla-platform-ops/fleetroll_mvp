# FleetRoll MVP (single-host audit + set/unset override)

This is a **minimal** implementation you can deploy immediately, then grow into the full FleetRoll system.

It intentionally matches the spec’s early-value goals:

- CLI-only mutations (no UI required)
- SSH-based probing
- Target override file: `/etc/puppet/ronin_settings`
- Local JSONL audit log for every action

## What it does now

### 1) Audit a host
- role presence + value (`/etc/puppet_role`)
- override presence (`/etc/puppet/ronin_settings`)
- override metadata (mode/owner/group/size/mtime)
- optionally prints override contents and computes SHA256

### 2) Set override (atomic)
- writes a temp file in the same directory
- `chmod/chown` it
- optional backup of any existing override
- `mv` into place atomically

### 3) Unset override
- optional backup
- `rm -f` the override file

## Requirements

- local machine has `ssh` available
- your SSH config/agent grants access to the host
- host supports `sudo -n` for the remote user (passwordless sudo)

## Usage

Make executable:

```bash
chmod +x fleetroll_mvp.py
```

Audit:

```bash
./fleetroll_mvp.py host-audit myhost.example.net
./fleetroll_mvp.py host-audit myhost.example.net --no-content
./fleetroll_mvp.py host-audit myhost.example.net --json
```

Set override from local file:

```bash
./fleetroll_mvp.py host-set-override myhost.example.net \
  --from-file ./ronin_settings.txt \
  --reason "Enable feature X for debugging" \
  --confirm
```

Set override from stdin:

```bash
cat ./ronin_settings.txt | ./fleetroll_mvp.py host-set-override myhost.example.net --confirm
```

Unset override:

```bash
./fleetroll_mvp.py host-unset-override myhost.example.net \
  --reason "Rollback override after incident" \
  --confirm
```

SSH options (ProxyJump, alternate port, etc.):

```bash
./fleetroll_mvp.py --ssh-option "-J bastion.example.net" host-audit myhost.example.net
./fleetroll_mvp.py --ssh-option "-p 2222" host-audit myhost.example.net
```

## Audit log

Default location: `~/.fleetroll/audit.jsonl`

Each line is a JSON object recording:
- actor
- timestamp
- action
- host
- parameters (reason, hashes, backups, etc.)
- result (ssh rc, ok, stderr)

**Note:** `host-audit` stores override content hashes (SHA256) in the audit log; it does not write raw contents to JSONL.

## Next steps to grow into full FleetRoll

1. Add `inventory seed add/remove/list` backed by a local SQLite DB.
2. Add `host refresh` to store probe results per host (sshable, sudoable, role, override_present, override_hash).
3. Add `host list --drift-type override` built from “override_present vs expected”.
4. Add `fleet audit` (parallel probes with bounded concurrency).
5. Only then add rollouts/populations/stages/gates with the state machines from the spec.
