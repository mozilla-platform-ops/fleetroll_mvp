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

Run `prek` before suggesting commits to ensure all checks pass.

## Cass - Agent Session Search

Use `cass` to search previous Claude Code chat sessions and extend your context with relevant information from past conversations. When working on a task, search for related topics, error messages, or implementations to learn from previous solutions and avoid repeating past mistakes.

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

## Git Operations

When we're ready to commit the work, stage the files and ensure tests and pre-commit passes.

The user will handle committing and pushing. When changes are ready to commit:

1. **Do NOT** run git commit, or git push commands
2. **Do** prompt the user with a suggested commit message (1 main line, newline, and then max 2 more lines)
4. The user will handle the actual git operations

Example:
```
Description of what changed:
  DESCRIPTION

Suggested commit message:
  Add feature X
```

## Function arguments (positional vs keyword)

- Prefer keyword arguments for readability and API stability.
- Default to **one positional argument** — the primary subject of the operation.
- A second positional is acceptable only when both args are obvious and symmetric (e.g., `copy(src, dst)`, `multiply(x, y)`, `range(start, stop)`).
- Make all other parameters **keyword-only**. Use Python's `*` to enforce this:
  ```python
  def fetch(url, *, timeout=30, retries=3, verify_ssl=True):
  ```
- Avoid positional booleans.
- Exceptions: well-known stdlib patterns, simple value objects (coordinates/colors), and functional patterns.

<!-- bv-agent-instructions-v1 -->

---

## Beads Workflow Integration

This project uses [beads_rust](https://github.com/Dicklesworthstone/beads_rust) for issue tracking. Issues are stored in `.beads/` and tracked in git.

### Best Practices

- **Session start**: Check `br ready` at session start to find available work
- **Starting work on a bead**: Set the bead's status to in_progress.
- **Creating new beads**: Use descriptive titles, descriptions, and set appropriate priority and type.
- **Closing beads**: When asked to close a bead and files have changed during the
  work, suggest a git commit message that mentions any relevant beads (e.g. `Refactor draw_screen into 8 focused methods (mvp-5jc <bead state>)`)

### Essential Commands

```bash
br

# CLI commands for agents (use these instead)
br ready              # Show issues ready to work (no blockers)
br list --status=open # All open issues
br show <id>          # Full issue details with dependencies
# create options
# --type <TYPE>  # valid types:  task, bug, feature, epic, chore
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

### Key Concepts

- **Dependencies**: Issues can block other issues. `br ready` shows only unblocked work.
- **Priority**: P0=critical, P1=high, P2=medium, P3=low, P4=backlog (use numbers, not words)
- **Types**: task, bug, feature, epic, question, docs
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
git commit -m "..."     # Commit code
git push                # Push to remote
```


<!-- end-bv-agent-instructions -->
