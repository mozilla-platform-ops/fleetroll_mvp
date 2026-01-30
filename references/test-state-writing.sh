#!/bin/bash
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# Test script for puppet state writing functionality
# Tests the write_puppet_state function against live system data

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "Puppet State Writing Test"
echo "========================================"
echo ""

# Source the state writing function
if [ ! -f "${SCRIPT_DIR}/write_puppet_state.sh" ]; then
    echo -e "${RED}FAIL${NC}: write_puppet_state.sh not found at ${SCRIPT_DIR}/write_puppet_state.sh"
    exit 1
fi

echo -e "${GREEN}✓${NC} Found write_puppet_state.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/write_puppet_state.sh"

if ! type write_puppet_state >/dev/null 2>&1; then
    echo -e "${RED}FAIL${NC}: write_puppet_state function not loaded"
    exit 1
fi
echo -e "${GREEN}✓${NC} Loaded write_puppet_state function"
echo ""

# Detect OS and set paths
# Use temp paths for testing unless running with sudo
# Note: override/vault paths should be OUTSIDE the git working directory
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    if [ "$EUID" -eq 0 ]; then
        OVERRIDE_PATH="/opt/puppet_environments/ronin_settings"
        VAULT_PATH="/var/root/vault.yaml"
    else
        OVERRIDE_PATH="/tmp/test_puppet_config/ronin_settings"
        VAULT_PATH="/tmp/test_puppet_config/vault.yaml"
    fi
    echo "Detected OS: macOS"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
    if [ "$EUID" -eq 0 ]; then
        OVERRIDE_PATH="/etc/puppet/ronin_settings"
        VAULT_PATH="/root/vault.yaml"
    else
        OVERRIDE_PATH="/tmp/test_puppet_config/ronin_settings"
        VAULT_PATH="/tmp/test_puppet_config/vault.yaml"
    fi
    echo "Detected OS: Linux"
else
    echo -e "${RED}FAIL${NC}: Unsupported OS: $OSTYPE"
    exit 1
fi
echo ""

# Check for required paths
echo "Checking system paths..."

# Check for puppet working directory
WORKING_DIR=""
if [ -d "/etc/puppet/environments" ]; then
    # Find first puppet environment directory
    WORKING_DIR=$(find /etc/puppet/environments -maxdepth 2 -name code -type d 2>/dev/null | head -n 1)
fi

if [[ "$OS" == "macos" ]] && [ -d "/opt/puppet_environments" ]; then
    # macOS alternative location
    if [ -z "$WORKING_DIR" ]; then
        WORKING_DIR=$(find /opt/puppet_environments -maxdepth 2 -name ronin_puppet -type d 2>/dev/null | head -n 1)
    fi
fi

if [ -z "$WORKING_DIR" ]; then
    echo -e "${YELLOW}WARNING${NC}: No puppet working directory found, using /tmp/test_puppet_repo"
    WORKING_DIR="/tmp/test_puppet_repo"

    # Always recreate test repo to ensure clean state
    if [ -d "$WORKING_DIR" ]; then
        echo "Removing old test repository..."
        rm -rf "$WORKING_DIR"
    fi

    echo "Setting up test git repository..."
    mkdir -p "$WORKING_DIR"
    (
        cd "$WORKING_DIR" || exit 1
        git init
        git config user.email "test@example.com"
        git config user.name "Test User"
        echo "# Test Puppet Repository" > README.md
        git add README.md
        git commit -m "Initial commit"
        git remote add origin https://github.com/test/puppet.git
        git checkout -b production
    )
    echo -e "${GREEN}✓${NC} Created test git repository with commits"
fi
echo -e "${GREEN}✓${NC} Working directory: $WORKING_DIR"

# Clean up and recreate test config directory for non-root users
if [[ "$OVERRIDE_PATH" == "/tmp/test_puppet_config"* ]]; then
    if [ -d "/tmp/test_puppet_config" ]; then
        rm -rf /tmp/test_puppet_config
    fi
