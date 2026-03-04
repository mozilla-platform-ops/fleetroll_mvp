# Agent Instructions

## README.md

Read README.md for any relevant information.

## Python Environment

This project uses `uv` for Python environment management. Always use `uv run` prefix for Python commands:

```bash
# Running tests
uv run pytest                      # Run all tests
uv run pytest tests/test_file.py   # Run specific test file
uv run pytest tests/test_file.py -v  # Run with verbose output

# Running Python
uv run python script.py            # Run a Python script
uv run python -c "import foo"      # Run Python code directly
uv run python -m module            # Run a module
```

**NEVER** use `pytest`, `python`, `python3`, or `python -m` directly - always prefix with `uv run`.

## Import Verification

For verifying Python imports, use the safe `verify-imports` tool instead of `uv run python -c`:

```bash
# Verify module imports
uv run verify-imports fleetroll.commands.monitor

# Verify multiple modules/objects
uv run verify-imports \
  fleetroll.commands.monitor \
  fleetroll.commands.monitor.MonitorDisplay \
  fleetroll.commands.monitor.build_row_values
```

**Why use verify-imports:**
- **Safe for bulk-allow**: Only performs imports, never executes arbitrary Python code
- **Security**: Input validation prevents code injection
- **Clear output**: Shows ✓ for successful imports, errors for failures

**NEVER** use `uv run python -c "from ... import ..."` for import verification - always use `verify-imports`.

## Pre-commit Checks

This project uses `prek` (a faster alternative to `pre-commit run --all-files`) for running pre-commit hooks:

```bash
# Run all pre-commit checks
prek

# This runs:
# - ruff format/check (Python formatting and linting)
# - shellcheck (shell script linting)
# - trailing whitespace, end of files, etc.
```

**IMPORTANT**: Always use `prek` instead of `pre-commit run --all-files` for better performance.

Run `prek` before suggesting commits to ensure all checks pass. Note that `prek` may reformat files in-place, requiring a re-stage-and-rerun loop:

```bash
git add src/fleetroll/commands/monitor.py
prek                    # ruff reformats the file
git add src/fleetroll/commands/monitor.py   # re-stage formatted version
prek                    # all checks pass now
```

## Testing Workflow

When implementing features:

1. **Choose the right tool**:
   - `uv run verify-imports` — quick smoke test that new imports resolve correctly
   - `uv run pytest tests/test_specific.py -v` — test actual functionality for the module you changed
   - `uv run pytest -v` — full suite, run before suggesting a commit

2. **Test incrementally** - Run specific tests as you implement:
   ```bash
   # Test a specific function
   uv run pytest tests/test_file.py::test_function_name -v

   # Test a module
   uv run pytest tests/test_file.py -v

   # Run all tests
   uv run pytest -v
   ```

3. **Follow TDD pattern when appropriate**:
   - Write test for new functionality
   - Implement minimum code to pass test
   - Refactor if needed
   - Run full test suite

4. **Extract pure functions for testability**:
   - Complex logic → pure function → easy to test
   - Example: `compute_header_layout(left, right, width) -> int`
   - Place before class definitions or in separate modules

5. **Iterative cycle**:
   ```bash
   # 1. Implement change
   # 2. Run relevant tests
   uv run pytest tests/test_specific.py -v
   # 3. Fix any failures
   # 4. Run full test suite
   uv run pytest -v
   # 5. Stage and format
   git add <files>
   prek
   # 6. Re-stage if formatted, run prek again until it passes
   git add <files>
   prek
   ```

6. **Test secrets — avoid hex strings**:
   - `detect-secrets` flags high-entropy hex strings (e.g., `deadbeef12345678`)
   - Use obviously-fake, non-hex values instead: `test_git_sha_1234`, `test_branch_sha_5678`
   - This avoids needing `# pragma: allowlist secret` comments
   - See existing pattern in `test_monitor_render.py::test_puppet_columns_applied_healthy`

## Cass - Agent Session Search

Use `cass` to search previous Claude Code chat sessions for relevant context from past conversations.

