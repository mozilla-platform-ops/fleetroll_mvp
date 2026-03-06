#!/usr/bin/env bash

set -e
set -x

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

./generate_windows_host_list.py

# TODO: generate mac list (read ronin-puppet mac inventory files)

# TODO: how to generate for linux? gdocs api? gdocs export that's processed locally?

# TODO: generate all.list
#  - all.list should contain all hosts, including windows, mac, and linux
