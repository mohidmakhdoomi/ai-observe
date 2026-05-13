# Plan Iteration 1 — Rebuttals

## Reviewer verdicts
- claude (iter1): APPROVE
- codex (iter1): REQUEST_CHANGES
- gemini (iter1): skipped per user preference (memory: no gemini); not part of the 3-way review for this project

## Addressing codex iter1 KEY_ISSUES

### 1. `LiveTracer.start()` open-failure semantics not explicit
**Codex:** plan didn't say what `run()` should do if `safe_open_trace_read` or `safe_append_jsonl_handle` fails before the thread starts.

**Change:** Plan revised in Phase 2, Task 2 (`LiveTracer.start()`) and Task 3 (`run()` integration). `start()` propagates open errors; the caller in `run()` wraps the call in `try/except`, emits a single `codex-observe: warning: live tracer failed to start: ...; continuing with post-hoc-only` stderr line, discards the `LiveTracer`, and falls back to the existing Spec 1 post-hoc-only path. The wrapper never blocks Codex from launching strace because of a live-tracer setup failure.

### 2. Strict-mode "original Codex exit" message not preserved across new failure branches
**Codex:** the join-timeout and live-parser-error→fallback branches said "strict-mode applies" without saying they preserve the Spec 1 contract of printing the original Codex exit on stderr before flipping to exit 1.

**Change:** Phase 2, Task 4 (`stderr message format`) now states explicitly: every new branch that flips the exit code to 1 under `CODEV_OBSERVE_STRICT_PARSE=1` — live-parser error → fallback, `ParserFailure` from live mode, join timeout, and the double-write cascade — first emits the existing Spec 1 `codex-observe: ...; original exit {codex_code}` line. This preserves the contract that a stderr reader always sees Codex's original exit before the strict-mode override.

### 3. `test_live_parse_disabled_matches_post_hoc_only` is too weak
**Codex:** comparing final `.jsonl` contents is insufficient — live mode also converges to the same final file. The plan should require an explicit assertion that `LiveTracer` was never constructed/started when `CODEV_OBSERVE_LIVE_PARSE=0`.

**Change:** Test renamed to `test_live_parse_disabled_no_thread_started` and given an explicit **structural** assertion: the test monkeypatches the `LiveTracer` constructor (or its `start` method) on the module to record invocations into a list and asserts the list stays empty. It still also asserts `.jsonl` matches a fresh `parse_trace_file` so the regression fence is double-sided (no live thread AND output still correct).

## Outcome

All three iter1 codex KEY_ISSUES were accepted and the plan was revised accordingly. No points were disputed. The revised plan is the version currently at `codev/plans/3-stream-observe-events-in-near-.md`.
