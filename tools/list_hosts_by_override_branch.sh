#!/usr/bin/env bash

set -e

# lists hosts with a specific override branch
#
# usage: tools/list_hosts_by_override_branch.sh <branch-substring>
# example: tools/list_hosts_by_override_branch.sh 031326-run-puppet-tweaks

branch="${1:?usage: $0 <branch-substring>}"

filter="ovr_bch~${branch} sort:host:asc"

uv run fleetroll host-monitor \
    configs/host-lists/linux/all.list \
    --once \
    --hostname-only \
    --filter "$filter" | \
    tr '\n' ' '
echo
