# maintain-29 thread — maintenance-sync-docs-and-code

## Maintain phase (2026-07-16)

MAINTAIN run 0002. Base commit `3fb1089` (run 0001 / PR #24). 18 commits since; substantive
work was spec 27 (AIR) adding the `agy` Antigravity CLI observer, spec 25 (AIR) adding
CHANGELOG 0.1.0, and a `.codev/config.json` porch-check-alias tweak.

**Findings:**
- No new `codev/reviews/` entries (specs 25/27 ran AIR → thread narrative, no numbered
  review), so no lessons harvest.
- Only doc staleness: `arch.md` listed the four older named shims but not `agy`. Fixed
  additively in two enumerations ("Generic command observer core" + "Packaging").
- No dead code removed — Python project, no vulture/pyflakes, small + fully exercised (236
  tests, 0 skips). Same posture as run 0001.
- CLAUDE.md ≡ AGENTS.md (only intentional title/note diffs). Hot files unchanged, in sync.

**Architect guidance mid-run** (actively remove verified-stale entries, don't keep out of
caution): did a full verification pass — every code-referencing claim in all four governance
docs (backend symbols, `AI_OBSERVE_BACKENDS`, provenance fields, `CODEV_OBSERVE` alias,
`codex_observe.py` facade, pyproject scripts, viewer static assets, artifact suffixes, all
CI assertions) maps to live code. Zero stale entries → zero cuts. Recorded the verification
explicitly in run file 0002 so the no-cut result reads as verified, not cautious.

Tests green (236, 0 skips). Proceeding to porch check/done → review phase.

## Review phase (2026-07-16)

Maintain-impl 3-way: all APPROVE. PR #30 created (Closes #29). PR-type 3-way review:
gemini APPROVE, claude APPROVE, **codex REQUEST_CHANGES** — correctly caught that my first
pass scoped "documentation" too narrowly (governance docs only). The spec-27 `agy` shim was
also missing from current-state enumerations in `pyproject.toml`, `README.md`, and
`docs/observe.md`. Fixed all three additively.

Verified two flagged files are NOT stale and left them: `CHANGELOG.md [0.1.0]` and
`codev/plans/11,20` — `agy` shipped in spec 27 *after* the `v0.1.0` tag (`git cat-file -e
v0.1.0:bin/agy` fails), so those are accurate release/historical records, not staleness.
Editing them would falsify history. Codex was right on scope, wrong to lump the CHANGELOG in.

Run file 0002 updated to reflect the broader sync + the deliberate non-changes with evidence.
Tests still green (236/0).

**PR review iter2**: gemini APPROVE, claude APPROVE, codex REQUEST_CHANGES — but codex now
confirms "the agy doc sync itself looks correct." Its 3 residual points are review-record
meta-consistency, not deliverable defects: (1) it misread claude's "all APPROVE" (that refers
to the maintain-impl set, which *was* all APPROVE, not the PR reviews); (2) a self-resolving
timing artifact (iter2 files now committed); (3) its own sandbox has no `/tmp` so it couldn't
run the suite (runs clean here + via porch). Immutable consult artifacts not edited. Full
reconciliation recorded in run file 0002 "Consultation Record". Deliverable is unanimously
confirmed correct; notifying architect for the gate.
