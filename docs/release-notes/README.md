# Release and publication workflow

Every release gets a `vX.Y.Z` Git tag and a published GitHub Release in
`mozilla-platform-ops/fleetroll_mvp`. The reviewed notes in Git are the source
for the GitHub Release body. Completing the local version bump and notes prepares
a release; publishing the GitHub Release completes it.

Choose the next version after finishing the changes for a release. The generator
uses committed version changes in `pyproject.toml` as release boundaries. Notes
for 0.3.0, for example, include commits after the 0.2.4 bump through the 0.3.0 bump,
including the bump commit. The generator uses version bump commits, rather than
tags, to calculate ranges. An uncommitted version edit does not create a boundary.

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
6. Complete the checks and publish using the steps below.

## Publish every release on GitHub

Run the full test suite and `prek` for the release, and verify both
`uv run fleetroll --version` and `uv run fr --version` report the chosen version.
The release commit must contain the version bump, lockfile, and reviewed notes.
Use the notes commit as the release commit; the annotated tag points there,
rather than at the earlier version bump. Check the commit and files before
tagging. Leave unrelated working-tree changes out of release commits.

Immediately after committing the reviewed notes, the following example publishes
0.3.0. Substitute the chosen version for each subsequent release. If unrelated
commits have been added since the notes commit, set `release_commit` to the full
SHA of the intended notes commit instead of using HEAD. For the initial 0.3.0
release, that notes commit is `7746f4c`.

```bash
release_version=0.3.0
release_commit=$(git rev-parse HEAD)

# Inspect the intended release commit and versioned files.
git show --stat "$release_commit"
git show "${release_commit}:pyproject.toml"
git show "${release_commit}:docs/release-notes/v${release_version}.md"

# Push the release commits on main, then create and push the exact release tag.
git push origin main
git tag -a "v${release_version}" "$release_commit" -m "Fleetroll ${release_version}"
git push origin "refs/tags/v${release_version}"

# Publish the reviewed notes on the upstream repository's Releases page.
gh release create "v${release_version}" \
  --repo mozilla-platform-ops/fleetroll_mvp \
  --verify-tag \
  --title "Fleetroll ${release_version}" \
  --notes-file "docs/release-notes/v${release_version}.md" \
  --latest

gh release view "v${release_version}" \
  --repo mozilla-platform-ops/fleetroll_mvp \
  --json url,tagName,isDraft,isPrerelease
```

Before publishing, ensure the local notes file matches the copy in the release
commit. Confirm the resulting URL opens the expected release and that it is
published, not a draft or prerelease. `--verify-tag` requires the pushed tag to
exist, preventing GitHub from silently creating it at the current default branch.
The release appears at
[GitHub Releases](https://github.com/mozilla-platform-ops/fleetroll_mvp/releases).

Build uploads and PyPI publication are not part of the standard workflow. GitHub
provides source archive links for the tagged commit. If publication is interrupted
after pushing the tag, inspect the existing tag and release before retrying; reuse
the intended tag rather than moving it or creating a duplicate release.

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
