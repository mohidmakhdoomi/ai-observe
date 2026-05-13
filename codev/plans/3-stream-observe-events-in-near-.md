# Implementation Plan: stream-observe-events-in-near-

## Overview

Two-phase delivery on the Spec 1 wrapper:

1. **Helpers + parser refactor.** Introduce the small, internal-only
   building blocks the live path needs — a canonical JSONL serializer
   shared by both writers, an incremental feed API on `TraceParser`, and
   path-hardened open helpers for the live append (write) and live tail
   (read) sides. No behavior change for end users; all 27 existing tests
   continue to pass unmodified.
2. **`LiveTracer` thread + wrapper integration.** Add the parser thread,
   wire it into `run()` behind the three new env knobs, implement the
   full failure / timeout / fallback contract, and ship a new test
   module covering every spec scenario including the end-to-end
   wrapper integration test.

A third docs-only phase updates `docs/observe.md` so users discover the
new behavior and the new env knobs. It depends on phase 2 because the
final wording must match shipped behavior.

```json
{
  "phases": [
    {
      "id": "phase-1-helpers",
      "name": "Helpers and parser refactor",
      "depends_on": []
    },
    {
      "id": "phase-2-live-tracer",
      "name": "LiveTracer thread and wrapper integration",
      "depends_on": ["phase-1-helpers"]
    },
    {
      "id": "phase-3-docs",
      "name": "Docs update",
      "depends_on": ["phase-2-live-tracer"]
    }
  ]
}
```

## Phases

### Phase 1: Helpers and parser refactor (`phase-1-helpers`)

- **Objective**: Add the internal building blocks the live path needs,
  with zero observable behavior change for end users. Existing JSONL
  output and all existing tests stay identical.
- **Files**:
  - `src/ai_observe/trace_parser.py` (modify)
  - `src/ai_observe/codex_observe.py` (modify)
  - `tests/test_trace_parser.py` (modify — add unit tests for the new
    feed API; no changes to existing tests)
  - `tests/test_codex_observe.py` (modify — add unit tests for the new
    safe-open helpers; no changes to existing tests)
- **Dependencies**: None.
- **Tasks**:
  1. **Canonical JSONL serializer.** Extract a module-level helper
     `dump_event(event: dict) -> str` in `trace_parser.py` that returns
     `json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"`.
     Update `write_jsonl` and `safe_write_jsonl` to call it. Both
     writers now share one implementation, so the spec's
     "byte-equivalent" claim is mechanically enforced.
  2. **Incremental feed API on `TraceParser`.** Add
     `feed_line(line: str) -> list[dict]` that parses one stripped
     trace line (skipping blanks) and returns the list of newly
     appended events from that call. Internally it calls the existing
     `_parse_line` and slices `self.events` from a remembered
     `_emitted` cursor. Re-raises `ParserFailure`; other exceptions
     are caught and recorded into `self.errors` (matching today's
     `parse_lines` semantics). `parse_lines` is rewritten as a thin
     loop over `feed_line` to prove equivalence in tests; behavior
     for existing callers is unchanged.
  3. **Path-hardened append helper.** Add
     `safe_append_jsonl_handle(path, observe_dir) -> file object` in
     `codex_observe.py`. Calls `verify_log_path_safe(path,
     observe_dir)`, then `os.open(path, O_WRONLY | O_APPEND |
     O_NOFOLLOW, 0o600)` and wraps the fd in
     `os.fdopen(fd, "a", encoding="utf-8")`. On any error wraps it in
     `ObserveError` (same envelope as `safe_write_jsonl`).
  4. **Path-hardened read helper.** Add
     `safe_open_trace_read(path, observe_dir) -> file object` that
     applies the same `verify_log_path_safe` check plus
     `os.open(path, O_RDONLY | O_NOFOLLOW)` and wraps it as a text
     stream with `encoding="utf-8", errors="replace"`. Closes the
     window where `.trace` could be swapped to a symlink between
     `prepare_logs` exclusive creation and the live reopen.
  5. Resolve `O_NOFOLLOW` and `O_APPEND` via `getattr(os, ..., 0)` so
     non-Linux/Windows imports still work (existing codebase
     convention — see `safe_write_jsonl`).
