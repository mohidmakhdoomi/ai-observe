# Review 3: Stream observe events in near real time

## Summary

Added a live-mode parser thread to the Spec 1 wrapper. While Codex runs, the wrapper tails `.trace` from a daemon thread and appends parsed events to `.jsonl` in real time. The raw `.trace` is still the durable record; if the live parser raises (non-`ParserFailure`), the wrapper rebuilds `.jsonl` from the full trace post-hoc and warns on stderr. `tail -F .codev/observe/<session>.jsonl` now works as a live event stream.

## Spec Compliance

- [x] **MUST 1** Incremental emission visible mid-session — covered by `LiveTracerUnitTests.test_incremental_emission_visible_before_close` and end-to-end `test_end_to_end_live_streaming_with_fake_strace`.
- [x] **MUST 2** Empty session still produces empty `.jsonl` — `test_empty_session_produces_empty_jsonl`.
- [x] **MUST 3** Non-`ParserFailure` live error → stderr warning + post-hoc rebuild + Codex exit preserved in non-strict, 1 in strict (with original exit printed) — `test_live_parser_fallback_to_post_hoc` (parametric over strict).
- [x] **MUST 4** `CODEV_OBSERVE_LIVE_PARSE=0` matches Spec 1 behavior, no thread started — `test_live_parse_disabled_no_thread_started` (in-process structural assertion).
- [x] **MUST 5** `CODEV_OBSERVE_TEST_FAIL_AFTER=N` in live mode → `.jsonl.partial` has first N events, `.jsonl` truncated to zero bytes, strict flips to 1 — `test_test_fail_after_under_live_mode_writes_partial_truncates_jsonl`.
- [x] **MUST 6** All 27 existing tests pass unchanged — total now 52 (27 + 9 phase-1 unit + 16 phase-2 live + 1 phase-2 e2e wrapper).
- [x] **SHOULD 7** `<unfinished>`/`<resumed>` across poll boundary — `test_unfinished_resumed_pair_across_poll_boundary`.
- [x] **SHOULD 8** Partial trailing line buffered, then emitted or flushed at stop — `test_partial_trailing_line_buffered_then_emitted`, `test_partial_trailing_line_flushed_at_stop`.
- [x] **SHOULD 9** Live append uses `safe_append_jsonl_handle` with `verify_log_path_safe` + `O_NOFOLLOW`; symlink-swap rejection covered by `test_safe_append_jsonl_handle_rejects_symlink_swap`.
- [x] **SHOULD 10** Parser-thread join timeout warns, leaves `.jsonl` partial, no `.jsonl.partial`, strict flips to 1 — `test_join_timeout_warns_and_preserves_partial_jsonl` (parametric over strict).
- [x] Live trace read is path-hardened via `safe_open_trace_read` (`verify_log_path_safe` + `O_NOFOLLOW`).

## Deviations from Plan

None. Plan was followed phase-by-phase. Two small clarifications worth noting:

- In Phase 1, `safe_append_jsonl_handle`'s success criterion was reworded from "opens with mode 0o600" (the open call alone can't guarantee that since the file pre-exists) to "preserves 0o600 from the pre-created artifact". The test asserts the mode is unchanged after append.
- In Phase 2, the live-error fallback branch was wired to set `parse_failed = True` so strict mode flips to 1 even when the post-hoc rebuild succeeds (this matched spec MUST 3 but was initially missed in iter1; codex caught it).

## Lessons Learned

### What Went Well
- Phase-1 helper extraction (`dump_event`, `feed_line`, `safe_append_jsonl_handle`, `safe_open_trace_read`) made phase 2 a thin glue layer. The single shared `dump_event` mechanically enforces the byte-equivalent claim.
- Driving `codex_observe.run()` in-process for monkeypatch-dependent tests (via a small `_run_in_process` helper) made injection-style fault tests possible without spawning a subprocess.

