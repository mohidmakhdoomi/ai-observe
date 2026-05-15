# Implementation Plan: speed-up-browser-viewer-event-

## Overview

Implement the Spec 9 performance work in four independently testable phases. The plan first removes the quadratic tailer hot path, then reduces server/SSE backlog overhead with bounded `append_batch` frames, then teaches the browser to ingest batches and avoid no-op selection tree walks, and finally updates docs/review notes with the chosen envelope and validation results.

The implementation preserves Spec 7 semantics: filtering remains client-side, the browser-retained event buffer remains the source for filter changes, `Show filtered` and tombstone behavior remain unchanged, localStorage persistence remains stable-origin-only, and the server continues to bind only to `127.0.0.1` and stream sanitized fields.

## Phase machine-readable block

```json
{
  "phases": [
    {
      "id": "phase-1-linear-tailer",
      "name": "Linear JSONL tailer chunk processing",
      "depends_on": []
    },
    {
      "id": "phase-2-sse-batching",
      "name": "Bounded SSE batch delivery with no-gap/no-duplicate tests",
      "depends_on": ["phase-1-linear-tailer"]
    },
    {
      "id": "phase-3-browser-batch-selection",
      "name": "Browser batch ingestion and selection-pruning fast path",
      "depends_on": ["phase-2-sse-batching"]
    },
    {
      "id": "phase-4-docs-regression",
      "name": "Documentation, review notes, and full regression pass",
      "depends_on": ["phase-3-browser-batch-selection"]
    }
  ]
}
```

## Phases

### Phase 1: Linear JSONL tailer chunk processing (`phase-1-linear-tailer`)

- **Objective**: Replace repeated `self._buf` slicing in `JsonlTailer._poll_once()` with linear chunk splitting that preserves trailing partial-line semantics and existing rotation/truncation behavior.
- **Files**:
  - `src/ai_observe/viewer/tailer.py` — refactor chunk processing to split complete lines in one pass while retaining only the final incomplete fragment in `self._buf`.
  - `tests/test_viewer_tailer.py` — add large-chunk, partial-fragment, and regression coverage around existing malformed/schema/truncation/rotation behavior.
- **Dependencies**: None.
- **Success Criteria**:
  - Processing a large chunk of complete JSONL events preserves event order and leaves no buffered trailing bytes.
  - A final non-newline-terminated fragment remains buffered and is emitted only after a later poll appends the newline/completion.
  - Existing warning and skip behavior remains unchanged for malformed JSON, non-object JSON, unsupported schema versions, disappeared files, truncation, inode replacement, and shutdown with incomplete fragments.
  - The implementation avoids per-line copying of the remaining unprocessed chunk; code structure is linear in bytes plus lines.
- **Tests**:
  - Run `python3 -m unittest tests.test_viewer_tailer`.
  - Include a deterministic large-chunk test using many JSONL lines in a single file read; assert exact delivered paths/results rather than brittle wall-clock timing.

### Phase 2: Bounded SSE batch delivery with no-gap/no-duplicate tests (`phase-2-sse-batching`)

- **Objective**: Reduce Python write/flush overhead and browser callback pressure by emitting bounded `append_batch` SSE frames for backlog and burst/live slices while preserving legacy `append` support and per-client no-gap/no-duplicate semantics.
- **Files**:
  - `src/ai_observe/viewer/server.py` — add batch-size constants/helpers, encode `append_batch` payloads as arrays of sanitized events, flush once per frame/batch, preserve `append` helper compatibility, and keep `shutdown` behavior unchanged.
  - `tests/test_viewer_server.py` — update SSE frame parsing helpers to read both `append` and `append_batch`, and add backlog/live exact-once tests across batch boundaries.
- **Dependencies**: Phase 1.
- **Success Criteria**:
  - Backlog delivery sends all events in original order exactly once for each SSE client.
  - Live events appended after the backlog watermark are delivered exactly once without gaps or duplicates.
  - Batch frames are bounded by a conservative maximum event count so one browser `JSON.parse` does not receive an unbounded 80k-event array.
  - Empty batches are not emitted.
  - Sparse live traffic remains near-immediate; implementation should avoid intentionally delaying a single live event just to fill a batch.
  - SSE payloads still contain only sanitized fields: `timestamp`, `operation`, `path`, `old_path`, `new_path`, and `result`.
  - Existing `shutdown` frame behavior remains intact.
- **Tests**:
  - Run `python3 -m unittest tests.test_viewer_server`.
  - Add a low test-only batch size or direct helper coverage where practical so tests verify multiple `append_batch` frames and a live event after the initial backlog.
  - Preserve or adapt existing concurrent-client tests so every client receives the same ordered event stream.

### Phase 3: Browser batch ingestion and selection-pruning fast path (`phase-3-browser-batch-selection`)

