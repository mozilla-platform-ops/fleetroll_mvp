#!/usr/bin/env bash

set -e

#uv run fleetroll gather-host 1804.list
#uv run fleetroll gather-host 2404.list

uv run fleetroll gather-host 1804-and-2404.list
