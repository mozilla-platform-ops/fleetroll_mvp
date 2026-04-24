#!/usr/bin/env bash

set -e

# lists hosts that havne't talked to tc and have been unreachable for awhile
#
# idea: use this as a source of hosts to reset

filter="tc_act>4h data>4h note=- sort:host:asc"
# filter="tc_act>4h note=-"

uv run fleetroll host-monitor  \
    configs/host-lists/linux/all.list \
    --once \
    --hostname-only \
    --filter "$filter" | \
    tr '\n' ' '
echo
