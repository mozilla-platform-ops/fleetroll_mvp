#!/usr/bin/env bash

set -e

#uv run fleetroll host-audit 1804.list
#uv run fleetroll host-audit 2404.list

uv run fleetroll host-audit 1804-and-2404.list
