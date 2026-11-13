#!/usr/bin/env bash
set -e

uv run fr host-monitor configs/host-lists/linux/all.list