- **Success criteria**:
  - All 27 existing tests pass without modification:
    `python3 -m unittest discover -s tests -v` still reports 27 tests,
    1 skipped (the real-`strace` smoke test).
  - New unit tests added for `dump_event` (round-trip on a sample
    event matches the current writer output byte-for-byte),
    `feed_line` (single-line, blank-line, unfinished-then-resumed
    pair, `ParserFailure` propagation), `safe_append_jsonl_handle`
    (rejects symlink swap, opens with mode 0o600, appends without
    truncating), and `safe_open_trace_read` (rejects symlink swap).
- **Tests** (new, in existing files):
  - `test_dump_event_matches_existing_format`
  - `test_feed_line_returns_new_events_only`
  - `test_feed_line_blank_lines_ignored`
  - `test_feed_line_unfinished_resumed_pair`
  - `test_feed_line_reraises_parser_failure`
  - `test_safe_append_jsonl_handle_rejects_symlink_swap`
  - `test_safe_append_jsonl_handle_appends_without_truncate`
  - `test_safe_open_trace_read_rejects_symlink_swap`

### Phase 2: LiveTracer thread and wrapper integration (`phase-2-live-tracer`)

- **Objective**: Stream events to `.jsonl` in near real time during a
  Codex session, with bounded latency, single-writer safety, and full
  fallback to post-hoc on any non-`ParserFailure` error. Implement
  every acceptance criterion from spec §"Success criteria".
- **Files**:
  - `src/ai_observe/codex_observe.py` (modify — add `LiveTracer`,
    new env-knob parsing, integrate into `run()`)
  - `tests/test_live_trace.py` (new — covers all new scenarios)
  - `tests/test_codex_observe.py` (modify — add one wrapper-level
    end-to-end integration test using a staged fake-strace; existing
    tests untouched)
