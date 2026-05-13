# Spec 3: Stream observe events in near real time

## Summary

Extend the Spec 1 codex wrapper so that filesystem mutation events appear in
`.codev/observe/<session>.jsonl` while Codex is still running, instead of only
after Codex exits. Users monitoring with `tail -F` (or any line-streaming
consumer) see syscall events land within roughly a second of Codex producing
them. The raw `.trace` file remains the durable source of truth, and the
existing post-hoc parse path remains intact as a fallback.

## Goals

- During a long-running Codex session, new events appear in
  `.codev/observe/<session>.jsonl` without waiting for Codex to exit.
- Sub-second latency under normal load (best effort; bounded by strace's
  default file buffering and the parser thread cadence). No hard SLA.
- Preserve the existing JSONL schema, ordering, env knobs, exit code
  semantics, and signal handling. Existing Spec 1 unit/integration tests
  must continue to pass without modification.
- Provide a safe fallback: if the live parser thread crashes for any reason,
  the wrapper rebuilds `.jsonl` from the full `.trace` after Codex exits and
  emits a stderr warning. The end-state JSONL must be byte-equivalent (or at
  worst event-equivalent, in trace order) to what the current post-hoc-only
  code produces.
- Keep the strace invocation shape (`strace -o <file> ...`) unchanged so the
  raw trace remains the canonical record on disk.

## Non-goals

- New output channels: no FIFOs, sockets, or stderr event streaming. Live
  `.jsonl` growth is the v1 mechanism.
- Streaming strace through a pipe (`-o '|cmd'`). Strace still writes to its
  file; the wrapper tails that file.
- Live `process.comm` enrichment from `/proc/<pid>/comm`.
- Changing the parser's operation mapping, event schema, ordering rules,
  or any path-resolution behavior.
- Coalescing or rate-limiting events. The parser still emits one event per
  observed mutating syscall.
- Cross-platform support beyond Linux.

## User experience

User runs Codex through the wrapper as before:

```bash
codex "implement feature"
```

While Codex is running, another terminal can stream events:

```bash
tail -F .codev/observe/<session>.jsonl
```

Lines appear shortly after Codex performs mutating syscalls. When Codex
exits, the wrapper finishes any remaining trace tail and the final `.jsonl`
matches what the post-hoc code would have written.

No new env knobs are exposed by default. Two internal/optional knobs:

- `CODEV_OBSERVE_LIVE_PARSE=0`: opt out of live tailing; fall back to the
  Spec 1 post-hoc-only behavior. Default is on (live parsing). Provided so
  operators can disable streaming if they suspect the live parser is
  perturbing a session.
- `CODEV_OBSERVE_LIVE_POLL_MS`: parser thread poll interval when the trace
  file shows no new bytes. Default `200` (ms). Lower bound `10`, upper
  bound `2000`. Out-of-range or unparseable values fall back to the default.

`CODEV_OBSERVE_STRICT_PARSE=1` behavior is unchanged: a parser failure
(live or post-hoc) still flips the exit code to 1 after the wrapper reports
Codex's original code on stderr.

## Approach

### High-level flow

1. Wrapper prepares logs and starts strace as today.
2. Wrapper spawns a **parser thread** that:
   - Opens the `.trace` file for reading.
   - Maintains a single shared `TraceParser` instance (the same class used
     post-hoc) and feeds it line by line as the file grows.
   - For each new event the parser produces, appends a JSON line to
     `.jsonl` and flushes.
3. Main thread waits on the strace process and forwards signals (unchanged
   from Spec 1).
4. After strace exits, the parser thread reads the tail of the trace until
   EOF, processes any final `<unfinished>`/`<resumed>` pairs and any
   trailing bytes, then signals completion.
5. Wrapper joins the parser thread:
   - If the thread completed cleanly, the live `.jsonl` is already the
     final output. No post-hoc re-parse is needed.
   - If the thread raised (and it was not a `ParserFailure` from the
     intentional test hook), wrapper falls back to post-hoc behavior:
     re-parse the full `.trace` with a fresh `TraceParser` and overwrite
     `.jsonl` with the result. Print a stderr warning identifying the
     live-parser error.
   - If the parser raised `ParserFailure` (the deterministic-failure test
     hook, `CODEV_OBSERVE_TEST_FAIL_AFTER`), behave exactly as today:
     write events to `.jsonl.partial` and follow strict-mode rules.

### Tailing the trace file

- Open the trace file with `open(path, "r", encoding="utf-8",
  errors="replace")` so partial multi-byte sequences at the read boundary
  are tolerated rather than fatal. (Strace writes ASCII path bytes and
  escapes non-ASCII payload, so this is conservative.)
