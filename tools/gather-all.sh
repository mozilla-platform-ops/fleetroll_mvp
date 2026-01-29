#!/usr/bin/env bash

# set -e
# set -x

host_list="host-lists/all.list"
options="-q"

uv run fleetroll host-audit $host_list $options "$@"
uv run fleetroll tc-fetch $host_list $options "$@"
