#!/usr/bin/env bash

set -e
set -x

uv run fleetroll host-monitor host-lists/all.list
