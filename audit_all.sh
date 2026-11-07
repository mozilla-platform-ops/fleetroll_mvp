#!/usr/bin/env bash

set -e

uv run fleetroll gather host configs/host-lists/linux/all_moonshots.list
