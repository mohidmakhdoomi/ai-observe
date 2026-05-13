# Implementation Plan: Browser visualizer for filesystem-event JSONL

## Overview

Implement Spec 5 as a small, stdlib-only Python server plus a vanilla-JS
browser bundle, delivered in four phases. The server (`ai_observe.viewer`)
tails a `.jsonl`, parses each event, and forwards minimal event records to
the browser over Server-Sent Events. The browser keeps the per-path
aggregation, renders a linked treemap + indented table, and exposes the
three metrics (Bytes / Events / Recent) and the "Show noise" toggle.

Phasing reflects natural seams in the codebase: the server has no UI
dependency, the UI aggregation has no DOM dependency, the rendering has
no aggregation dependency. Each phase commits a runnable artifact.

## Phase machine-readable block

```json
{
  "phases": [
    {
      "id": "phase-1-server",
      "name": "Server: viewer module, JSONL tailer, SSE channel",
      "depends_on": []
    },
    {
      "id": "phase-2-ui-core",
      "name": "Browser: aggregation core, metrics, exclude filter",
      "depends_on": ["phase-1-server"]
    },
    {
      "id": "phase-3-ui-views",
      "name": "Browser: treemap + linked table, metric/noise toggles",
      "depends_on": ["phase-2-ui-core"]
    },
    {
      "id": "phase-4-docs",
      "name": "Docs + end-to-end smoke check",
      "depends_on": ["phase-3-ui-views"]
    }
  ]
}
```

## Phases

### Phase 1: Server — `ai_observe.viewer` module, JSONL tailer, SSE channel (`phase-1-server`)

- **Objective**: Stand up a stdlib-only, loopback-only HTTP server that
  tails a JSONL file and streams its lines as SSE events. No UI yet; the
  page served at `/` is a placeholder that lists received events in a
  `<pre>`. End-to-end "open a JSONL, see events arrive in the browser"
  works at the end of this phase.