fi

# Create test override and vault files if they don't exist
if [ ! -f "$OVERRIDE_PATH" ]; then
    echo "Creating test override file at $OVERRIDE_PATH..."
    OVERRIDE_DIR=$(dirname "$OVERRIDE_PATH")
    if mkdir -p "$OVERRIDE_DIR" 2>/dev/null; then
        cat > "$OVERRIDE_PATH" <<'OVERRIDE_EOF'
# Test override settings
worker_type: gecko-t-linux
deployment_stage: testing
region: us-west-2
OVERRIDE_EOF
        echo -e "${GREEN}✓${NC} Created test override file"
    else
        echo -e "${YELLOW}WARNING${NC}: Cannot create override file (insufficient permissions)"
    fi
else
    echo -e "${GREEN}✓${NC} Override file exists: $OVERRIDE_PATH"
fi

if [ ! -f "$VAULT_PATH" ]; then
    echo "Creating test vault file at $VAULT_PATH..."
    VAULT_DIR=$(dirname "$VAULT_PATH")
    if mkdir -p "$VAULT_DIR" 2>/dev/null; then
        cat > "$VAULT_PATH" <<'VAULT_EOF'
# Test vault secrets
api_key: test_key_12345
database_password: test_password_67890
VAULT_EOF
        echo -e "${GREEN}✓${NC} Created test vault file"
    else
        echo -e "${YELLOW}WARNING${NC}: Cannot create vault file (insufficient permissions)"
    fi
else
    echo -e "${GREEN}✓${NC} Vault file exists: $VAULT_PATH"
fi

# Check for puppet role
ROLE="test-role"
if [ -f "/etc/puppet_role" ]; then
    ROLE=$(<"/etc/puppet_role")
    echo -e "${GREEN}✓${NC} Found puppet role: $ROLE"
else
    echo -e "${YELLOW}WARNING${NC}: No puppet role file found, using test role: $ROLE"
fi

echo ""
echo "========================================"
echo "Test 1: Write state file"
echo "========================================"

# Create test output file
TEST_OUTPUT_FILE=$(mktemp /tmp/puppet_state_test.XXXXXX.json)
echo "Output file: $TEST_OUTPUT_FILE"

# Call write_puppet_state with test data
EXIT_CODE=2  # Simulating successful puppet run with changes
DURATION=45

echo "Writing state with parameters:"
echo "  working_dir: $WORKING_DIR"
echo "  role: $ROLE"
echo "  exit_code: $EXIT_CODE"
echo "  duration: ${DURATION}s"
echo "  override_path: $OVERRIDE_PATH"
echo "  vault_path: $VAULT_PATH"
echo ""

write_puppet_state "$WORKING_DIR" "$ROLE" "$EXIT_CODE" "$DURATION" \
    "$OVERRIDE_PATH" "$VAULT_PATH" "$TEST_OUTPUT_FILE"

if [ ! -f "$TEST_OUTPUT_FILE" ]; then
    echo -e "${RED}FAIL${NC}: State file not created"
    exit 1
fi
echo -e "${GREEN}✓${NC} State file created"
echo ""

echo "========================================"
echo "Test 2: Validate JSON format"
echo "========================================"

# Check if jq is available for JSON validation
if command -v jq >/dev/null 2>&1; then
    if jq empty "$TEST_OUTPUT_FILE" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} JSON is valid"
    else
        echo -e "${RED}FAIL${NC}: Invalid JSON"
        cat "$TEST_OUTPUT_FILE"
        rm "$TEST_OUTPUT_FILE"
        exit 1
    fi
else
    # Basic JSON validation without jq
    if grep -q '"schema_version"' "$TEST_OUTPUT_FILE" && \
       grep -q '"ts"' "$TEST_OUTPUT_FILE" && \
       grep -q '"success"' "$TEST_OUTPUT_FILE"; then
        echo -e "${GREEN}✓${NC} JSON appears valid (basic check)"
    else
        echo -e "${RED}FAIL${NC}: JSON validation failed"
        cat "$TEST_OUTPUT_FILE"
        rm "$TEST_OUTPUT_FILE"
        exit 1
    fi