- **Dependencies**: phase-1-helpers.
- **Tasks**:
  1. **Env-knob parsing helpers** (private module functions):
     - `_live_enabled(env) -> bool`: True unless `CODEV_OBSERVE_LIVE_PARSE == "0"`.
     - `_live_poll_seconds(env) -> float`: parses
       `CODEV_OBSERVE_LIVE_POLL_MS`, clamps to [10, 2000], default 200.
       Returns seconds. Unparseable / out-of-range falls back to default.
     - `_live_join_timeout(env) -> float`: parses
       `CODEV_OBSERVE_LIVE_JOIN_TIMEOUT`, clamps to [0.1, 600], default 30.
       Unparseable / out-of-range falls back to default.
  2. **`LiveTracer` class** in `codex_observe.py`:
     - `__init__(self, trace_path, jsonl_path, observe_dir, parser, poll_seconds)`.
     - Attributes: `stop_event: threading.Event`,
       `error: BaseException | None`, `parser_failure: ParserFailure | None`,
       `thread: threading.Thread | None`.
     - `start()`: opens `.trace` via `safe_open_trace_read`, opens
       `.jsonl` via `safe_append_jsonl_handle`, then starts
       `threading.Thread(target=self._run, daemon=True)`. Either open
       can raise `ObserveError` or `OSError`. `start()` does **not**
       catch — it propagates. The caller in `run()` wraps the
       `LiveTracer.start()` call in a `try/except`: on any exception,
       print a single `codex-observe: warning: live tracer failed to
       start: ...; continuing with post-hoc-only` line to stderr,
       discard the `LiveTracer` instance, and fall back to the
       Spec 1 post-hoc-only path. Codex still runs; this never
       blocks the wrapper from launching strace.
     - `_run()`:
       - Carry a string buffer `pending`.
       - Loop:
         - `chunk = self._trace_fh.read(64 * 1024)`
         - If `chunk` non-empty: append to `pending`, split on `"\n"`,
           feed every complete line to `parser.feed_line(line)`; write
           each returned event via the shared `dump_event` + flush.
           Keep the final fragment in `pending`.
         - If `chunk` empty:
           - If `self.stop_event.is_set()`: flush `pending` to the
             parser as a final line (matching post-hoc behavior of
             yielding the last line without trailing `\n`), then break.
           - Else: `time.sleep(poll_seconds)`.
       - On `ParserFailure` (re-raised by `feed_line`): record in
         `self.parser_failure`, close fds, exit thread.
       - On any other `Exception`: record in `self.error`, close fds,
         exit thread.
       - Finally: close `.trace` and `.jsonl` handles in a `finally`
         block.
     - `request_stop()`: sets `stop_event`.
     - `join(timeout)`: joins the thread, returns
       `(timed_out: bool, error, parser_failure)`.
  3. **`run()` integration**:
     - After `prepare_logs` and before launching strace, if
       `_live_enabled(env)` is true, instantiate a fresh
       `TraceParser` (same constructor args as the post-hoc path
       already uses) and start `LiveTracer`. Capture the shared
       parser reference so the wrapper can use it to detect "live
       mode was on" later.
     - After strace finishes (after the existing `wait_for_process`
       block but before the post-hoc parse), if a `LiveTracer` is
       running:
       - Call `tracer.request_stop()`.
       - Call `tracer.join(_live_join_timeout(env))`.
       - Branch on the outcome:
         - **Clean exit, no error, no `ParserFailure`**: skip the
           post-hoc parse; live `.jsonl` is final.
         - **Clean exit, `ParserFailure`**: behave exactly like
           today's `ParserFailure` branch — write `.jsonl.partial`
           with the failure's `.events`, **and** truncate `.jsonl`
           via `safe_write_jsonl(jsonl_path, [], observe_dir)`.
           Print today's stderr message. Strict-mode rules apply.
         - **Clean exit, non-`ParserFailure` error**: print stderr
           warning naming the exception, then call the existing
           post-hoc parse + `safe_write_jsonl(jsonl_path, events,
           observe_dir)` to overwrite `.jsonl`. If that post-hoc
           parse itself raises:
           - `ParserFailure` → write `.jsonl.partial` from the
             failure's events as today; strict-mode applies.
           - other `Exception` → write empty `.jsonl.partial`,
             print stderr warning naming both errors; strict-mode
             applies.
         - **Join timed out**: print stderr warning naming the
           timeout; do **not** write `.jsonl.partial`; do **not**
           call post-hoc parse (the daemonized thread may still
           hold the `.jsonl` fd); treat as parser-failure-equivalent
           for strict-mode (exit 1 in strict, codex exit code in
           non-strict). `.jsonl` is left in whatever partial state
           the thread reached.
     - If `_live_enabled(env)` is false: existing post-hoc-only path
       is unchanged.
     - `CODEV_OBSERVE_TEST_FAIL_AFTER` continues to be passed to
       `TraceParser(fail_after_events=...)` regardless of mode; in
       live mode it raises from `feed_line` and the wrapper handles
       it via the `ParserFailure` branch above.
  4. **stderr message format**: all new warnings use the existing
     `print(f"codex-observe: ...", file=sys.stderr)` convention so
     they sit alongside Spec 1 warnings cleanly. In every branch
     that flips the exit code to `1` under
     `CODEV_OBSERVE_STRICT_PARSE=1` (live-parser error → fallback,
     `ParserFailure` from live mode, join timeout, double-write
     cascade), the wrapper **first** emits the existing Spec 1
     `f"codex-observe: ...; original exit {codex_code}"` line that
     names the original Codex exit code, mirroring the
     `safe_write_jsonl(... partial_path ...)` branches today. This
     preserves the current contract: a user reading stderr always
     sees Codex's original code before the strict-mode override.
  5. **Thread teardown safety**: the `_run` `finally` block always
     closes both handles. The main thread only opens its own
     post-hoc-fallback writer after `tracer.join()` returns cleanly,
     guaranteeing no two fds reference `.jsonl` at once.
- **Success criteria**:
  - All 27 pre-existing tests still pass without modification.
  - New tests in `tests/test_live_trace.py` cover every scenario
    from spec §"Test scenarios" and pass.
  - New end-to-end wrapper integration test in
    `tests/test_codex_observe.py` passes.
  - `python3 -m unittest discover -s tests -v` reports the new total
    test count (27 + phase-1 unit tests + phase-2 new tests), with
    the live real-`strace` test still cleanly skipping when strace
    is unavailable.