### Challenges Encountered
- **Strict-mode contract slipped in iter1.** The live-error → post-hoc-rebuild branch wrote correct output but didn't flip `parse_failed`, so strict mode kept Codex's exit. Caught in 3-way review; fixed by setting `parse_failed = True` and embedding `original exit {code}` in the warning. Lesson: when a branch resolves a failure mode by recovering, still set the failure flag if spec contract demands a strict-mode override.
- **Subprocess tests don't see parent monkeypatches.** First version of `test_live_parse_disabled_no_thread_started` was inert because the wrapper ran in a child process. Switched to in-process invocation.
- **Porch checks defaulted to npm.** This is a Python project but porch's built-in `implement` checks are `npm run build` and `npm test`. Resolved by adding a `porch.checks` override to `.codev/config.json`: skip `build`, replace `tests` with `python3 -m unittest discover -s tests`.

### What Would Be Done Differently
- Add a checklist for "branches that should set `parse_failed`" up-front rather than inferring per-branch.
- Consider extracting the in-process wrapper-runner helper into a shared test utility (currently lives in `test_live_trace.py`).

## Technical Debt
- The `_run_in_process` helper duplicates the `main()` error envelope (catches `ObserveError`, prints, returns code). If we add more in-process driven tests later, lift it to a test helper module.

## Consultation Feedback

### Plan Phase

#### Codex (iter1)
- **Concern**: `LiveTracer.start()` open-failure semantics were not explicit.
  - **Addressed**: Plan revised — `start()` propagates; `run()` wraps it in try/except, warns, discards tracer, and falls back to post-hoc-only.
- **Concern**: Strict-mode "original Codex exit" message was not preserved across new failure branches.
  - **Addressed**: Plan §"stderr message format" now requires every strict-flipping branch to emit the original-exit line first.
- **Concern**: `test_live_parse_disabled_matches_post_hoc_only` was too weak (content-only).
  - **Addressed**: Test renamed `test_live_parse_disabled_no_thread_started` with structural assertion that `LiveTracer` is never constructed.

#### Claude (iter1)
- No concerns raised (APPROVE).

#### Gemini
- Skipped per user preference (memory: no gemini); stub file in place.

### Implementation Phase — Phase 1 (Helpers)

#### Codex (iter1)
- No concerns raised (APPROVE).

#### Claude (iter1)
- No concerns raised (APPROVE).

#### Gemini
- Skipped per user preference (memory: no gemini); stub file in place.

### Implementation Phase — Phase 2 (LiveTracer)

#### Codex (iter1)
- **Concern**: Strict mode did not flip exit code on live-parser non-`ParserFailure` fallback (post-hoc rebuild succeeded but `parse_failed` stayed False).
  - **Addressed**: Set `parse_failed = True` unconditionally in that branch; embedded `original exit {codex_code}` in the warning.
- **Concern**: `test_live_parse_disabled_no_thread_started` monkeypatched `LiveTracer` in the parent but ran wrapper as subprocess — patch never reached child.
  - **Addressed**: Test refactored to use in-process `_run_in_process` helper so the patch is observable.
- **Concern**: `test_live_parser_fallback_to_post_hoc` only ran non-strict, leaving the broken strict path untested.
  - **Addressed**: Parameterized the test over `strict in ("0", "1")`.

#### Claude (iter1)
- No concerns raised (APPROVE).

#### Gemini
- Skipped per user preference (memory: no gemini); stub file in place.

### Implementation Phase — Phase 3 (Docs)

#### Codex (iter1)
- No concerns raised (APPROVE).

#### Claude (iter1)
- No concerns raised (APPROVE).

#### Gemini
- Skipped per user preference (memory: no gemini); stub file in place.

## Architecture Updates

No architecture updates needed. `codev/resources/arch.md` does not exist in this repo; this change adds a single daemon-thread component inside the existing `codex_observe.py` wrapper without introducing new subsystems, data flows, or external interfaces. The JSONL schema, env-knob model, and observe-dir layout are unchanged; only three optional knobs were added and one new file (`tests/test_live_trace.py`).

## Lessons Learned Updates

No lessons-learned updates needed. `codev/resources/lessons-learned.md` does not exist in this repo. The lessons captured above are project-specific (strict-mode flag wiring in branch recoveries; subprocess vs in-process monkeypatching) and live in this review file.

## Flaky Tests

None encountered.

## Follow-up Items

- If more in-process wrapper-driven tests are added, lift `_run_in_process` to a shared `tests/support/` module.
- Consider exposing the live-mode env knobs in any future user-facing CLI help.

## Validation

```bash
python3 -m unittest discover -s tests
```

Result: 52 tests, all passing.
