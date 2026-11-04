# Release notes by release boundary

The generator now uses traditional release boundaries by default (mvp-d5wo).
Choose a version when preparing a release, after the implementation is finished.
Each version's notes cover commits after the previous version bump through its
own version bump, inclusive. Continue using committed changes to the version in
`pyproject.toml` as boundaries; tags are not required for this transition.

For example, after bumping and committing 0.3.0, its notes cover all work since
the 0.2.4 bump, including the new CLI hierarchy and override safety checks. This
corrects the era model, which would attribute that work to 0.2.4.

## Scope and decisions

- Make traditional boundaries the default in both `detect_version_ranges` and
  the CLI. Retain explicit `--rolling-main` for legacy era output and preserve
  `--no-rolling-main` as an accepted explicit choice of the new default.
- Label commits after the latest boundary as `Unreleased`, without attaching
  them to the last released version or guessing the next version. Keep default
  draft generation focused on this range; document how to select finalized
  version notes explicitly. The selector is `--version unreleased` and the
  filename is `unreleased.md`; its heading is `Unreleased` without a `v` prefix.
- Use the same commit boundaries for notes, debug output, commit coverage, and
  bead attribution. Prefer committed bead closure evidence to timestamps. Keep
  any fallback explicit and test same-day release boundaries so dates do not
  incorrectly move known commits between versions.
- Preserve the draft/review directory convention: generated drafts under
  `docs/release-notes/generated/`, reviewed notes under `docs/release-notes/`.
  Do not overwrite reviewed historical notes or regenerate all historical
  drafts as part of migration. The explicit all/force options remain available.
- Document the workflow: implement changes, choose and commit a version bump,
  generate that version's draft, review it, then save reviewed notes. Generation
  uses committed history; an uncommitted version edit is not a boundary.
- Update the convenience wrapper and tests to reflect the new default. Cover
  root/first release, successive bumps, non-version pyproject edits, same-day
  bumps, post-release commits, and HEAD exactly at a bump without losing the
  finalized latest version. Cover both traditional and explicit legacy modes.

The existing publication-workflow bead mvp-8v8 is related. Version bumping,
publication, tagging, and pushing are separate actions; this migration does not
publish a release automatically.

See [the release workflow](release-notes/README.md) for operator instructions.
