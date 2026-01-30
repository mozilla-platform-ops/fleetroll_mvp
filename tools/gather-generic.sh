#!/usr/bin/env bash

# set -e
# set -x

# check that $1 exists
if [ ! -f "$1" ]; then
  echo "you must sepcify a host list"
  exit 1
fi

host_list="$1"
shift
options="-q"

uv run fleetroll host-audit "$host_list" $options "$@"
uv run fleetroll tc-fetch "$host_list" $options "$@"
