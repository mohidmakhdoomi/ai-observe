# Phase 2 — Rebuttal to impl iter 3 review

Verdicts: **Gemini APPROVE**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH
confidence). Codex's point accepted and fixed.

## Codex (REQUEST_CHANGES)

1. **Silent no-op success path — a green exit with zero checks run (`__main__.py`).**
   *Accepted — fixed.* When no scenarios were discovered/selected (or none applicable),
   `run_suite` returned `[]` and the runner exited `0` — a silent green that undermines
   the loud-gating contract. Added a distinct **loud nonzero** exit:
   - `real_checks(results)` = results whose status is an actual assertion
     (pass/fail/known-bug), excluding `excluded` reports.
   - `final_exit_code`: `1` if any check failed; **`3` (EXIT_NOTHING_RUNNABLE)** if zero
     real checks ran; `0` only when ≥1 check ran and none failed.
   - `main()` prints a loud stderr message on code 3 ("no checks were run — nothing
     runnable … This is not success"). Exit codes now: `0` ran-clean, `1` failure,
     `2` usage, `3` nothing-runnable.
   This means the current Phase-2 default run (`python -m tests.agent_sessions`, no
   scenarios yet) now exits **3**, not 0 — correct: nothing is runnable until Phase 3
   wires the first scenario.

2. **Coverage gap — the zero-check path was unguarded (`selftest_runner.py`).**
   *Accepted — fixed.* Added two `main()`-level self-tests:
   `test_empty_registry_is_loud_nothing_runnable` (no scenarios → exit 3, loud message)
   and `test_all_excluded_is_loud_nothing_runnable` (only `excluded` records → exit 3).

## Gemini / Claude (APPROVE)
No changes requested; both confirmed the oracle, rot-proof gates, loud/named gating, and
the applicability ordering, with self-tests passing (now **34/34**).

## Net changes
`__main__.py`: added `real_checks` / `final_exit_code` / `EXIT_NOTHING_RUNNABLE`; `main()`
exits 3 loudly when zero real checks ran. `selftest_runner.py`: +2 nothing-runnable
tests. Self-tests 34/34 green. No reviewer point declined.
