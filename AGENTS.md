# Agent Instructions

## README.md

Read README.md for any relevant information.

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
  work, suggest a git commit message that mentions any relevant beads (e.g. `Refactor draw_screen into 8 focused methods (mvp-5jc)`)
- U
- Always `br sync` before ending session


### Essential Commands

```bash
br

# CLI commands for agents (use these instead)
br ready              # Show issues ready to work (no blockers)
br list --status=open # All open issues
br show <id>          # Full issue details with dependencies
# valid types:     Task, Bug, Feature, Epic, Chore, Docs, Question.
br create -type task -priority 2 -description "Description text" "Issue Title"
br update <id> --status=in_progress
br update <id> --description="..."  # Update description
br dep add <id> <depends_on>
br dep remove <id> <depends_on>
br dep list
br close <id> --reason="Completed"
br close <id1> <id2>  # Close multiple issues at once
br sync               # Commit and push changes
```

**IMPORTANT: Never edit `.beads/*.jsonl` files directly. Always use `br` commands.**

### Workflow Pattern

1. **Start**: Run `br ready` to find actionable work
2. **Claim**: Use `br update <id> --status=in_progress`
3. **Work**: Implement the task
4. **Complete**: Use `br close <id>`
5. **Sync**: Always run `br sync` at session end

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
br sync                 # Commit beads changes
git commit -m "..."     # Commit code
br sync                 # Commit any new beads changes
git push                # Push to remote
```


<!-- end-bv-agent-instructions -->