- **Files (create)**:
  - `src/ai_observe/viewer/__init__.py` — empty package marker.
  - `src/ai_observe/viewer/__main__.py` — `python -m ai_observe.viewer
    <jsonl>` CLI. Argparse accepts exactly: positional `path`,
    `--port` (default 0 → OS-chosen), and `--no-browser`. There is
    **no** `--host` flag (binding is hardcoded to `127.0.0.1`, per
    spec) and **no** `--poll-ms` flag (poll interval is not
    user-tunable in v1, per spec; tests instantiate `JsonlTailer`
    directly with a constructor argument instead). The CLI validates
    that `path` exists and is a regular file (not a directory, not a
    FIFO, not a symlink to a directory) and exits non-zero with a
    clear stderr message otherwise. If `--no-browser` is absent the
    CLI calls `webbrowser.open(url)` inside a `try/except` that
    silently swallows any exception — the printed URL is the only
    contract; the auto-open is best-effort.

    **Repo execution story**: the existing test harness puts
    `src/` on `sys.path` explicitly (see `tests/test_*.py` and
    `bin/codex`). There is no packaging metadata in this repo. The
    plan therefore makes `python -m ai_observe.viewer` work by the
    same mechanism: users invoke it as
    `PYTHONPATH=src python -m ai_observe.viewer <jsonl>`, and the
    new `docs/viewer.md` documents that prefix. We do not add
    `pyproject.toml` in this spec — that's a separate maintenance
    task. Tests use the same `sys.path` hack as the rest of the
    suite.
  - `src/ai_observe/viewer/server.py` — `ViewerServer` wrapping
    `http.server.ThreadingHTTPServer` bound to `127.0.0.1` only;
    routes: `GET /` (HTML), `GET /static/...` (vendored JS/CSS),
    `GET /events` (SSE). Single-process, multi-thread (one thread per
    open SSE client). Per-client replay/live handoff is the
    explicit responsibility of this module — see below.

    **`/events` replay + live handoff (the design Codex/Claude
    flagged as under-specified)**: there is one shared
    `JsonlTailer` per server instance, started at process start. It
    appends each parsed event to a single in-memory list `events`
    under a `threading.Lock`; on each append it sets a
    `threading.Condition`. Each SSE client handler thread does:

    1. On connection, snapshot `n = len(events)` under the lock and
       send all events `[0:n]` as `event: append` SSE frames in
       order. (This is the "replay from offset 0" the spec
       requires; the snapshot point pins the watermark so we do
       not miss or duplicate events that arrive during replay.)
    2. After replay, loop: under the lock, wait on the condition
       until `len(events) > n`, then send the slice
       `events[n:len(events)]` and update `n`. Exits the loop on
       client disconnect (caught when the SSE write fails with
       `BrokenPipeError` / `ConnectionResetError`) or on shutdown
       (the server sets a `shutdown_event` that the wait checks).

    Memory growth is bounded by session size; v1 envelope is ~10⁴
    events so this is fine. Documented as a known v1 envelope in
    `docs/viewer.md`. The `events` list is the broadcaster's
    *only* mutable cross-thread state; locking it cleanly is the
    main thread-safety requirement and is unit-tested with two
    concurrent SSE clients consuming an in-flight fixture.

    **SSE payload shape (pinned, security-sensitive)**: each
    `append` frame's `data:` is a JSON object containing exactly
    the fields:
    `{ "timestamp": "<ISO8601>", "operation": "<op>",
       "path": "<abs|null>", "old_path": "<abs|null>",
       "new_path": "<abs|null>", "result": <int|null> }`.
    Notably **excluded**: `raw_syscall`, `command`, `pid`,
    `process`, `session_id`, `invocation_id`, `schema_version`.
    Those fields exist on the JSONL but the page must not see
    them per the spec's security posture. A unit test asserts the
    SSE payload's keys exactly match this whitelist for every
    event produced from a fixture containing all operation types.
  - `src/ai_observe/viewer/tailer.py` — `JsonlTailer` class. Opens the
    path read-only, tracks `(inode, offset)`, polls on a fixed
    interval (constructor argument, default 250 ms), buffers partial
    trailing lines, handles truncation and inode-changed (reopen
    from 0 with stderr warning), skips malformed-JSON lines and
    `schema_version != 1` lines with stderr warnings. Empty-file
    startup is explicitly supported: opening a zero-byte file is
    not an error; the tailer simply sits at offset 0 and waits.

    **Partial-trailing-line behavior (anti-pattern call-out)**:
    the existing `LiveTracer` in `src/ai_observe/codex_observe.py`
    flushes any pending fragment on stop. **This module must not.**
    A JSONL fragment without a final `\n` is held until either a
    newline arrives or the tailer is asked to shut down; on
    shutdown only, if a fragment is still pending, the tailer
    emits exactly one stderr warning naming the byte length of the
    held fragment. The fragment is never parsed as JSON. This
    matches the spec contract for live JSONL readers and is
    unit-tested.
  - `src/ai_observe/viewer/static/index.html` — minimal placeholder:
    just connects to `/events` and `console.log`s each event, plus
    appends raw event JSON to a `<pre>` so a human can sanity-check.
    Fixed `<title>ai_observe viewer</title>`; no path strings leak into
    `<title>` (security requirement from spec).
  - `tests/test_viewer_tailer.py` — unit tests against `JsonlTailer`
    using a fixture file that the test mutates (append, partial line,
    truncate, malformed line, schema mismatch).
  - `tests/test_viewer_server.py` — integration test: start
    `ViewerServer` on an OS-chosen port against a fixture JSONL,
    connect via `urllib.request` to `/events`, read SSE frames, assert
    each fixture event arrives in order; append more lines, assert the
    new events arrive within ~1 s.
  - `tests/fixtures/viewer/basic.jsonl` — small committed synthetic
    fixture (~30 events) covering `create`, `modify`, `delete`,
    `rename`, `chmod`, `metadata`, plus one rename-onto-existing
    (collision), one malformed line, and one `schema_version: 2` line.
    All paths are synthetic (no real user paths) per spec privacy
    posture.
- **Dependencies**: None.
- **Success criteria**:
  - `python -m ai_observe.viewer tests/fixtures/viewer/basic.jsonl
    --no-browser` starts, prints a `http://127.0.0.1:<port>/` URL on
    stderr, accepts a curl `/events` connection, and emits an SSE
    `event: append` frame per valid JSONL line.
  - Attempting to bind a non-loopback host via `--host 0.0.0.0` exits
    with a clear error (no fallback).
  - Tailer tests pass: append, partial line buffered until newline,
    truncation reopens, inode change reopens, malformed line is
    skipped with a single stderr warning, `schema_version: 2` line is
    skipped with a single stderr warning. The well-formed surrounding
    events are all delivered.
  - Server test asserts SSE ordering matches the fixture and that
    appended events arrive within a generous bound (≤2 s) under the
    250 ms default poll.
  - Ctrl-C (SIGINT) on the running server exits cleanly: no
    traceback, file handles closed, SSE clients receive a final
    `event: shutdown` frame so they stop reconnecting.
