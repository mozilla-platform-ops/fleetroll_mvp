# Release notes workflow

Choose the next version after finishing the changes for a release. The generator
uses committed version changes in `pyproject.toml` as release boundaries. Notes
for 0.3.0, for example, include commits after the 0.2.4 bump through the 0.3.0 bump,
including the bump commit. Tags are not required. An uncommitted version edit
does not create a boundary.

1. Finish and commit the implementation and choose the release version.
2. Update the version in `pyproject.toml`, refresh `uv.lock` with `uv lock`, and
   commit the version bump. Do not bump early merely to label work in progress.
3. Generate the release draft using its version:

   ```bash
   uv run release-notes --version 0.3.0
   ```

4. Review `docs/release-notes/generated/v0.3.0.md`. Edit the feature descriptions
   for readers, highlight behavior changes and migration instructions, and
   remove the orphan-commit, stats, and Git Log sections if they are not useful
   for publication.
5. Save the reviewed notes as `docs/release-notes/v0.3.0.md` and commit them.
   Tagging, pushing, and publishing are separate release actions.

The generator creates drafts without overwriting existing files unless `--force`
is supplied. It does not update reviewed notes in this directory. Historical
reviewed notes should retain their published meaning during this model change.

## Previewing work before choosing a version

```bash
uv run release-notes
# Equivalent explicit selector:
uv run release-notes --version unreleased
# Refresh an existing preview:
uv run release-notes --version unreleased --force
```

These commands write `generated/unreleased.md`, containing commits after the
latest version bump through HEAD. An empty Unreleased draft is valid immediately
after a bump. It does not claim the bump commit or replace the latest release's
draft. The convenience script `tools/dev/create-release-notes.sh` uses this
default and forwards supplied flags; it no longer forces regeneration of all
historical drafts. `--all` and `--force` remain explicit options.

Use `uv run release-notes --debug` to inspect commit attribution. Closed beads
are attributed by their first committed closure; the same commit ranges drive
notes, coverage, and debug output. When closure evidence is missing, the generator
warns and falls back to full timestamps. Review those assignments because close
times can differ from commit times. Known closure commits outside the selected
history are not reassigned by timestamp.

## Legacy era mode

`--rolling-main` explicitly restores the previous version-era interpretation,
where changes are labeled with the version active during development.
`--no-rolling-main` explicitly selects the new default. Legacy progress drafts
use a distinct filename such as `v0.2.4-IN_PROGRESS.md` so they cannot collide
with a finalized release draft. Do not regenerate reviewed history during the
transition; use a separate `--output-dir` when comparing interpretations.
