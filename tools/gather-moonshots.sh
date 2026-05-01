#!/usr/bin/env bash

# set -e
# set -x

host_list="configs/host-lists/linux/all.list"
options="-q"

uv run fleetroll gather-host $host_list $options "$@"
uv run fleetroll gather-tc $host_list $options "$@"
