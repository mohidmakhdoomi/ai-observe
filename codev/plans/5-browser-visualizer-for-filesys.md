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
    <jsonl>` CLI: argparse for positional path, `--port`, `--host`
    (rejected unless equal to `127.0.0.1`; the flag exists only for
    explicit confirmation, with `127.0.0.1` as the default and only
    accepted value), `--no-browser`, `--poll-ms` (hidden, default 250).
  - `src/ai_observe/viewer/server.py` — `ViewerServer` wrapping
    `http.server.ThreadingHTTPServer` bound to `127.0.0.1` only;
    routes: `GET /` (HTML), `GET /static/...` (vendored JS/CSS),
    `GET /events` (SSE). Single-process, multi-thread (one thread per
    open SSE client).
  - `src/ai_observe/viewer/tailer.py` — `JsonlTailer` class. Opens the
    path read-only, tracks `(inode, offset)`, polls on a fixed
    interval, buffers partial trailing lines, handles truncation and
    inode-changed (reopen from 0 with stderr warning), skips
    malformed-JSON lines and `schema_version != 1` lines with stderr
    warnings. Exposes an iterator-style API; the server adapter pumps
    it onto each connected SSE client's queue.
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
- **Test approach**: `pytest` unit + integration tests as above; no
  browser yet. The integration test reads SSE frames as raw text from
  `urllib`, splitting on `\n\n` and parsing `data: <json>` lines —
  this also validates the SSE framing itself.
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
      Snapshot honors `includeNoise`.
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
  "Show noise" toggle, and live indicator + counters. This is the
  user-facing phase.
- **Files (create / modify)**:
  - `src/ai_observe/viewer/static/treemap.js` — hand-rolled
    squarified-treemap layout (~150 LOC), pure function from
    `{node, width, height}` to a list of `{path, x, y, w, h, color}`
    rectangles. Hand-rolled rather than vendored, because (a) no
    network fetch / no build step, (b) the algorithm is small and
    well-documented, and (c) it keeps the page bundle under 50 KB.
    The risk is layout-quality bugs; mitigated by a small
    deterministic unit test on canonical inputs.
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
    re-highlight).
  - `src/ai_observe/viewer/static/index.html` — modify: real DOM
    skeleton (top bar with three toggle buttons + "Show noise"
    checkbox + live badge + event counter; `<div id="treemap">` and
    `<div id="table">`).
  - `tests/test_viewer_treemap.py` — unit test on the squarified
    treemap function: feed canonical inputs (one rectangle; two
    equal; the classic Bruls et al. example) and assert layout
    output exactly. Pure-Python oracle again, kept in lockstep with
    the JS via parity tests.
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

- **CI tests** (all pure-Python, stdlib + pytest):
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
