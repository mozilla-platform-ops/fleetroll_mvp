#!/usr/bin/env bash
set -e
set -x

host_list="configs/host-lists/all_moonshots.list"
options=""
uv run fleetroll host-monitor $host_list "$options" "$@"