- **Tests** (new):

  In `tests/test_live_trace.py`:
  - `test_incremental_emission_visible_before_close` — feeder thread
    writes a partial trace in stages (event A, sleep, event B), the
    test snapshots `.jsonl` between stages and asserts A is present
    before B is written. Drives `LiveTracer` directly against a temp
    file.
  - `test_unfinished_resumed_pair_across_poll_boundary` — feeder
    writes `<unfinished ...>`, flushes, sleeps past
    `poll_seconds`, then writes `<... resumed>`. Asserts exactly
    one stitched event ends up in `.jsonl` and matches
    `parse_trace_file` output on the same byte stream.
  - `test_partial_trailing_line_buffered_then_emitted` — feeder
    writes a syscall line in two writes (bytes-without-newline,
    sleep, newline). Asserts no event emitted during the partial
    state, one event after the newline.
  - `test_partial_trailing_line_flushed_at_stop` — feeder writes
    a final syscall line without a trailing newline, then test sets
    `stop_event`. Asserts the event lands (matches `parse_trace_file`
    behavior on a file with no trailing newline).
  - `test_live_parser_fallback_to_post_hoc` — monkeypatch
    `feed_line` to raise a non-`ParserFailure` exception partway
    through. Drive via `run()` with a fake strace. Asserts stderr
    warning, `.jsonl` final contents equal a fresh
    `parse_trace_file` on the same `.trace`, Codex exit code
    preserved in non-strict mode.
  - `test_test_fail_after_under_live_mode_writes_partial_truncates_jsonl`
    — set `CODEV_OBSERVE_TEST_FAIL_AFTER=1`, drive via `run()` with
    a fake strace that produces ≥2 mutating syscalls. Asserts
    `.jsonl.partial` has exactly 1 event, `.jsonl` is zero bytes,
    exit code is 0 in non-strict and 1 in strict mode.
  - `test_live_parse_disabled_no_thread_started` — set
    `CODEV_OBSERVE_LIVE_PARSE=0`, drive via `run()` with a fake
    strace. Assertion is **structural**, not just content-based:
    monkeypatch the `LiveTracer` constructor (or its `start`
    method) on the module to record invocations into a list, and
    assert the list stays empty. Also assert `.jsonl` final
    contents match a fresh `parse_trace_file` of the same trace,
    so the regression fence is double-sided (no live thread AND
    output still correct).
  - `test_live_start_open_failure_falls_back_to_post_hoc` —
    monkeypatch `safe_open_trace_read` (or `safe_append_jsonl_handle`)
    to raise `ObserveError`. Drive `run()` with a fake strace.
    Assert: stderr contains the "live tracer failed to start"
    warning, `.jsonl` final contents match a fresh post-hoc parse,
    Codex exit code preserved in non-strict, exit=1 in strict mode
    (and stderr names the original Codex exit code before the
    strict override).
  - `test_empty_session_produces_empty_jsonl` — fake strace
    produces an empty trace; `.jsonl` exists and is zero bytes.
  - `test_poll_ms_env_validation` — calls `_live_poll_seconds` with
    `{"CODEV_OBSERVE_LIVE_POLL_MS": v}` for v in
    `["0", "9999", "abc", ""]` and asserts return value equals the
    default (0.200). Also one valid value (e.g. `"50"` → `0.050`).
  - `test_join_timeout_env_validation` — symmetric to the poll-ms
    test, for `_live_join_timeout`.
  - `test_live_append_helper_rejects_symlink_swap` — pre-create
    `.jsonl`, replace with a symlink, call
    `safe_append_jsonl_handle`, assert `ObserveError`, assert no
    bytes written to the symlink target. (This test belongs to
    phase 2 because it exercises the helper added in phase 1 in
    its live context, but lives in `test_live_trace.py` for
    cohesion. Could also be placed in `test_codex_observe.py`;
    builder may choose.)
  - `test_join_timeout_warns_and_preserves_partial_jsonl` —
    monkeypatch `LiveTracer` so its `_run` ignores `stop_event`
    (e.g. via a test hook). Drive `run()` with
    `CODEV_OBSERVE_LIVE_JOIN_TIMEOUT=0.2`. Assert wrapper exits
    within a few seconds, stderr contains the timeout warning,
    exit code matches Codex's in non-strict mode and is 1 in
    strict mode, no `.jsonl.partial` written, `.jsonl` is left as
    the thread had it.
  - `test_live_append_failure_falls_back_to_post_hoc` —
    monkeypatch the live writer to raise `OSError("ENOSPC")` after
    the first write. Assert: parser thread exits cleanly with
    captured error, wrapper calls post-hoc rewrite via
    `safe_write_jsonl`, final `.jsonl` matches fresh post-hoc
    parse, stderr names the live-append failure, Codex exit code
    preserved in non-strict mode.
  - `test_double_write_failure_cascade` — extend the previous test
    by also patching `safe_write_jsonl` to raise. Assert
    `.jsonl.partial` is (attempted) written, stderr names both
    failures, non-strict exit code = Codex exit code, strict mode
    exit code = 1.

  In `tests/test_codex_observe.py` (new test only — existing tests untouched):
  - `test_end_to_end_live_streaming_with_fake_strace` — extend the
    `make_fake_tools` pattern so the fake `strace` writes its trace
    in two stages with a sleep in between. Spawn the real wrapper
    binary via `run_wrapper`. While it runs, a helper thread reads
    `.jsonl` after the first stage and asserts at least one event is
    visible before the wrapper exits. After the wrapper exits,
    assert the full event set matches a `parse_trace_file` of the
    completed `.trace`. Confirms thread startup/join, env-knob
    wiring, and the full `run()` lifecycle.