**When to search:**
- Before starting unfamiliar work — check if it was attempted before
- When hitting an error you suspect was seen in a previous session
- When implementing a pattern that may already exist in the codebase history
- When reviewing docs like this one — search for past failures and lessons

⚠️ **Never run bare `cass` in an agent context** — it launches the interactive TUI. Always use `--robot` or `--json`.

```bash
# 1) Health check (exit 0 = OK, non-zero = rebuild index)
cass health --json || cass index --full

# 2) Search across all agent history
cass search "authentication error" --robot --limit 5 --fields minimal

# 3) View + expand a hit (use source_path/line_number from search output)
cass view /path/to/session.jsonl -n 42 --json
cass expand /path/to/session.jsonl -n 42 -C 3 --json

# 4) Discover the full machine API
cass robot-docs guide
cass robot-docs schemas
```

**Output conventions:**
- `stdout` = data only
- `stderr` = diagnostics
- `exit 0` = success


## MCP CLI Command
You have access to an `mcp-cli` CLI command for interacting with MCP servers.

The actual `mcp-cli` command to use is `bunx @wener/mcp-cli`.

**MANDATORY PREREQUISITE**
You MUST call 'mcp-cli info <server>/<tool>' BEFORE ANY 'mcp-cli call <server>/<tool>'.

Available Commands:
# STEP 1: ALWAYS CHECK SCHEMA FIRST (MANDATORY)
mcp-cli info <server>/<tool>             # REQUIRED before ANY call
# STEP 2: Only after checking schema, make the call
mcp-cli call <server>/<tool> '<json>'    # Only run AFTER mcp-cli info
mcp-cli call <server>/<tool> -           # Invoke with JSON from stdin

# Discovery commands
mcp-cli servers                          # List all connected MCP servers
mcp-cli tools [server]                   # List available tools
mcp-cli grep <pattern>                   # Search tool names and descriptions
mcp-cli resources [server]               # List MCP resources
mcp-cli read <server>/<resource>         # Read an MCP resource
mcp-cli dump request-tools               # Export tools in chat-completions format


## Git Operations

When we're ready to commit the work, stage the files and ensure tests and pre-commit passes.

The user will handle committing and pushing. When changes are ready to commit:

1. **Do NOT** run `git commit` or `git push` commands
2. **Do** prompt the user with a suggested commit message
3. The user will handle the actual git operations

Format: one summary line, then optionally 1-2 detail lines after a blank line.

Example:
```
Description of what changed:
  Refactored monitor rendering into smaller methods

Suggested commit message:
  Refactor draw_screen into focused methods

  Extract header, row, and footer rendering into separate
  methods for testability.
```


## Code Quality Practices

### Function Arguments (positional vs keyword)

- Prefer keyword arguments for readability and API stability.
- Default to **one positional argument** — the primary subject of the operation.
- A second positional is acceptable only when both args are obvious and symmetric (e.g., `copy(src, dst)`, `multiply(x, y)`, `range(start, stop)`).
- Make all other parameters **keyword-only**. Use Python's `*` to enforce this:
  ```python
  def fetch(url, *, timeout=30, retries=3, verify_ssl=True):
  ```
- Avoid positional booleans.
- Exceptions: well-known stdlib patterns, simple value objects (coordinates/colors), and functional patterns.

### Read Before Editing

- **Always read files before editing** - Understand existing code structure
- Use `Read` tool to examine context, not just the specific lines you plan to change
- Look for existing patterns and follow them

### Type Checking Issues

When `ty check` reports errors:

1. **Possibly-unresolved-reference**: Variable may not be defined in all code paths
   - Solution: Restructure control flow or initialize variable earlier
   - Example: Define variable before if/else, not just in branches

2. **Early returns help**: Return early from branches to clarify control flow

3. **Type annotations**: Add them when they clarify intent, not just for the checker

## Development Patterns

### When to Extract Functions

Extract logic into standalone functions when:
- It needs unit testing
- It has complex conditional logic
- It could be reused
- It would clarify the calling code