- **Test approach**: **`unittest.TestCase` style** to match the
  existing test modules (`tests/test_trace_parser.py`,
  `tests/test_live_trace.py`, `tests/test_codex_observe.py` —
  all use `unittest`, end with `unittest.main()`, and there is no
  pytest config in the repo). Tests run with the same
  `sys.path.insert(0, "src")` prelude used elsewhere. The
  integration test reads SSE frames as raw text from `urllib`,
  splitting on `\n\n` and parsing `data: <json>` lines — this also
  validates the SSE framing itself. The CLI test mocks
  `webbrowser.open` via `unittest.mock.patch` so tests do not
  spawn a browser; explicit assertion that it is called with the
  printed URL, and that an exception from it does not crash the
  CLI.

  Additional named tests this phase must include (called out
  because Codex flagged them as floating):
  - empty existing `.jsonl` (zero bytes) — server starts, page
    serves, `/events` connects, no append frames until lines are
    written;
  - nonexistent path — CLI exits non-zero with a clear message;
  - directory path — CLI exits non-zero with a clear message;
  - shutdown with a held incomplete fragment — exactly one stderr
    warning observed, no parse error, no exception;
  - two concurrent SSE clients on the same server — both receive
    the full replay independently and both receive new appended
    events in order with no gaps or duplicates.
- **Commit**: `[Spec 5][Phase: server] feat: SSE viewer server and
  JSONL tailer`.

### Phase 2: Browser aggregation core, metrics, exclude filter (`phase-2-ui-core`)

- **Objective**: Implement the browser-side data model end-to-end with
  no rendering UI yet. A small headless test harness exercises the
  three metrics, the rename/tombstone rules, the exclude filter, and
  reload-replay correctness. The page from Phase 1 grows a hidden
  `window.viewer` object exposing the aggregation for the test
  harness; the visible `<pre>` placeholder is replaced with a tiny
  JSON dump of the top-N paths by current metric so a human can
  eyeball it.
- **Files (create)**:
  - `src/ai_observe/viewer/static/aggregator.js` — pure module
    exporting `createAggregator()` returning `{ ingest(event),
    snapshot({ metric, includeNoise }), reset() }`. Maintains:
    - `paths`: Map<absPath, { bytes, events, recencyAcc, lastTouched,
      tombstoned, op_counts? }>. `op_counts` is computed but unused by
      the UI in v1; present so tests can assert correctness.
    - Per-event dispatch on `operation`. Modify/create/delete/chmod/
      metadata update the path's counters. Rename applies the
      tombstone + migration rules from the spec (Bytes move, Events
      `events(B) += events(A) + 1; events(A) := 0`, Recent state
      inherited + one fresh contribution, `last_touched = max(...)`,
      `A` tombstoned; collisions are additive into existing `B`).
    - Recency: exponential decay with a single shared half-life
      constant; chosen value `RECENCY_HALF_LIFE_MS = 60_000` (60 s).
      The accumulator is updated lazily — each path stores
      `(accValue, accAtTimestamp)` and `decay(now) = acc * 2^(-(now -
      accAt)/half_life)`. Snapshot calls `decay(latestEventTs)` so
      results are reproducible from event data alone (not wall clock).
    - Snapshot builds a directory tree on demand: it groups paths by
      `/`-split components, sums children into parent totals for
      Bytes/Events/Recent, and takes `max` over children for
      `last_touched`. Tombstoned paths are excluded.
    - Exclude filter is a separate pure function `isNoise(path)` with
      the spec's pattern list compiled to regexes once at module load.
      Snapshot honors `includeNoise`. **Event-level rule**: an event
      is counted as "noise" — and therefore excluded from
      aggregation when `includeNoise=false`, and counted into
      `filteredEventCount` — iff *every* non-null path on the event
      (`path`, `old_path`, `new_path`) matches the exclude list.
      An event touching at least one non-noise path is ingested in
      full. This matches the spec's "exclude iff *all* of its
      non-null paths match" rule, which Codex flagged as
      under-tested. A dedicated unit test feeds a mixed-path
      rename and asserts the counters.
  - `src/ai_observe/viewer/static/index.js` — bootstraps `EventSource`,
    pipes each `append` event into the aggregator, exposes the
    aggregator on `window.viewer` for testability, and (for this
    phase only) renders a `<pre>` dump of `snapshot({metric:'bytes',
    includeNoise:false}).topByBytes(20)`.
  - `src/ai_observe/viewer/static/index.html` — updated to load
    `aggregator.js` and `index.js`.
  - `tests/test_viewer_aggregator.py` — runs `aggregator.js` under
    Node (if available) **or** under a tiny pure-Python re-port: the
    aggregation logic is small enough that mirroring it in a Python
    test helper costs less than wiring a JS test runner into CI. The
    plan picks the pure-Python re-port; the JS module remains the
    canonical implementation, and a parity test feeds both the same
    fixture and asserts identical snapshots. This avoids adding a
    Node dependency to CI while still testing the real JS via a
    "reference oracle." See risks for the tradeoff.
  - `tests/fixtures/viewer/rename_chain.jsonl` — focused fixture: a
    write/rename/write chain (`/p/tmp.x` → modify → modify → rename to
    `/p/final` → modify) plus one collision case where `/p/final`
    already had prior writes.
