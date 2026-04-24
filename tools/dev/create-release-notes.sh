#!/usr/bin/env bash

set -e
set -x

uv run release-notes --force --all