**Example from this codebase**:
```python
# Extracted pure function (easy to test)
def compute_header_layout(left: str, right: str, usable_width: int) -> int:
    """Compute the number of rows needed for the header."""
    if usable_width > 0 and len(left) + 1 + len(right) > usable_width:
        return 2
    return 1

# Method uses extracted function
def _draw_top_header(self, ...) -> int:
    use_two_lines = compute_header_layout(left, right, usable_width)
    # ... rest of implementation
```

### Incremental Implementation

For complex features:
1. **Start small**: Get one part working first
2. **Test each piece**: Don't wait until everything is done
3. **Commit working states**: Use git to track progress
4. **Refactor incrementally**: Small improvements, test after each

<!-- bv-agent-instructions-v1 -->

---

## Beads Workflow Integration

This project uses [beads_rust](https://github.com/Dicklesworthstone/beads_rust) for issue tracking. Issues are stored in `.beads/` and tracked in git.

### Best Practices

- **Session start**: Check `br ready` at session start to find available work
- **Starting work on a bead**: Set the bead's status to in_progress.
- **Creating new beads**: Use descriptive titles, descriptions, and set appropriate priority and type.
- **Closing beads**: Provide descriptive reasons that explain what was accomplished (see examples below)

### Essential Commands

```bash
br ready              # Show issues ready to work (no blockers)
br list --status=open # All open issues
br show <id>          # Full issue details with dependencies
# create options
# --type <TYPE>  # valid types: task, bug, feature, epic, chore, docs
# --parent <PARENT>  # parent issue (epics usually)
br create --type task --priority 2 --description "Description text" "Issue Title"
br update <id> --description="..."  # Update description, priority, type, etc
br update --claim <id> --actor <cli-agent/model> # sets assignee=actor + `status=in_progress`
br dep add <id> <depends_on>
br dep remove <id> <depends_on>
br dep list
br close <id> --reason="Completed"
br close <id1> <id2>  # Close multiple issues at once
```

**IMPORTANT: Never edit `.beads/*.jsonl` files directly. Always use `br` commands.**

### Workflow Pattern

1. **Start**: Run `br ready` to find actionable work
2. **Claim**: Use `br update <id> --status=in_progress`
3. **Work**: Implement the task
4. **Complete**: Use `br close <id>`

### Closing Beads

Provide **descriptive reasons** that explain what was accomplished:

❌ **Bad**: `br close mvp-38w --reason="Completed"`

✅ **Good**: `br close mvp-38w --reason="Implemented two-line header layout with tests. When terminal width is insufficient for left+right content, header uses two rows with automatic column header offset adjustment."`

**Good close reasons include**:
- What was implemented
- How it works (briefly)
- What was tested
- Any important architectural decisions

When files have changed during the work, suggest a git commit message that mentions the relevant bead (e.g. `Refactor draw_screen into 8 focused methods (mvp-5jc)`)

### Key Concepts

- **Dependencies**: Issues can block other issues. `br ready` shows only unblocked work.
- **Priority**: P0=critical, P1=high, P2=medium, P3=low, P4=backlog (use numbers, not words)
- **Types**: task, bug, feature, epic, chore, docs
- **Blocking**: `br dep add <issue> <depends-on>` to add dependencies

### Design Documents

When planning significant features, create a design doc in `docs/` and reference it from the bead description. This keeps beads concise while preserving detailed design decisions.

Example workflow:
1. Create `docs/feature-name.md` with full design
2. Update bead: `br update <id> --description="See \`docs/feature-name.md\` for design details"`

### Session Protocol

**Before ending any session, run this checklist:**

```bash
git status              # Check what changed
git add <files>         # Stage code changes
prek                    # Verify all checks pass
```

Then prompt the user with a suggested commit message. The user will handle `git commit` and `git push`.


<!-- end-bv-agent-instructions -->

---

## tmux TUI Testing

The `host-monitor` command runs a curses-based TUI. tmux is used as a headless
rendering surface for integration tests: launch in a detached session, read
screen state with `capture-pane`, and simulate input with `send-keys`.

### Key tmux commands

```bash
# Start a detached session (120 cols × 40 rows, run cmd)
tmux new-session -d -s <name> -x 120 -y 40 '<cmd>'

# Read current pane contents as plain text
tmux capture-pane -t <name> -p

# Read pane with ANSI escape sequences (for color assertions)
tmux capture-pane -t <name> -p -e

# Send a key or character
tmux send-keys -t <name> 'q' ''

# Resize the window
tmux resize-window -t <name> -x 80 -y 24

# Tear down the session
tmux kill-session -t <name>
```

### Poll, don't sleep

The monitor event loop has a 200 ms `getch` timeout. Never use bare
`time.sleep()` to wait for renders — use `TmuxSession.wait_for()` or
`wait_until()` instead:

```python
# Good — polls capture-pane until text appears or timeout
sess.wait_for("HOST", timeout=5.0)

# Good — polls until any predicate becomes truthy
sess.wait_until(lambda: "▶" in sess.capture(), timeout=3.0)

# Bad — brittle; may be too short or waste time
time.sleep(2)
```

### TmuxSession helper class

`tests/tmux_helpers.py` provides a context-manager class:

```python
from tests.tmux_helpers import TmuxSession

with TmuxSession(cmd="HOME=/tmp/h uv run fleetroll host-monitor hosts.txt",
                 cols=160, rows=40) as sess:
    sess.wait_for("HOST", timeout=10.0)   # wait for initial render
    sess.send_keys("?")                   # open help popup
    sess.wait_for("quit", timeout=3.0)
    sess.send_keys("q")                   # close popup / quit
```

Key methods:

| Method | Description |
|---|---|
| `capture(with_escapes=False)` | Return pane text (pass `with_escapes=True` for ANSI colors) |
| `send_keys(*keys)` | Send one or more key sequences |
| `resize(cols, rows)` | Resize the window |
| `wait_for(text, timeout)` | Poll until text appears on screen |
| `wait_until(fn, timeout)` | Poll until callable returns truthy |
| `kill()` | Kill the session (called automatically by context manager) |

### Pytest fixtures

`tests/tmux_helpers.py` also provides two fixtures:

- **`tmux_monitor_env`** — creates isolated `$HOME`, seeds the SQLite DB with
  5 hosts via `insert_host_observation()`, writes a hosts list file.  Returns
  a dict with `home`, `db_path`, `hosts_file`, and `hosts`.
- **`tmux_session`** — launches the monitor TUI, waits for `HOST` to appear,
  yields a ready `TmuxSession`, and kills it on teardown.

### Test data

`seed_test_db(db_path, hosts)` inserts one `ok=1` observation per host through
the real DB layer.  Tests use these known records to assert on rendered values
(role names, SHA prefixes, etc.).

Each test gets its own `$HOME` via `monkeypatch.setenv("HOME", ...)`, matching
the pattern in `tests/integration/conftest.py`.

### Assertions

```python
screen = sess.capture()
assert "HOST" in screen                     # text present
assert "gecko_t_linux_talos" in screen      # row data
assert "▶" in screen or "[" in screen      # scroll indicator present

# Color assertions (require with_escapes=True)
colored = sess.capture(with_escapes=True)
assert "\x1b[" in colored                   # some ANSI coloring active
```

### Running TUI tests

```bash
# Run only TUI tests
uv run pytest -m tui -v

# Run all tests except TUI (fast path)
uv run pytest -m 'not tui' -v

# Run a specific TUI test
uv run pytest tests/tui/test_monitor_tui.py::TestQuit::test_q_exits -v
```

### Marker and skip

All TUI test classes carry `@pytest.mark.tui` (applied via `pytestmark`).
Tests are automatically skipped when tmux is not installed (`skip_no_tmux`).

### Manual verification

```bash
# Seed a throw-away DB
export HOME=$(mktemp -d)

# Start a session manually
tmux new-session -d -s smoke -x 120 -y 40 \
  "uv run fleetroll host-monitor /path/to/hosts.txt"

# Inspect the rendered screen
tmux capture-pane -t smoke -p

# Kill when done
tmux kill-session -t smoke
```
