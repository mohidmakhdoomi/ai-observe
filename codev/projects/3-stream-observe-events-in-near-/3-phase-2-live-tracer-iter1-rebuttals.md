# Phase 2 LiveTracer — Implementation Iteration 1 Rebuttals

## Reviewer verdicts
- codex (iter1): REQUEST_CHANGES — three real findings, all accepted
- claude (iter1): APPROVE
- gemini (iter1): skipped per user preference (memory: no gemini); stub file in place

## Addressing codex iter1 KEY_ISSUES

### 1. Strict mode did not flip exit code on live-parser non-`ParserFailure` fallback
**Codex:** `run()`'s "live error → post-hoc rebuild" branch never set `parse_failed = True`, so the final `if parse_failed and CODEV_OBSERVE_STRICT_PARSE=1: return 1` could not fire. Spec §"Success criteria" #3 explicitly requires the strict-mode exit-code flip in this branch (with the original Codex exit printed first).

**Change:** In `src/ai_observe/codex_observe.py`, the live-error branch now sets `parse_failed = True` unconditionally and the stderr warning now embeds `original exit {codex_code}` so the contract holds: non-strict preserves Codex's exit code; strict flips to 1 after the user sees the original code.

### 2. `test_live_parse_disabled_no_thread_started` did not actually verify the structural assertion
**Codex:** the test monkeypatched `codex_observe.LiveTracer` in the parent process but invoked the wrapper as a subprocess, so the child loaded a fresh, un-patched module and the `calls` list was always trivially empty.

**Change:** the test now drives `codex_observe.run()` in-process via the existing `_run_in_process` helper. The same helper is already used by the other monkeypatch-dependent tests; switching this one keeps the structural check meaningful.

### 3. Strict-mode coverage gap for the live-parser fallback branch
**Codex:** `test_live_parser_fallback_to_post_hoc` only ran with `CODEV_OBSERVE_STRICT_PARSE` unset, so the broken strict path went untested.

**Change:** the test now parameterizes over `strict in ("0", "1")` and asserts `rc == 0` for non-strict and `rc == 1` for strict, plus that stderr contains `original exit`. With fix #1 in place this passes; without it, the strict subtest fails (confirmed locally before re-running).

## Outcome

All three codex iter1 KEY_ISSUES accepted; one production fix, one test refactor, one test parameterization. All 52 tests pass.