- **Objective**: Accept both single-event `append` and array-valued `append_batch` SSE frames in the browser, centralize exact-once ingestion into testable helpers, and skip full snapshot-tree selection pruning when no paths are selected.
- **Files**:
  - `src/ai_observe/viewer/static/index.js` — add exported batch/single ingestion helpers, wire `EventSource` listeners for `append` and `append_batch`, preserve event-buffer ordering and aggregator ingestion, and add a no-selected-paths fast path to selection pruning.
  - `tests/test_viewer_index_js.py` — add Node-backed tests for legacy append ingestion, batch ingestion, event buffer order, exact-once aggregator calls, malformed batch handling, and no-selection pruning behavior.
- **Dependencies**: Phase 2.
- **Success Criteria**:
  - Legacy `append` frames continue to work unchanged.
  - `append_batch` frames parse arrays of sanitized events, append them to `eventBuffer` in order, and ingest each event exactly once into the active aggregator.
  - Invalid or non-array batch payloads are ignored safely like existing malformed single-event payloads.
  - Backlog delivery produces bounded render scheduling: browser work is reduced primarily by fewer `EventSource` callbacks and fewer JSON parses; existing `scheduleRender()` coalescing remains intact.
  - `pruneSelections()` returns before collecting tree paths when `state.selectedPaths.size === 0`.
  - Existing selected-path pruning behavior remains unchanged when selections are present.
  - Filter replay from `eventBuffer`, `Show filtered`, tombstone precedence, current root, metric, sorting, expansion, live badge, and filter editor behavior are preserved.
- **Tests**:
  - Run `python3 -m unittest tests.test_viewer_index_js`.
  - Run `python3 -m unittest tests.test_viewer_aggregator tests.test_viewer_breadcrumb tests.test_viewer_table_js tests.test_viewer_treemap` to catch regressions in filter and selection helpers.

### Phase 4: Documentation, review notes, and full regression pass (`phase-4-docs-regression`)

- **Objective**: Document the chosen performance strategy, remaining practical limits, and validation results; run the final regression suite before PR review.
- **Files**:
  - `docs/viewer.md` — add/update a short performance note describing linear tailer processing, bounded SSE batching, browser support for both frame formats, and the retained client-side filter replay envelope.
  - `codev/reviews/9-speed-up-browser-viewer-event-.md` — create review notes during the Review phase with final tests, performance strategy, any remaining envelope limits, and flaky-test skips if any occur.
- **Dependencies**: Phase 3.
- **Success Criteria**:
  - Documentation explains that large backlog startup is optimized but the viewer still retains events client-side and filter changes still replay the retained buffer.
  - Review notes record the batch strategy, test commands/results, and any remaining limits or deferred work such as deeper filter-replay indexing.
  - Full test suite passes, or any unrelated pre-existing flaky tests are skipped with explicit annotations and documented under `## Flaky Tests` in the review.
  - No user-facing behavior from Spec 7 regresses.
- **Tests**:
  - Run `python3 -m unittest discover -s tests`.
  - If Node is available, verify Node-backed JS tests run as part of the suite.
  - Optionally run a manual smoke with a synthetic JSONL large enough to produce multiple SSE batches and confirm the viewer URL remains loopback-only.

## Cross-phase implementation notes

- Do not edit `codev/projects/9-speed-up-browser-viewer-event-/status.yaml` directly; porch owns project state.
- Do not use `git add .` or `git add -A`; stage files explicitly.
- Keep server changes additive and compatible: the browser must support both `append` and `append_batch`, and server helpers may retain single-event `append` for tests or future compatibility.
- Prefer small pure helpers for SSE frame chunking and browser ingestion so tests can validate behavior without fragile timing assertions.
- Avoid hard wall-clock performance assertions in CI. Use structural tests for bounded batches and exact-once delivery, plus review notes/manual measurements for practical performance claims.
- If batch size needs tuning, choose a conservative event-count constant that bounds browser JSON parse work and document it in code/review notes.
- Preserve all security/privacy constraints: loopback-only binding, sanitized fields only, no server-side filters, no path/filter logging, and no new dynamic HTML APIs.

## Risk Assessment

- **Batching changes event delivery semantics**: Mitigate with server tests that parse both frame types and assert ordered exact-once backlog and live delivery for one and multiple clients.
- **Large batch JSON.parse blocks the browser main thread**: Mitigate with a bounded maximum event count per batch and tests that force multiple batches.
- **Sparse live event latency worsens**: Mitigate by sending currently available live slices immediately rather than waiting to fill a batch.
- **Browser ingestion duplicates or reorders events**: Mitigate with pure helper tests that track event buffer order and aggregator ingest calls for single and batch frames.
- **Tailer partial-line behavior regresses**: Mitigate with existing and new tests for incomplete trailing fragments completed in later polls and shutdown warnings.
- **Selection pruning fast path skips needed cleanup**: Mitigate by applying the fast path only when no selections exist and preserving existing selected-path tests for non-empty selections.
- **Performance tests become flaky**: Mitigate by avoiding strict timing thresholds in CI and validating performance through algorithmic structure, bounded-frame tests, and documented manual/bench observations.
