#!/bin/bash
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# Production State File Verifier
# Validates that /etc/puppet/last_run_metadata.json exists and has correct format

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

STATE_FILE="/etc/puppet/last_run_metadata.json"

echo "========================================"
echo "Puppet State File Verifier"
echo "========================================"
echo ""
echo "Checking: $STATE_FILE"
echo ""

# Check if state file exists
if [ ! -f "$STATE_FILE" ]; then
    echo -e "${RED}FAIL${NC}: State file does not exist"
    echo "Expected location: $STATE_FILE"
    exit 1
fi
echo -e "${GREEN}✓${NC} State file exists"

# Check if file is readable
if [ ! -r "$STATE_FILE" ]; then
    echo -e "${RED}FAIL${NC}: State file is not readable"
    exit 1
fi
echo -e "${GREEN}✓${NC} State file is readable"

# Validate JSON format
if command -v jq >/dev/null 2>&1; then
    if jq empty "$STATE_FILE" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} JSON is valid"
    else
        echo -e "${RED}FAIL${NC}: Invalid JSON"
        exit 1
    fi
else
    # Basic JSON validation without jq
    if grep -q '"schema_version"' "$STATE_FILE" && \
       grep -q '"ts"' "$STATE_FILE" && \
       grep -q '"success"' "$STATE_FILE"; then
        echo -e "${GREEN}✓${NC} JSON appears valid (basic check)"
    else
        echo -e "${RED}FAIL${NC}: JSON validation failed"
        exit 1
    fi
fi

# Check for required fields
REQUIRED_FIELDS=(
    "schema_version"
    "ts"
    "duration_s"
    "success"
    "exit_code"
    "role"
    "git_repo"
    "git_branch"
    "git_sha"
    "git_dirty"
    "vault_path"
    "vault_sha"
    "override_path"
    "override_sha"
)

MISSING_FIELDS=()
for field in "${REQUIRED_FIELDS[@]}"; do
    if grep -q "\"$field\"" "$STATE_FILE"; then
        echo -e "${GREEN}✓${NC} Field present: $field"
    else
        echo -e "${RED}✗${NC} Field missing: $field"
        MISSING_FIELDS+=("$field")
    fi
done

if [ ${#MISSING_FIELDS[@]} -gt 0 ]; then
    echo ""
    echo -e "${RED}FAIL${NC}: Missing fields: ${MISSING_FIELDS[*]}"
    exit 1
fi

echo ""
echo "========================================"
echo "State File Content"
echo "========================================"
echo ""
cat "$STATE_FILE"
echo ""

# Show file age
echo "========================================"
echo "File Information"
echo "========================================"
echo ""
echo "Last modified: $(stat -c %y "$STATE_FILE" 2>/dev/null || stat -f "%Sm" "$STATE_FILE")"
echo ""

echo "========================================"
echo -e "${GREEN}SUCCESS${NC}: State file is valid"
echo "========================================"