- **Dependencies**: phase-1-server.
- **Success criteria**:
  - Bytes/Events/Recent each produce a non-trivial, deterministic
    snapshot on `basic.jsonl` and `rename_chain.jsonl` — golden
    snapshots are committed under `tests/fixtures/viewer/golden/`.
  - Rename rules verified per spec: source path tombstoned and absent
    from snapshot; destination carries migrated bytes; destination
    event count equals (source + dest + 1); collision case sums
    additively.
  - Exclude filter: with `includeNoise=false`, all `/home/*/.codex/**`
    paths in a fixture are absent; with `includeNoise=true`, they are
    present. Snapshot also reports `filteredEventCount` so the UI can
    show the "noise hidden" counter.
  - Re-connecting `/events` (server replays from offset 0) and
    re-feeding all events produces a snapshot identical to the
    single-pass run (idempotent under replay).
  - JS module is loaded by the browser without errors (smoke check
    via the Phase-1 integration test runner using a headless fetch of
    `/` to confirm the page parses and references the right static
    paths).
- **Test approach**: pure-Python parity oracle plus golden snapshots.
  No browser automation yet — the JS module is small, side-effect-free,
  and structurally identical to its Python mirror, which is exactly
  what makes the parity-test strategy honest. The mirror lives in
  `tests/_aggregator_oracle.py` and is exercised by
  `test_viewer_aggregator.py`.
- **Commit**: `[Spec 5][Phase: ui-core] feat: browser aggregator with
  three metrics, rename tombstone, exclude filter`.

### Phase 3: Browser views — treemap + linked table, toggles (`phase-3-ui-views`)

- **Objective**: Replace the `<pre>` with the real two-panel UI: a
  squarified treemap on the left, an indented sortable tree/table on
  the right, with linked selection. Add the top-bar metric toggle,
  "Show noise" toggle, live indicator + counters, and the
  WinDirStat-style **click-to-drill-down** interaction on the
  treemap. This is the user-facing phase.
