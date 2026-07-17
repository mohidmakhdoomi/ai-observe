# Phase 2 — Rebuttal to impl iter 1 review

Verdicts: **Gemini APPROVE**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH
confidence). Both of Codex's actionable points fixed; the third is a consequence of the
first plus Codex's unusually restricted sandbox.

## Codex (REQUEST_CHANGES)

1. **Missing-tool preflight was gated behind temp-dir allocation (`__main__.py`).**
   *Accepted — fixed.* Although the earlier order put artifact resolution before
   preflight (to make the `--keep-artifacts` boundary tool-independent), that resolution
   *allocated a temp dir* for the default case, so `--tools nope` depended on a writable
   temp dir. Split into a **pure** `validate_keep_artifacts()` (boundary check, no
   allocation) and `allocate_artifact_dir()` (temp/persistent creation). New `main()`
   order: **validate boundary → tool preflight → scenario select → allocate**. Now
   `--tools nope` fails loud at preflight without ever touching `tempfile`, and
   `--keep-artifacts .` is still rejected before preflight. Both robustness goals held
   simultaneously.

2. **The planned fake-tool seam wasn't truly exercised (`selftest_runner.py`).**
   *Accepted — fixed.* The prior test only drove `run_suite` with a fake scenario that
   raised `ToolUnusable`. Added `StubToolSeamTests`: it writes a stub agent
   (`#!/bin/sh; exit 0`) onto a temp `PATH`, confirms **real PATH resolution**
   (`tool_available("stubagent")` finds it exactly like a real tool), then a scenario
   invokes the stub, feeds its result through the **actual detection rule**
   (`ensure_tool_usable`), and asserts the runner renders the **loud, named `fail`**.
   This exercises the present-but-unusable path through real tool resolution + detection
   + rendering — while staying agent-, ai-observe-, and strace-free, so `--selftest`
   remains universally green (a property Codex's own sandbox depends on).

3. **"Self-tests not passing / depend on writable temp."** *Addressed via #1; the
   remainder is environmental.* The CLI missing-tool test was "failing for the wrong
   reason" in Codex's sandbox precisely because temp-dir creation crashed before
   preflight — fix #1 removes that dependency, so the missing-tool path is now robust
   without a writable temp. The remaining temp use is in tests that *legitimately* test
   temp-dir behavior (`TempDirCleanupTests`) and the keep-artifacts boundary; requiring
   a writable temp dir there is inherent to what they verify and is a reasonable
   assumption for a developer running the suite. In this environment all self-tests pass
   (now **29/29**, up from 28 with the new stub-tool seam).

## Gemini / Claude (APPROVE)
No changes requested; both confirmed the oracle, rot-proof known-bug registry, the
explicit named `excluded` path, and the sealed keep-artifacts boundary, with 28/28
(now 29/29) self-tests passing and the ACs met.

## Net changes
`__main__.py`: split `validate_keep_artifacts` / `allocate_artifact_dir`, reordered
`main()` (validate → preflight → allocate). `selftest_runner.py`: added
`StubToolSeamTests` (real-PATH stub-tool unauth seam). Self-tests: 29/29 green. No
reviewer point declined.
