# Rollout report display rework (mvp-fgja)

The default report answers: how are the cohorts doing, can we compare them, and
how much evidence is missing? This changes rendering only; JSON and assessment
calculations remain unchanged.

## Layout

- Header: report window and selected host count; approximate whole-pool queue
  counts immediately below, with timestamps and lookup errors in details.
- Population: `role: provisioner/pool` printed once. Add `[worker-group]` only
  when multiple groups occur in the report; always retain it in details/JSON.
  Each population contains a full-window cohort table and its shared-window comparisons.
- Cohort health: current verified hosts, completed, failed, exceptions, and unknown
  outcomes when present. Show health/sample notes without repeating outcomes or
  percentages. Mixed revisions remain a warning. Long names wrap without truncation;
  narrow terminals use labelled rows instead of a table.
- Comparison: one conclusion per pair, shared time bounds, finished tasks per side
  against the sample floor, rates/differences when available, and attribution per
  side. Collapse repeated sample and attribution warnings. Unknown failures inside
  the comparison remain visible and distinct from report-window exclusions.
  Name the pair as `master ↔ branch`; human statements describe higher rates on
  either master or the override, including tradeoffs between failures and exceptions.
  Signed rate differences are explicitly override minus master. Preserve the legacy
  directional `observed_result` JSON values for compatibility.
- Evidence limits: selected configuration coverage, excluded outcomes, query
  coverage, observation freshness, unverified configurations, and truncation/errors.
  Known out-of-scope history stays separate. Show override file presence only when
  it adds information to verified cohort membership, with application caveat.
- Details: observed outcome breakdowns, SHA/revision distributions, queue timestamps,
  full fleet membership diagnostics, exclusion reasons, failure patterns, and host
  summaries. Verbose adds individual jobs and unknown history ranges.

No clean sample wording implies that unknown work passed or that rollout is
approved. No failure, query error, or consequential evidence warning is hidden.
Routine compact-history metadata is supporting detail, not a default warning;
actual history availability/gap warnings remain visible.

## Terminal output

Wrap plain physical lines to terminal width before applying color. Use CRLF for
TTY writes so output remains aligned with ONLCR/OPOST disabled; use LF for redirected
text. Do not modify shared terminal settings. The progress line also ends in CRLF
on a TTY. Reproduce the disabled-newline-translation case under a real PTY and
check normal/narrow tmux rendering, redirected output, and unchanged terminal flags.
This reproduces a sufficient cause of the observed drift, without claiming to
identify which process changed the user's terminal state.

## Before/after artifacts

Capture one identical report fixture with the old renderer and the new renderer
in a temporary directory for review; tests cover representative evidence states,
multi-population reports, long names, detail access, and JSON invariance.