- **Files (create / modify)**:
  - `src/ai_observe/viewer/static/treemap.js` — hand-rolled
    squarified-treemap layout (~150 LOC), pure function from
    `{node, width, height}` to a list of `{path, x, y, w, h, color,
    isDir}` rectangles. The layout function takes a *root node*
    (subtree) as input — drill-down is implemented by passing a
    different root, not by mutating layout state. Hand-rolled rather
    than vendored, because (a) no network fetch / no build step,
    (b) the algorithm is small and well-documented, and (c) it keeps
    the page bundle under 50 KB. The risk is layout-quality bugs;
    mitigated by a small deterministic unit test on canonical inputs.
  - `src/ai_observe/viewer/static/table.js` — indented tree/table
    renderer. Sibling-local sort (spec contract). Expand/collapse
    state held in a `Set<path>`. Preserves selection across sort and
    metric toggle. Renders rows incrementally with `documentFragment`
    to keep update batches cheap.
  - `src/ai_observe/viewer/static/style.css` — minimal layout: 50/50
    split, top bar, neutral palette by file extension (a small fixed
    map for the most common extensions, fallback gray).
  - `src/ai_observe/viewer/static/index.js` — modify: wire the
    aggregator snapshot into both renderers every 250 ms (coalesced
    rAF), wire toggle controls, wire selection (clicking either
    panel updates a shared `selectedPath` state and both renderers
    re-highlight). When sort changes or metric toggles and the
    selected row's position moves, the table calls
    `row.scrollIntoView({block: "nearest"})` — preserving "keep the
    selected row visible across sort changes" from the spec.
    **Click-to-drill-down (WinDirStat-style, REQUIRED in v1)**:

    The page maintains a `currentRoot: string` state (default
    `"/"` — i.e., the synthetic root of the tree). The treemap is
    always laid out from `currentRoot` down, *not* from the global
    root, when `currentRoot != "/"`. The table mirrors the same
    scope: when drilled down, the table shows only the subtree of
    `currentRoot`. Interactions:

    - **Click a directory rectangle**: sets `currentRoot` to that
      directory's path; both panels re-render scoped to the new
      root. Clicking a *file* rectangle selects the file (existing
      linked-selection behavior) but does not drill.
    - **"Up" control**: a button in the top bar (`▲ Up`) sets
      `currentRoot` to the parent of the current root; disabled when
      `currentRoot == "/"`.
    - **Breadcrumb**: a `/`-separated breadcrumb (`/  ›  home  ›
      user  ›  code`) appears in the top bar; each segment is a
      clickable link that jumps `currentRoot` to that ancestor. The
      leftmost segment (`/`) always resets to the global root.
    - **State preservation across drill changes**: the table's
      `expanded: Set<path>` is preserved verbatim; rows whose paths
      become out-of-scope simply aren't rendered, but their expanded
      state survives a later drill-up. `selectedPath` is preserved
      if still in scope, otherwise cleared. Metric toggle, sort
      column/direction, and "Show noise" all survive drill changes
      unchanged. Drill changes do not reset the aggregator (which is
      global to the whole session); they only change *what slice* of
      it is rendered.
    - **Empty subtree**: if `currentRoot` points at a leaf or a
      subtree with no in-scope paths under the current "Show noise"
      setting, the treemap shows an empty panel and a short message
      (`No paths under <currentRoot>`); the up control and
      breadcrumb remain functional. No crash, no NaN layout.
    - **No URL leakage**: drill state is **not** reflected in
      `document.URL` or `document.title` — both stay fixed, per the
      spec's privacy posture. Drill state is in-memory only; a page
      reload returns to `currentRoot = "/"`.

    Selection on the *table* side: clicking a directory row in the
    table still expands/collapses that row (existing behavior).
    Drill-down is a treemap-side interaction; the table doesn't
    drill on click. This avoids two competing meanings for a single
    table click and matches WinDirStat's own model (the directory
    pane is for browsing; the treemap pane is for zooming).
  - `src/ai_observe/viewer/static/index.html` — modify: real DOM
    skeleton (top bar with three toggle buttons + "Show noise"
    checkbox + live badge + event counter; `<div id="treemap">` and
    `<div id="table">`).
  - `tests/test_viewer_treemap.py` — unit test on the squarified
    treemap function: feed canonical inputs (one rectangle; two
    equal; the classic Bruls et al. example) and assert layout
    output exactly. Also covers drill-down: layout with `root="/p"`
    on a tree containing both `/p/x` and `/q/y` produces only
    rectangles under `/p`. Pure-Python oracle again, kept in
    lockstep with the JS via parity tests.
  - `tests/test_viewer_breadcrumb.py` — unit test on the breadcrumb
    derivation: `breadcrumbSegments("/a/b/c")` returns the segment
    list `[("/", "/"), ("a", "/a"), ("b", "/a/b"), ("c", "/a/b/c")]`
    and the "Up" target of `/a/b/c` is `/a/b`; "Up" target of `/` is
    `null` (control disabled). Pure-Python oracle parity.
  - `tests/test_viewer_smoke_e2e.py` — end-to-end smoke test using
    Python's `http.client`: start server, GET `/`, parse the HTML to
    confirm it references all expected static assets, GET each
    static asset and confirm it parses as valid JS/CSS/HTML (basic
    lints: no `innerHTML` of unsanitized content via a grep
    assertion, no `document.title` writes outside the fixed string).
