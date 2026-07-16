# PR Review Rebuttal — Run 0002 (iteration 1)

**Reviews**: gemini APPROVE · claude APPROVE · **codex REQUEST_CHANGES**

Codex's iter1 REQUEST_CHANGES was **correct** — the first pass scoped "documentation" too
narrowly (governance docs only) and claimed `arch.md` was the sole staleness. Addressed by
extending the `agy` sync to every current-state shim enumeration. Point-by-point:

## Codex #1 — `pyproject.toml:39` comment omits `agy`

**Agreed. Fixed.** The `[project.scripts]` header comment enumerating the intentionally-not-
installed named shims now reads `(claude/codex/gemini/opencode/agy)`. `bin/agy` exists and
the packaging-smoke test already enforces the same non-entry-point invariant for it.

## Codex #2 — `README.md`, `docs/observe.md` still list only the older four shims

**Agreed. Fixed.** A repo-wide sweep (`grep -rl opencode`, excluding tests/state/historical
artifacts) found three current-state files stale by omission; all updated:

- `README.md`: the intro "deliberately not installed" list and the "Named shims" section's
  `bin/*` enumeration now include `agy` / `bin/agy`.
- `docs/observe.md`: the "Named shims" `bin/*` list, the `AI_OBSERVE_REAL_* → bin/*` mapping
  (added `AI_OBSERVE_REAL_AGY → bin/agy`), and the environment-variable reference table
  (added the `AI_OBSERVE_REAL_AGY` row).
- `pyproject.toml`: see #1.

**Note on `CHANGELOG.md:19` (codex flagged it too):** *not changed — verified NOT stale.*
The `## [0.1.0]` entry lists the four pre-`agy` shims because `agy` shipped in spec 27
(merge `beb59b9`) **after** the `v0.1.0` tag was cut at `77dae8b` (spec 25). `git cat-file -e
v0.1.0:bin/agy` fails, confirming `bin/agy` is not in the 0.1.0 release. Retroactively adding
`agy` to a shipped release entry would falsify release history. Same reasoning excludes the
historical `codev/plans/11,20` artifacts. This is the "release history is not current-state"
distinction, not an oversight.

## Codex #3 — `codev/maintain/0002.md` overstates the audit result

**Agreed. Fixed.** The run file's "arch.md was the only staleness" framing was corrected: the
Audit Findings now record the broader sync, the Documentation Changes Log lists all five
updated files plus the two deliberate KEEPs (CHANGELOG, plans) with reasons, and the Summary
reflects the true scope.

## Verification

- Full suite green after the changes: `python3 -m unittest discover -s tests` → **236 tests,
  0 skips, OK** (and porch's independent `tests` check passed).
- Post-fix PR-review iter2: gemini APPROVE, claude APPROVE. Codex iter2 confirms "the `agy`
  doc sync itself looks correct"; its remaining iter2 objections concern review-record
  meta-consistency (an immutable consultant artifact's wording; a self-resolving iter1/iter2
  timing gap) and its own sandbox's missing `/tmp` — none are deliverable defects. Full
  reconciliation is recorded in `codev/maintain/0002.md` → "Consultation Record".