- Read with a small buffer (e.g. `read(64 * 1024)`) and split on newline.
  Carry the trailing partial line across iterations until a `\n` arrives.
- When `read()` returns empty:
  - If strace is still running, sleep `CODEV_OBSERVE_LIVE_POLL_MS` and try
    again.
  - If strace has exited and a final read also yields empty, **flush any
    buffered trailing fragment to the parser as if a newline had
    arrived**. This matches post-hoc behavior: `parse_lines` iterates a
    file-object that yields the last line even without a terminal `\n`,
    so the live tailer must do the same to keep event sets equivalent.
    A still-open `<unfinished ...>` fragment will be safely skipped by
    the parser's existing handling.
- Never call `seek()` backwards. The parser is a forward stream.

### Writing JSONL incrementally

- The parser thread owns the open file handle for `.jsonl`. It writes one
  line per event with `fh.write(json.dumps(...) + "\n")` followed by
  `fh.flush()` so consumers tailing the file see whole lines.
- The file is pre-created during `prepare_logs` (existing Spec 1 step)
  with `O_WRONLY | O_CREAT | O_EXCL` and mode `0600`. Live mode reopens
  that same path with `O_WRONLY | O_APPEND | O_NOFOLLOW` (when available)
  through a new helper that runs the same `verify_log_path_safe`/symlink
  rejection used by `safe_write_jsonl`. This preserves the existing
  no-follow/path-escape guarantees and matches the open-hardening
  semantics of the post-hoc writer.
- Empty sessions still produce an empty `.jsonl`: the parser thread opens
  but never writes if no events are produced. Matches today's behavior.

### Stitching across read boundaries

`<unfinished>` and `<resumed>` fragments may straddle a read or even
straddle a poll interval (the unfinished line might be flushed by strace
while the resumed line is still buffered).

The existing parser already handles this within a single pass: it stashes
unfinished syscalls in `self.unfinished` keyed by `(pid, name)` and applies
them when the matching `<... resumed>` line arrives. Because the live and
post-hoc paths use the *same* `TraceParser` instance fed in trace order,
no new stitching logic is required. The only addition: ensure the line
splitter never delivers a partial line; carry the trailing fragment in the
thread's buffer until a newline arrives.

### Failure semantics

| Failure mode | Behavior |
|---|---|
| Live parser thread raises unexpected exception | Wrapper logs warning to stderr, re-parses full `.trace` with fresh parser, overwrites `.jsonl` (truncate-then-rewrite via `safe_write_jsonl`). Final state matches today. |
| Live parser raises `ParserFailure` (test hook) | Wrapper writes `.jsonl.partial` exactly as today, applies strict-mode rules. The pre-created `.jsonl` file is **truncated to zero bytes** (using the same `safe_write_jsonl` flow with an empty event list) so live-mode behavior matches the post-hoc-only contract: events live in `.jsonl.partial`, not in `.jsonl`. |
| Strace exits abnormally | Same as today: codex_code reflects strace exit; parser drains remaining trace and writes final `.jsonl`. |
| Wrapper interrupted (SIGINT/SIGTERM) | Same as today: signal forwarded to traced group; parser thread is told to stop tailing once strace exits; final drain still runs. |
| `.jsonl` write fails mid-stream (disk full, permission flip) | Wrapper catches the error, treats it as a live-parser failure, and triggers the post-hoc fallback (which will also fail to write — surfacing the underlying error). |
| `.trace` truncated/disappears mid-run | Parser tail reads return empty; thread waits for strace to exit; post-hoc re-parse will see the same (truncated) file. No special recovery. |

### Backpressure

There is no event drop and no bounded queue. The raw `.trace` is source of
truth; the parser thread is allowed to lag arbitrarily behind strace. If
the trace grows faster than the parser can consume, the lag accumulates as
unread bytes on disk, not as memory growth. Memory growth is bounded by
the parser's existing per-PID state and the single carry-over line
fragment.

### Thread join with timeout

After strace exits, the main thread sets the stop flag on `LiveTracer`
and calls `thread.join(timeout=...)`. The timeout is bounded
(default 30 s; can be tuned via `CODEV_OBSERVE_LIVE_JOIN_TIMEOUT` for
tests). If the thread fails to join within the timeout — a defensive
case that should not occur in practice because the trace file is local
and strace has exited — the wrapper treats it as a live-parser failure
(stderr warning + post-hoc fallback) and abandons the thread. This
prevents the wrapper from hanging.

### Why a thread, not a process or asyncio

- A subprocess would mean coordinating two parsers (one live, one
  post-hoc) and duplicating Python startup cost.