- **Dependencies**: phase-2-ui-core.
- **Success criteria**:
  - With `basic.jsonl` and a developer running it locally, the
    treemap renders, the table renders, hovering rectangles updates
    the tooltip with path/bytes/events/timestamp (no `raw_syscall`
    in the DOM, asserted via grep test), and clicking either panel
    selects the corresponding path in the other.
  - **Drill-down**: clicking a directory rectangle re-scopes the
    treemap and table to that subtree; the breadcrumb updates;
    clicking an ancestor segment in the breadcrumb returns to that
    level; the "▲ Up" control walks one level toward `/`; reaching
    `/` disables the Up control. Clicking a *file* rectangle does
    not drill. Verified by a layout-oracle test that asserts the
    rectangle list produced from a drilled root contains only paths
    under that root and that breadcrumb segments are derived
    correctly from any input path.
  - **Drill state preservation**: under all three metric toggles,
    sort changes, and "Show noise" toggles, drilling down and back
    up returns to a layout equivalent to the original (deterministic
    given the same aggregator state). Asserted via oracle
    snapshot.
  - All three metric toggles visibly redistribute the treemap on a
    real trace; snapshot oracles confirm the underlying data
    redistribute on the synthetic fixture.
  - "Show noise" toggle: with default off, `/home/*/.codex/**` is
    hidden; turning it on reveals those rectangles. Counter shows
    the filtered event count.
  - No `document.title` writes after page load; the title remains
    `ai_observe viewer`. Static lint test asserts this.
  - Page bundle (HTML + JS + CSS) under 50 KB total.
  - Closing the browser tab does not crash the server; reopening
    re-replays.
  - Static-lint tests catch any introduction of `innerHTML =`,
    `document.write`, or `eval` in the static directory.
- **Test approach**: pure-Python parity test for the treemap layout;
  static-lint tests for security invariants; end-to-end smoke test
  via stdlib `http.client`. No Selenium / Playwright — keeping CI
  dependency-free is a project constraint (Spec 1/3 precedent).
  **The plan acknowledges that real interactive UI verification will
  be done manually by the developer on a real trace before final
  approval**; this is called out in the Review phase.
- **Commit**: `[Spec 5][Phase: ui-views] feat: treemap + table views,
  metric and noise toggles`.

### Phase 4: Docs + end-to-end smoke check (`phase-4-docs`)

- **Objective**: Document the new viewer and run a final integrated
  walkthrough on a real trace.
- **Files (create / modify)**:
  - `docs/viewer.md` (create) — invocation, metric definitions,
    exclude list, security posture (loopback only, no title/URL
    leaks, no `raw_syscall` rendering), known v1 envelope (~10⁴
    events). Mirrors the structure of `docs/observe.md`.
  - `docs/observe.md` (modify) — single cross-link to the new
    `docs/viewer.md` near the "Streaming events" section.
  - `README.md` (modify, if a project README exists) — one-line link
    to `docs/viewer.md`.
  - `codev/resources/arch.md` (modify, if it exists) — add the
    `ai_observe.viewer` subpackage to the module list with a one-line
    description.
- **Dependencies**: phase-3-ui-views.
- **Success criteria**:
  - `docs/viewer.md` exists and covers: invocation, flags, metric
    definitions, exclude list, privacy posture, fixture path for
    smoke-testing.
  - `docs/observe.md` cross-links the new doc.
  - Manual walkthrough by the developer against a real trace
    (`.codev/observe/...`) confirms: live tab updates within ~1 s of
    Codex touching files, exclude default makes the treemap
    readable, "Show noise" reveals the hidden subtree, all three
    metric toggles produce distinct layouts. Walkthrough notes are
    captured in the review document.
- **Test approach**: docs review only; the manual walkthrough is
  human-verified and recorded in the review.
- **Commit**: `[Spec 5][Phase: docs] docs: viewer documentation and
  cross-links`.

## Test strategy summary

- **CI tests** (all pure-Python, stdlib + `unittest`):
  - `test_viewer_tailer.py` — JSONL tailer behavior.
  - `test_viewer_server.py` — server + SSE end to end.
  - `test_viewer_aggregator.py` — parity vs. JS aggregator via
    Python oracle; golden snapshots; rename/tombstone/collision.
  - `test_viewer_treemap.py` — parity vs. JS squarified layout via
    Python oracle.
  - `test_viewer_smoke_e2e.py` — server starts, page loads, static
    assets parse, security-lint greps pass.
- **Local-only checks** (developer-run, not CI):
  - Manual walkthrough on a real `.jsonl` trace (Phase 4).
  - Performance budget check on the ~8800-event reference trace.

