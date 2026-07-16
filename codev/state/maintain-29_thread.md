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
