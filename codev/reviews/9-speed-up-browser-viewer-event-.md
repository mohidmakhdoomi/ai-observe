# Review: speed-up-browser-viewer-event-

## Summary

Implemented practical performance improvements for large `ai_observe.viewer` backlogs while preserving Spec 7 client-side filter behavior and the server security posture.

The main changes are:

- `JsonlTailer` now scans read chunks linearly instead of repeatedly slicing the remaining buffer one JSONL line at a time.
- `/events` now sends sanitized backlog/live events as bounded `append_batch` SSE frames, reducing Python flushes and browser `EventSource` callbacks for large backlogs.
- The browser supports both legacy `append` frames and new `append_batch` frames, retaining events in arrival order and ingesting each exactly once.
- Selection pruning now returns early when no paths are selected, avoiding unnecessary full snapshot-tree walks on ordinary renders.
- `docs/viewer.md` now documents the performance strategy and the remaining client-side replay envelope.

## Spec Compliance

- [x] Large chunk tailer processing is linear in bytes plus lines and preserves partial trailing line behavior.
- [x] Malformed JSON, unsupported schema, truncation, inode replacement, disappearance, and shutdown warning behavior remain covered by existing tailer tests.
- [x] SSE delivery preserves per-client no-gap/no-duplicate semantics for backlog and live events.
- [x] SSE payloads remain sanitized display fields only.
- [x] Browser ingestion supports both `append` and `append_batch` and appends to the retained event buffer exactly once.
- [x] Spec 7 filter semantics remain unchanged: filtering is client-side, filter changes replay `eventBuffer`, `Show filtered` behavior remains intact, and stable-origin persistence rules are untouched.
- [x] Selection pruning has a no-selection fast path.
- [x] Documentation describes the chosen strategy and remaining limits.

## Performance Strategy

### Tailer

The previous tailer loop repeatedly replaced `self._buf` with `self._buf[nl + 1:]`, copying the shrinking remainder once per line. The new loop keeps one combined `data` buffer, advances a start index through newline positions, handles each complete line, and stores only the final incomplete fragment. This removes the effectively quadratic startup cost for large single-read backlogs.

### SSE and browser ingestion

The server now chunks event slices into bounded batches (`_APPEND_BATCH_SIZE = 512`) and emits `event: append_batch` with an array payload. A backlog around 80k events is therefore delivered in hundreds of frames rather than tens of thousands. Sparse live appends are still sent immediately as one-element batches when observed.

The browser retains compatibility with old `append` frames and adds `append_batch` handling through shared ingestion helpers. Rendering remains coalesced by the existing scheduler.

### Selection pruning

The runtime `pruneSelections()` returns immediately when `state.selectedPaths.size === 0`, clearing any stale anchor without collecting all tree paths. The pure helper also guards empty path arrays, and tests use a throwing tree to prove empty selection pruning does not walk the snapshot.

## Remaining Envelope Limits

The viewer still intentionally keeps all sanitized events in browser memory and replays that retained buffer when filters change. The documented interactive filter-replay target remains roughly `10^4` events. Larger static backlogs should start much faster after the tailer/SSE fixes, but repeated filter edits on very large retained buffers can still be limited by main-thread replay. Bounded retention, worker-based aggregation, and filter-independent indexes remain future work.

## Tests Run

- `python3 -m unittest tests.test_viewer_tailer`
- `python3 -m unittest tests.test_viewer_server tests.test_viewer_index_js`
- `python3 -m unittest tests.test_viewer_index_js tests.test_viewer_aggregator tests.test_viewer_breadcrumb tests.test_viewer_table_js tests.test_viewer_treemap`
- `python3 -m unittest tests.test_viewer_index_js tests.test_viewer_smoke_e2e`
- `python3 -m unittest discover -s tests` — passing (128 tests)

Build check is skipped by `.codev/config.json`.

## Consultation Feedback

### Specify Phase

- **Codex**: Requested more measurable performance guidance, live batching latency guidance, and clearer batching intent.
  - **Addressed**: The plan made batching a concrete preferred implementation, bounded batch sizes, and near-immediate sparse live delivery explicit.
- **Claude**: Approved; suggested bounded batch sizes and clearer wording around browser callback/JSON parse savings.
  - **Addressed**: Folded into the implementation plan and docs.

### Plan Phase

- **Codex**: Requested making the SSE protocol change independently shippable and updating/reaffirming the documented event envelope.
  - **Addressed**: Combined server and browser batch support into one phase and updated `docs/viewer.md` envelope guidance.
- **Claude**: Approved.

### Implement Phase 1: Linear tailer

- **Codex**: Approved.
- **Claude**: Approved.

### Implement Phase 2: SSE/browser batching

- **Codex**: Approved.
- **Claude**: Approved; noted dead `_send_batch()` and type annotation cleanup as non-blocking follow-ups.

### Implement Phase 3: Selection-pruning fast path

- **Codex**: Requested the fast path at the `pruneSelections()` runtime integration point and corresponding coverage.
  - **Addressed**: Added the `pruneSelections()` early return with anchor cleanup and a source-level test for the runtime guard, while retaining the behavioral throwing-tree helper test.
- **Claude**: Approved.

## Deviations from Plan

- The selection-pruning fast path is implemented both in `pruneSelections()` and defensively in `pruneSelectedPaths()` so the runtime path and helper callers avoid tree walks on empty selections.
- Static browser code was lightly minified to keep the existing 50KB static asset smoke budget passing after new helper code was added.

## Flaky Tests

No flaky tests encountered. No tests were skipped.

## Follow-up Items

- Consider removing the now-unused server `_send_batch()` helper and tightening `_send_event` typing in a cleanup change.
- Consider revisiting the 50KB static asset budget before future viewer UI work.
- Consider a future filter-independent aggregate/index design if very large traces require frequent interactive filter edits.