## Consultation log

### Iteration 2 (codex + claude; gemini skipped per project preference) — drill-down delta

Plan was rolled back after Iteration 1 because the architect
clarified that **WinDirStat-style click-to-drill-down on the treemap
is required for v1**. The spec is unchanged ("treemap zooms" is now
read as the drill-down requirement).

Plan revisions in this iteration are scoped to Phase 3:

- Treemap layout function now takes a root node, enabling subtree
  layout.
- Added `currentRoot` page state, click-on-directory-rectangle
  semantics, an "▲ Up" control, and a clickable breadcrumb.
- Documented state preservation rules (expanded set, selection,
  sort, metric, noise toggle all survive drill changes).
- Documented empty-subtree behavior and no-URL/no-title leakage.
- Added drill-aware unit tests (treemap scoped layout, breadcrumb
  derivation) and amended Phase 3 success criteria.

Phase 1 (server) and Phase 2 (aggregation) are unaffected; in-flight
Phase 1 code remains committed and tests still pass.

### Iteration 1 (codex + claude; gemini skipped per project preference)

- **Codex — REQUEST_CHANGES**: spec mismatches (`--host`,
  `--poll-ms`); `/events` replay+live handoff under-specified; SSE
  payload shape not pinned (risk of leaking sensitive fields);
  no repo-local execution story given the lack of packaging;
  several spec behaviors floating (empty-file startup, invalid
  input rejection, shutdown-only partial-fragment warning,
  event-level filtered-count semantics); risk of cargo-culting
  `LiveTracer`'s flush-on-stop behavior.
- **Claude — COMMENT**: pytest vs. `unittest.TestCase` mismatch
  with project convention; SSE replay-from-start mechanism not
  described in Phase 1; `--host` accepting exactly one value is
  confusing; `--poll-ms` contradicts spec; `webbrowser.open()`
  fallback path not described; thread safety of SSE broadcaster
  unmentioned.

Updates made: dropped `--host` and `--poll-ms` from the CLI; spelled
out the `/events` per-client replay-then-live design with explicit
lock + condition + watermark semantics; pinned the SSE payload to a
whitelist of six fields and added a key-exact unit test; documented
that execution uses `PYTHONPATH=src` like the rest of the repo
(no packaging change in scope); switched the test framework to
`unittest.TestCase` to match existing test modules; added named tests
for empty file, missing path, directory path, shutdown-with-fragment,
and two-concurrent-SSE-clients; called out the `LiveTracer`
flush-on-stop pattern as an anti-pattern for this module; pinned the
event-level rule for `filteredEventCount`; added explicit
`scrollIntoView` requirement; explicitly excluded treemap
zoom/drill-down from v1.

## Risk Assessment

- **JS-vs-Python aggregator parity drifts silently**:
  *Mitigation*: a single parity test runs the same fixture through
  both and asserts identical snapshots. If the oracle becomes a
  divergence trap, switch to invoking the real JS module via Node in
  a developer-only opt-in test. CI stays pure-Python by default.
- **Hand-rolled squarified treemap has layout bugs**:
  *Mitigation*: unit test against the canonical Bruls et al.
  example; fallback option is to vendor `d3-hierarchy.treemap.min.js`
  as a single static file (still no build step). Keep the algorithm
  module pure-functional so swapping is mechanical.
- **No browser-automation tests catch interaction regressions**:
  *Mitigation*: the spec calls for manual walkthrough at Review;
  static lint tests catch the highest-impact security regressions
  (innerHTML, title writes); the aggregator and layout — the parts
  that actually carry data — are unit-tested via oracles. Acceptable
  given Spec 1/3's no-extra-CI-deps precedent.
- **Sensitive paths leak via tooltips screenshotted into bug
  reports**:
  *Mitigation*: out of scope to prevent; documented in
  `docs/viewer.md` privacy section. The viewer is for the operator
  themselves; they own what they share.
- **The reference 8800-event trace isn't a committed fixture, so a
  contributor can't reproduce the performance number**:
  *Mitigation*: spec explicitly says perf budgets are local-only
  targets, not CI assertions. The committed synthetic fixture
  exercises *behavior* exhaustively; the real trace exercises
  *scale*.
- **Inode-change reopen has races on very fast rotations**:
  *Mitigation*: viewer warns on stderr and reopens from 0; observer
  never rotates, so this is purely defensive. Documented.