fi
echo ""

echo "========================================"
echo "Test 3: Verify required fields"
echo "========================================"

REQUIRED_FIELDS=(
    "schema_version"
    "ts"
    "success"
    "exit_code"
    "git_repo"
    "git_branch"
    "git_sha"
    "git_dirty"
    "override_path"
    "override_sha"
    "vault_path"
    "vault_sha"
    "role"
    "duration_s"
)

MISSING_FIELDS=()
for field in "${REQUIRED_FIELDS[@]}"; do
    if grep -q "\"$field\"" "$TEST_OUTPUT_FILE"; then
        echo -e "${GREEN}✓${NC} Field present: $field"
    else
        echo -e "${RED}✗${NC} Field missing: $field"
        MISSING_FIELDS+=("$field")
    fi
done

if [ ${#MISSING_FIELDS[@]} -gt 0 ]; then
    echo ""
    echo -e "${RED}FAIL${NC}: Missing fields: ${MISSING_FIELDS[*]}"
    rm "$TEST_OUTPUT_FILE"
    exit 1
fi
echo ""

echo "========================================"
echo "Test 4: Verify field values"
echo "========================================"

# Check exit_code matches
if grep -q "\"exit_code\": $EXIT_CODE" "$TEST_OUTPUT_FILE"; then
    echo -e "${GREEN}✓${NC} exit_code matches: $EXIT_CODE"
else
    echo -e "${RED}FAIL${NC}: exit_code does not match"
    rm "$TEST_OUTPUT_FILE"
    exit 1
fi

# Check success is true (exit code 2 = success)
if grep -q '"success": true' "$TEST_OUTPUT_FILE"; then
    echo -e "${GREEN}✓${NC} success is true"
else
    echo -e "${RED}FAIL${NC}: success should be true for exit code 2"
    rm "$TEST_OUTPUT_FILE"
    exit 1
fi

# Check role matches
if grep -q "\"role\": \"$ROLE\"" "$TEST_OUTPUT_FILE"; then
    echo -e "${GREEN}✓${NC} role matches: $ROLE"
else
    echo -e "${RED}FAIL${NC}: role does not match"
    rm "$TEST_OUTPUT_FILE"
    exit 1
fi

# Check duration matches
if grep -q "\"duration_s\": $DURATION" "$TEST_OUTPUT_FILE"; then
    echo -e "${GREEN}✓${NC} duration matches: ${DURATION}s"
else
    echo -e "${RED}FAIL${NC}: duration does not match"
    rm "$TEST_OUTPUT_FILE"
    exit 1
fi

# Check override_path matches
if grep -q "\"override_path\": \"$OVERRIDE_PATH\"" "$TEST_OUTPUT_FILE"; then
    echo -e "${GREEN}✓${NC} override_path matches: $OVERRIDE_PATH"
else
    echo -e "${RED}FAIL${NC}: override_path does not match"
    rm "$TEST_OUTPUT_FILE"
    exit 1
fi

# Check vault_path matches
if grep -q "\"vault_path\": \"$VAULT_PATH\"" "$TEST_OUTPUT_FILE"; then
    echo -e "${GREEN}✓${NC} vault_path matches: $VAULT_PATH"
else
    echo -e "${RED}FAIL${NC}: vault_path does not match"
    rm "$TEST_OUTPUT_FILE"
    exit 1
fi

echo ""

echo "========================================"
echo "Test 5: Display state file content"
echo "========================================"
echo ""
cat "$TEST_OUTPUT_FILE"
echo ""

echo "========================================"
echo "Test 6: Test failure scenario"
echo "========================================"

TEST_OUTPUT_FILE_FAIL=$(mktemp /tmp/puppet_state_test_fail.XXXXXX.json)
EXIT_CODE_FAIL=1
write_puppet_state "$WORKING_DIR" "$ROLE" "$EXIT_CODE_FAIL" "$DURATION" \
    "$OVERRIDE_PATH" "$VAULT_PATH" "$TEST_OUTPUT_FILE_FAIL"

if grep -q '"success": false' "$TEST_OUTPUT_FILE_FAIL"; then
    echo -e "${GREEN}✓${NC} Failure scenario: success is false for exit code 1"
else
    echo -e "${RED}FAIL${NC}: success should be false for exit code 1"
    rm "$TEST_OUTPUT_FILE" "$TEST_OUTPUT_FILE_FAIL"
    exit 1
fi
rm "$TEST_OUTPUT_FILE_FAIL"

echo ""
echo "========================================"
echo "Test 7: Test git dirty tracking"
echo "========================================"

# Test 7a: Verify clean repo reports git_dirty: false
if grep -q '"git_dirty": false' "$TEST_OUTPUT_FILE"; then
    echo -e "${GREEN}✓${NC} Clean repo: git_dirty is false"
else
    echo -e "${RED}FAIL${NC}: Clean repo should have git_dirty: false"
    cat "$TEST_OUTPUT_FILE"
    rm "$TEST_OUTPUT_FILE"
    exit 1
fi

# Test 7b: Make repo dirty and verify git_dirty: true
TEST_OUTPUT_FILE_DIRTY=$(mktemp /tmp/puppet_state_test_dirty.XXXXXX.json)
echo "Making repository dirty..."
(cd "$WORKING_DIR" && echo "# Modified" >> README.md)

write_puppet_state "$WORKING_DIR" "$ROLE" "$EXIT_CODE" "$DURATION" \
    "$OVERRIDE_PATH" "$VAULT_PATH" "$TEST_OUTPUT_FILE_DIRTY"

if grep -q '"git_dirty": true' "$TEST_OUTPUT_FILE_DIRTY"; then
    echo -e "${GREEN}✓${NC} Dirty repo: git_dirty is true"
else
    echo -e "${RED}FAIL${NC}: Dirty repo should have git_dirty: true"
    cat "$TEST_OUTPUT_FILE_DIRTY"
    rm "$TEST_OUTPUT_FILE" "$TEST_OUTPUT_FILE_DIRTY"
    exit 1
fi

# Test 7c: Add untracked file and verify still dirty
echo "Adding untracked file..."
(cd "$WORKING_DIR" && echo "test" > untracked.txt)

TEST_OUTPUT_FILE_UNTRACKED=$(mktemp /tmp/puppet_state_test_untracked.XXXXXX.json)
write_puppet_state "$WORKING_DIR" "$ROLE" "$EXIT_CODE" "$DURATION" \
    "$OVERRIDE_PATH" "$VAULT_PATH" "$TEST_OUTPUT_FILE_UNTRACKED"

if grep -q '"git_dirty": true' "$TEST_OUTPUT_FILE_UNTRACKED"; then
    echo -e "${GREEN}✓${NC} Untracked files: git_dirty is true"
else
    echo -e "${RED}FAIL${NC}: Repo with untracked files should have git_dirty: true"
    cat "$TEST_OUTPUT_FILE_UNTRACKED"
    rm "$TEST_OUTPUT_FILE" "$TEST_OUTPUT_FILE_DIRTY" "$TEST_OUTPUT_FILE_UNTRACKED"
    exit 1
fi

rm "$TEST_OUTPUT_FILE_DIRTY" "$TEST_OUTPUT_FILE_UNTRACKED"

echo ""
echo "========================================"
echo "All tests passed!"
echo "========================================"
echo ""
echo "Cleanup: removing test file $TEST_OUTPUT_FILE"
rm "$TEST_OUTPUT_FILE"

echo ""
echo -e "${GREEN}SUCCESS${NC}: All tests passed"
echo ""
echo "To test on production path (requires sudo):"
echo "  sudo $0"
echo ""
