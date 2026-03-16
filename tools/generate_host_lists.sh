#!/usr/bin/env bash

set -e
set -x

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

./generate_windows_host_list.py

./generate_mac_host_list.py

# TODO: how to generate for linux? gdocs api? gdocs export that's processed locally?

# generate base (mega) all.list
./generate_all_host_list.py
