#!/usr/bin/env bash

set -e

# lists hosts that haven't talked to tc and have been unreachable for awhile
#
# idea: use this as a source of hosts to reset

uv run fleetroll host-monitor \
    configs/host-lists/linux/all.list \
    --once \
    --hostname-only \
    --filter-file configs/filters/moonshots-hung.yaml | \
    tr '\n' ' '
echo
