#!/usr/bin/env bash

# set -e
# set -x

host_list="configs/host-lists/all_moonshots.list"
options="-q"

uv run fleetroll host-audit $host_list $options "$@"
uv run fleetroll tc-fetch $host_list $options "$@"
