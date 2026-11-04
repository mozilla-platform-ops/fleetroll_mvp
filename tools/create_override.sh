#!/usr/bin/env bash

# Compatibility wrapper: retain the repository output directory default.
set -euo pipefail
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$repo_dir" fleetroll override create \
  --output-dir "$repo_dir/configs/overrides" "$@"
