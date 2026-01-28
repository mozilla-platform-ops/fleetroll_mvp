# Fleetroll MVP

[![CI](https://github.com/aerickson/fleetroll_mvp/actions/workflows/ci.yml/badge.svg)](https://github.com/aerickson/fleetroll_mvp/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/aerickson/fleetroll_mvp/branch/main/graph/badge.svg)](https://codecov.io/gh/aerickson/fleetroll_mvp)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

<p align="center">
  <img src="images/screenshot_1.jpg" alt="Fleetroll Monitor Interface" width="800">
</p>

Fleetroll MVP streamlines host state visibility and configuration management.It's a tool for auditing, monitoring, and managing Linux (and Mac soon) hardware (long-running) hosts in the Mozilla FirefoxCI Taskcluster environment using Puppet. It's designed for the Mozilla Release Operations team.

Fleetroll MVP is an exploration on the concepts in Fleetroll product spec in development. See [`specs/FleetRoll_Product_Spec_v5.md`](specs/FleetRoll_Product_Spec_v5.md).


## Functionality

Fleetroll MVP aims to solve some of the issues described in the `Problem Statement` section in [`specs/FleetRoll_Product_Spec_v5.md`](specs/FleetRoll_Product_Spec_v5.md).

Fleetroll MVP currently provides the ability to:
- audit fleets of hosts to determine the host vault, puppet, and override status
- include Taskcluster information for the hosts
- deploy overrides and vault.yaml files
- validate override file syntax

## Background

On Linux and Mac hardware, the hosts run puppet at startup.

### Linux flow

The taskcluster worker (generic-worker) is only run if puppet passes.

```
systemd runs run-puppet.sh script at boot
  -> run-puppet.sh writes /tmp/puppet_run_done on success

gnome-terminal autostart (18.04) or systemd (24.04) starts the 'generic-worker launch script' (real name TBD)
  'generic-worker launch script' waits for /tmp/puppet_run_done to run, then launches g-w
```

### Mac flow

generic-worker will start on Mac even if the puppet run is unsuccesful.

```
/Library/LaunchDaemons/org.mozilla.worker-runner.plist starts g-w if /var/tmp/semaphore/run-buildbot exists

TODO: verify mac is running at boot... not sure if it is
```

## Setup

```bash
## install deps
# prek (https://github.com/j178/prek, git commit hooks)
brew install prek
# actionlint (URL?, github actions linting)
brew install actionlinst
# beads-rust (https://github.com/Dicklesworthstone/beads_rust, repo issue tracking)
curl -fsSL "https://raw.githubusercontent.com/Dicklesworthstone/beads_rust/main/install.sh?$(date +%s)" | bash

# just running the program
uv sync

# development
# TODO: use --all-groups?
uv sync --group test
```

## Usage

### Auditing hosts

```bash
# audit a single host
uv run fleetroll host-audit t-linux64-ms-238.test.releng.mdc1.mozilla.com

# audit a list of hosts
uv run fleetroll host-audit 1804.list
```

### Monitoring

#### Host data

```bash
# monitor last recorded audit state (live-updating, follows by default)
uv run fleetroll host-monitor 1804.list
# keys: q quit, up/down (or j/k) scroll, left/right horizontal scroll, PgUp/PgDn page

# monitor once (no follow)
uv run fleetroll host-monitor 1804.list --once
```

### TaskCluster data

```bash
# fetch TaskCluster worker data for hosts (stores in ~/.fleetroll/taskcluster_workers.jsonl)
uv run fleetroll tc-fetch 1804.list

# verbose output (shows API calls)
uv run fleetroll tc-fetch -v 1804.list
```

### Override management

```bash
# show stored override contents by sha prefix or human hash
uv run fleetroll show-override 0328af8c9d6f
uv run fleetroll show-override freddie-arkansas

# set override (single host)
uv run fleetroll host-set-override --from-file ~/.fleetroll/overrides/0328af8c9d6f t-linux64-ms-229.test.releng.mdc1.mozilla.com --confirm

# set override (host list)
uv run fleetroll host-set-override --from-file ~/.fleetroll/overrides/0328af8c9d6f 1804.list --confirm

# unset override (host list)
uv run fleetroll host-unset-override 1804.list --confirm
```

### Vault management

```bash
# show stored vault contents by sha prefix or human hash
uv run fleetroll show-vault 0328af8c9d6f
uv run fleetroll show-vault jupiter-lactose

# set vault (single host) - defaults to /root/vault.yaml
uv run fleetroll host-set-vault --from-file vault.yaml t-linux64-ms-229.test.releng.mdc1.mozilla.com --confirm

# set vault (host list)
uv run fleetroll host-set-vault --from-file vault.yaml 1804.list --confirm
```

### Debugging

```bash
# print the remote audit script (useful for debugging)
uv run fleetroll debug-host-script

# wrap as ssh-ready command
uv run fleetroll debug-host-script --wrap
```

## Development

### TODOs

Stored in beads. See `AGENTS.md` for more info.

### Testing

```bash
# testing
uv run pytest

# pytest-watcher
uv run ptw .
```