### Phase 3: Docs update (`phase-3-docs`)

- **Objective**: Document live mode and the three new env knobs in
  `docs/observe.md` so users know about the new behavior and can
  opt out / tune it.
- **Files**:
  - `docs/observe.md` (modify)
- **Dependencies**: phase-2-live-tracer.
- **Tasks**:
  1. Add a short "Streaming events" section to `docs/observe.md`
     explaining that `.jsonl` grows live and that `tail -F` works.
  2. Add the three env knobs (`CODEV_OBSERVE_LIVE_PARSE`,
     `CODEV_OBSERVE_LIVE_POLL_MS`, `CODEV_OBSERVE_LIVE_JOIN_TIMEOUT`)
     to the env-knob reference with defaults, bounds, and the
     fallback-to-default rule.
  3. Note in the failure-modes section that a non-`ParserFailure`
     live-parser error triggers a stderr warning and post-hoc
     rebuild; a join timeout leaves `.jsonl` partial; both still
     preserve Codex's exit code unless `CODEV_OBSERVE_STRICT_PARSE=1`.
- **Success criteria**:
  - `docs/observe.md` mentions live streaming with `tail -F` and
    documents all three new env knobs.
  - No production code or test changes in this phase.
- **Tests**: None (docs-only). Lint-like check: confirm the doc
  builds (it's a single markdown file, so just confirm it commits
  cleanly).

## Risk Assessment

- **Risk: live writer and post-hoc writer drift in serialization
  format**, breaking the byte-equivalent claim.
  - **Mitigation**: phase 1 extracts a single `dump_event` helper used
    by both. A round-trip test (`test_dump_event_matches_existing_format`)
    pins the format byte-for-byte against the current writer's output.
- **Risk: live append helper drift from `safe_write_jsonl` hardening**
  (symlink-swap or path-escape gap).
  - **Mitigation**: phase 1 routes the live append through the same
    `verify_log_path_safe` check; phase 2 ships a parallel
    symlink-swap test.
- **Risk: thread teardown leaks fds or races the post-hoc rewrite**.
  - **Mitigation**: `_run` always closes its handles in a `finally`
    block; main thread only opens fallback writers after a successful
    `thread.join` (or never opens them on timeout). Daemon=True ensures
    a stuck thread cannot block process exit.
- **Risk: `<unfinished>`/`<resumed>` pair straddling a poll interval
  produces a duplicate or lost event**.
  - **Mitigation**: live and post-hoc paths share one `TraceParser`
    instance with its existing `self.unfinished` map. A dedicated test
    drives the pair across a poll boundary and asserts byte-equivalent
    output to `parse_trace_file`.
- **Risk: trailing partial line at EOF diverges from post-hoc
  behavior** (post-hoc reads the final line without `\n`; naïve live
  tailer would buffer it forever).
  - **Mitigation**: `_run` flushes its `pending` buffer to the parser
    as if a newline arrived once `stop_event` is set and the trace
    file returns empty.
- **Risk: env-knob parse errors raise from `run()` mid-startup**.
  - **Mitigation**: env-knob helpers swallow all parse/range errors
    and return defaults; covered by `test_poll_ms_env_validation` and
    `test_join_timeout_env_validation`.
- **Risk: test timing flakiness around `time.sleep` and inter-thread
  visibility on shared CI**.
  - **Mitigation**: tests use the smallest practical sleeps
    (`poll_seconds=0.02` etc.) and check for "at least one event"
    before completion, not for exact ordering of mid-run snapshots.
    Final assertions are deterministic (byte-equivalent to
    `parse_trace_file`).
