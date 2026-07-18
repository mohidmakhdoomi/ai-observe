# Phase 2 — Rebuttal to impl iter 2 review

Verdicts: **Gemini APPROVE**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH
confidence). Codex's remaining point accepted and fixed.

## Codex (REQUEST_CHANGES)

1. **Presence preflight ran before applicability was known (`__main__.py`).**
   *Accepted — fixed.* The runner preflit *every* `--tools` entry for PATH presence
   before scenario selection, so a requested-but-non-applicable tool would hard-fail on
   absence instead of being reported `excluded` (e.g. `--tools claude,codex --scenarios
   timeline` with codex absent). Restructured `main()` into a three-tier tool check:
   - **Unknown tool** (not `claude`/`agy`/`codex`) → always an error, scenario-independent
     (keeps `--tools nope` → exit 2 naming it; now a clearer "unknown tool(s)" message).
   - **Scenario selection happens next**, so applicability is known.
   - **Presence preflight only for tools a selected scenario actually USES**
     (`used = {t : ∃ selected scenario with t in applies_to}`); a known tool absent but
     *not* used is left to `run_suite`, which emits the explicit named `excluded` record.
     A known tool that IS used but absent still hard-fails, loud and named.

2. **`excluded` behavior wasn't covered through the real CLI/`main()` path
   (`selftest_runner.py`).** *Accepted — fixed.* Added `CliMainApplicabilityTests` that
   drive `main()` end-to-end with a monkeypatched scenario registry and a
   simulated-absent tool:
   - `--tools claude,codex --scenarios timeline` (codex absent, timeline claude-only) →
     **exit 0**, codex reported `excluded` (named) in both `--json` and the stderr
     summary, claude runs — proving the ordering fix end-to-end.
   - `--tools codex --scenarios single_write` (codex absent, scenario uses codex) →
     **exit 2**, names codex (applicable-but-absent still hard-fails).
   - `--tools nope` → exit 2, names nope (unknown-tool guard).

## Gemini / Claude (APPROVE)
No changes requested; both confirmed the oracle, runner, sealed keep-artifacts boundary,
and self-tests (now **32/32**).

## Net changes
`__main__.py`: `main()` reordered to unknown-tool check → scenario selection →
used-only presence preflight. `selftest_runner.py`: added `CliMainApplicabilityTests`
(3 end-to-end `main()` cases). Self-tests 32/32 green. No reviewer point declined.
