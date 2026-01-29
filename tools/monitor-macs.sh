#!/usr/bin/env bash
set -e
set -x

host_list="host-lists/all_macminis.list"
options=""
uv run fleetroll host-monitor $host_list "$options" "$@"
