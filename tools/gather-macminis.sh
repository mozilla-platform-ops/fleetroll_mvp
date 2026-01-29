#!/usr/bin/env bash

set -e
set -x

host_list="host-lists/all_macminis.list"

uv run fleetroll host-audit $host_list
uv run fleetroll tc-fetch $host_list