- asyncio would require rewriting `TraceParser` to be reentrant. The
  parser is pure CPU/IO, no networking; a single OS thread sharing memory
  with the main thread is the simplest fit and matches stdlib-only.
- Python's GIL is irrelevant here: the main thread is blocked in
  `proc.wait`/`signal.signal`, not contending for CPU.

### What changes in code

- `src/ai_observe/trace_parser.py`:
  - Add a generator-style entry point or a `feed_lines(iterable)`-like
    helper that lets a caller incrementally push lines into an existing
    `TraceParser` and read out the newly produced events. Keep the
    `parse_trace_file` API unchanged for the post-hoc path and for tests.
  - Expose access to `parser.events` so the live driver can detect
    newly-appended events after each line feed (or, alternatively, return
    a slice of new events from each `feed_lines` call).
- `src/ai_observe/codex_observe.py`:
  - Add a `LiveTracer` class (or module-level function) that owns the
    parser thread: open `.trace` for reading, open `.jsonl` for append,
    poll loop, EOF handling, exception capture, clean stop signal.
  - In `run()`, after starting strace, start `LiveTracer` in a thread.
    After strace exits, set the stop flag, join the thread, inspect its
    error state, and either accept the live `.jsonl` or fall back to a
    post-hoc parse that overwrites `.jsonl`.
  - Honor `CODEV_OBSERVE_LIVE_PARSE=0` to skip live tailing entirely
    (today's behavior).
  - Honor `CODEV_OBSERVE_LIVE_POLL_MS` for the poll interval.

### Compatibility with Spec 1 behaviors that must not regress

- Exit code: real Codex exit code still wins in non-strict mode.
- Strict mode: parser failure still flips to 1.
- Signal handling, ptrace-denied error, missing-strace error, real-codex
  resolution, recursion guard, observe-dir symlink safety, session-id
  collision suffixing, log-write artifact filter — none of these are
  touched.
- The `CODEV_OBSERVE_TEST_FAIL_AFTER=N` hook still triggers a
  `ParserFailure` after N events. Under live mode, that failure is
  raised from the parser thread; the wrapper observes it on join and
  writes `.jsonl.partial` (just like the post-hoc path does today).
- Sessions with no mutations still leave an empty `.jsonl`.

## Open questions

### Important

- **JSONL ordering when fallback fires**: when the live parser fails
  mid-stream, the partial `.jsonl` it already wrote is discarded and
  overwritten by the post-hoc re-parse. Acceptable per acceptance
  criterion 3 ("final event set matches what current code would
  produce"). Resolved: overwrite, do not append.
- **`O_APPEND` on Linux JSONL writes**: append is guaranteed atomic only
  for writes ≤ `PIPE_BUF`. Single JSONL events can exceed that for long
  syscall strings (`-s 4096`). Single-writer (one thread, append-only)
  side-steps the issue; documented here. Resolved: single writer is
  enough.
- **Test-hook semantics under live mode**: the
  `CODEV_OBSERVE_TEST_FAIL_AFTER` injection point is *inside*
  `TraceParser._parse_line`. Under live mode that raises in the parser
  thread. The wrapper must surface that as a `ParserFailure` (not as the
  generic "live parser crashed" warning) so the `.jsonl.partial` test
  contract is preserved. Resolved: catch `ParserFailure` in the thread,
  re-raise it after join.

### Nice-to-know

- Whether to expose `CODEV_OBSERVE_LIVE_PARSE`/`CODEV_OBSERVE_LIVE_POLL_MS`
  in the README. Spec says yes briefly; final wording deferred to plan.
- Whether to fsync the `.jsonl` after each line. Default `flush()` is
  enough for `tail -F`; full `fsync` is overkill and would hurt latency
  under load. Decided: `flush()` only.

## Success criteria

### Functional (MUST)

1. During a long-running Codex session (simulated in tests by a fake
   real-codex script that performs mutations spaced over time), a reader
   that opens `.codev/observe/<session>.jsonl` partway through the run
   sees events for completed syscalls before strace/codex exit.
2. A session whose traced child performs no mutating syscalls still
   produces an empty `.jsonl` after the wrapper exits.
3. If the live parser thread raises a non-`ParserFailure` exception
   mid-session, the wrapper:
   - prints a stderr warning naming the exception,
   - re-parses the full `.trace` after Codex exits,
   - overwrites `.jsonl` so its final contents equal those produced by
     the Spec 1 post-hoc-only code path on the same `.trace`,
   - preserves the Codex exit code in non-strict mode, and
   - flips exit code to 1 in `CODEV_OBSERVE_STRICT_PARSE=1` mode (after
     printing the original Codex exit code to stderr).
4. `CODEV_OBSERVE_LIVE_PARSE=0` produces the same `.jsonl` and stderr
   output as today (no live thread started).
5. `CODEV_OBSERVE_TEST_FAIL_AFTER=N` still results in `.jsonl.partial`
   containing the first N events. The `.jsonl` file (pre-created by
   `prepare_logs`) ends as a zero-byte file after the run, regardless of
   whether live mode is on or how many events the live parser had
   already streamed before the failure. Strict-mode flips exit code to
   1 as today.
6. All 27 existing tests pass without modification.

### Functional (SHOULD)

7. `<unfinished>`/`<resumed>` pairs whose two lines arrive in separate
   read iterations of the live tailer still produce a single stitched
   event matching post-hoc output.
8. A partial trailing line (no newline yet) is buffered, not parsed.
   It's parsed once its newline arrives, or skipped safely at EOF if the
   newline never lands.

### Non-functional (SHOULD)

9. Under normal interactive load (handful of mutations per second), an
   event observed by strace is visible in `.jsonl` within
   `CODEV_OBSERVE_LIVE_POLL_MS` + parser cost (target: < 1 second with
   default 200 ms poll). Not asserted in tests; documented as design
   intent.
10. Memory overhead of live mode is dominated by the existing parser's
    per-PID state. The live tailer's own buffers are O(largest single
    syscall line), which strace's `-s 4096` caps at a few KB.

### Test scenarios

A new test module `tests/test_live_trace.py` (stdlib-only) must cover:

- **Incremental emission**: a feeder writes strace lines into a temp
  trace file with delays between groups; a reader opens the live
  `.jsonl` mid-stream and confirms early events are visible before the
  feeder finishes. Implemented without spawning real strace by driving
  `LiveTracer` against a temp file the test writes to.
- **Resume across boundary**: a feeder writes the `<unfinished ...>`
  line, flushes, sleeps past a poll interval, then writes the
  `<... resumed>` line. Confirm exactly one stitched event ends up in
  `.jsonl` and matches the post-hoc result on the same input.
- **Partial trailing line**: feeder writes bytes with no newline, sleeps,
  then writes the rest plus newline. Confirm no event is emitted during
  the partial state, and one event after the newline lands.
- **Live parser fallback to post-hoc**: monkeypatch or subclass the live
  driver so its line-feed call raises a non-`ParserFailure` exception
  partway through. Confirm: stderr warning, `.jsonl` final contents
  equal to a fresh post-hoc parse of the same trace, exit code
  unchanged in non-strict mode.
- **`CODEV_OBSERVE_TEST_FAIL_AFTER` under live mode**: confirm
  `.jsonl.partial` contains exactly N events and `.jsonl` is not
  written, and that strict mode flips exit code as before.
- **`CODEV_OBSERVE_LIVE_PARSE=0`**: live thread is never started; final
  `.jsonl` matches a post-hoc-only run on the same trace.
- **`CODEV_OBSERVE_LIVE_POLL_MS` validation**: out-of-range values
  (`0`, `9999`) and non-numeric values (`abc`) fall back to the default
  (200 ms) without raising.
- **Empty session**: no mutating syscalls; `.jsonl` exists and is empty.
- **End-to-end wrapper integration**: a fake `strace` shim (extending
  the existing `make_fake_tools` pattern in `tests/test_codex_observe.py`)
  appends trace lines to its `-o` output file in stages with sleeps
  between writes, then exits. The test drives the wrapper via the real
  `bin/codex` entrypoint and asserts (a) at least one event becomes
  visible in `.jsonl` before strace exits (read mid-run from a
  subprocess background thread), and (b) the final `.jsonl` matches the
  full event set. This proves thread startup/join, env-knob wiring, and
  the full `run()` lifecycle, not just `LiveTracer` in isolation.

Existing tests in `tests/test_codex_observe.py` and
`tests/test_trace_parser.py` continue to pass untouched.

## Acceptance criteria mapping

- "Long-running session, tail -F shows events without waiting for exit":
  test scenario "Incremental emission" + success criterion 1.
- "No-mutation session still produces empty `.jsonl`": success criterion
  2 + test scenario "Empty session".
- "Live parser fallback rebuilds from `.trace`, stderr warning, final
  set matches post-hoc": success criterion 3 + test scenario "Live
  parser fallback".
- "All 27 existing unit tests still pass": success criterion 6 +
  explicit no-change rule for `tests/test_codex_observe.py` and
  `tests/test_trace_parser.py`.
- "New tests cover incremental, boundary stitching, fallback": test
  scenarios listed above.

## Constraints

- Codex-only implementation/review (continue Spec 1 convention).
- Python stdlib only.
- Linux-first; no behavior change required on non-Linux.
- No new external runtime dependencies.
