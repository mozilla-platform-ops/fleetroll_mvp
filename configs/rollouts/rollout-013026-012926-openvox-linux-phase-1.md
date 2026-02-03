# Rollout: 012926-openvox-linux-phase-1

## Metadata

- **Created by:** aerickson
- **Date:** 2026-01-30
- **Branch:** `012926-openvox-linux-phase-1`
- **Override:** `configs/overrides/012926-openvox-linux-phase-1.override`
- **Puppet repo:** https://github.com/aerickson/ronin_puppet.git

## Stage 1: Canary (small test set)

Deploy to initial canary hosts and monitor for issues.

- [x] Deploy to initial canary set

  ```bash
  # Deploy override
  uv run fleetroll host-set-override --from-file configs/overrides/012926-openvox-linux-phase-1.override configs/host-lists/canary-1804-and-2404-set-2.list

  ```

- [x] Monitor rollout health (`RO_HEALTH` column)
- [ ] Verify puppet runs succeed
  - problems.
    - 18.04 not supported. reverted. TO FIX
    - 24.04: bug. fix pushed. green now.
- [ ] Deploy to second canary set

  ```bash
  # Deploy override
  uv run fleetroll host-set-override --from-file configs/overrides/012926-openvox-linux-phase-1.override configs/host-lists/TBD.list
  ```

## Stage 2: Broader canary

Expand to all canary hosts.

- [ ] Deploy to all canary hosts

  ```bash
  # Deploy override
  uv run fleetroll host-set-override --from-file configs/overrides/012926-openvox-linux-phase-1.override configs/host-lists/TBD.list
  ```

- [ ] Monitor rollout health
- [ ] Verify TaskCluster workers are active

## Stage 3: Production rollout (batch 1)

Begin production rollout with first batch.

- [ ] Deploy to first batch of production hosts

  ```bash
  # Deploy override
  uv run fleetroll host-set-override --from-file configs/overrides/012926-openvox-linux-phase-1.override configs/host-lists/TBD.list
  ```

- [ ] Monitor rollout health

## Stage 4: Production rollout (remaining)

Complete production rollout to all hosts.

- [ ] Deploy to all remaining hosts

  ```bash
  # Deploy override
  uv run fleetroll host-set-override --from-file configs/overrides/012926-openvox-linux-phase-1.override configs/host-lists/TBD.list
  ```

- [ ] Monitor final rollout health
- [ ] Verify all hosts show `RO_HEALTH=Y`

## Rollback (if needed)

If issues are encountered, remove the override from affected hosts.

```bash
# Remove override from specific hosts
uv run fleetroll host-remove-override configs/host-lists/TBD.list
```
