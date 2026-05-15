# Spec 9: Speed up browser viewer event processing

## Summary

The browser viewer became noticeably slow on large local traces after Spec 7 added client-side configurable filters and retained event replay. The immediate user pain is startup/backlog processing for very large JSONL files and sluggish filter or selection interactions once the browser has ingested many events.

This feature improves practical performance while preserving the public observer JSONL schema, loopback-only server posture, sanitized SSE payloads, and Spec 7's client-side filter semantics. The primary target is to remove pathological server/tailer behavior for traces around the observed 80k-event / 409MB file and to reduce unnecessary per-event/per-render work that makes large backlogs feel slow.

## Problem

`ai_observe.viewer` is documented and tested primarily around smaller traces, but real local traces can exceed the documented envelope. One current trace, `.codev/observe/20260514T105559Z-3740-f925.jsonl`, is approximately 409MB with 82,114 schema-v1 events. For this class of input, the viewer can spend multi-tens of seconds before the browser becomes useful.

Observed local measurements indicate that pure browser/Node JavaScript aggregation of sanitized events is not the dominant bottleneck. The largest bottleneck is currently in server-side startup/backlog processing:

- `JsonlTailer._poll_once()` appends a large file chunk to `self._buf`, repeatedly finds the next newline, and slices `self._buf` one line at a time. For a large startup read this repeatedly copies the shrinking remainder and can behave effectively quadratically.
- `server.py` sends and flushes one SSE frame per event. A backlog of tens of thousands of events therefore creates tens of thousands of Python writes/flushes and browser `EventSource` callbacks.

There are also Spec-7-related browser costs:

- Filter changes fully replay `eventBuffer` into a fresh aggregator.
- Selection pruning walks the full snapshot tree on every render even when there are no selected paths.
- Filter matching may become more expensive if users configure many custom filters.

## Goals

1. Make static JSONL startup/backlog delivery noticeably faster for large local traces, especially around 80k schema-v1 events / hundreds of MB.
2. Preserve exact viewer behavior for existing event schema fields, aggregation metrics, filter semantics, tombstone precedence, and UI-visible counts.
3. Preserve server privacy/security posture: bind only to `127.0.0.1`, stream only sanitized fields, avoid server-side filter persistence or path logging.
4. Reduce unnecessary browser work during backlog ingestion and renders.
5. Add targeted tests that protect the performance-sensitive behavior without making the test suite brittle or hardware-dependent.

## Non-goals

- Changing the public JSONL schema.
- Server-side filtering, server-side path persistence, or URL/query-based filter state.
- Removing the browser-retained `eventBuffer` source of truth for filter changes.
- Weakening Spec 7 behavior for `Show filtered`, tombstone precedence, all-paths-match filtered event counts, or stable-origin localStorage rules.
- Building a fully virtualized/worker-thread browser renderer unless needed by a future feature.
- Guaranteeing unlimited trace size support. The viewer may still document a practical envelope, but it must not exhibit avoidable pathological behavior at the observed 80k-event scale.

## Stakeholders

- Developers and maintainers opening large `ai_observe` traces to debug file-system activity.
- Users relying on Spec 7 filters to hide project-specific noisy paths without reconnecting or reloading.
- Maintainers of viewer tests and Python/JavaScript aggregation parity.

## Current state

### Tailer

- `JsonlTailer` tails a JSONL file from offset 0 at startup and then polls appended bytes.
- It buffers a trailing partial line and warns about it on shutdown.
- It skips malformed JSON, non-object JSON, and events with unsupported `schema_version`.
- It detects truncation and inode replacement and reopens from offset 0.
- It sanitizes events before handing them to the server broadcaster.
- Large complete chunks are currently split by repeatedly slicing `self._buf`.

### Server/SSE

- `ViewerServer` binds to `127.0.0.1` only.
- `_Broadcaster` stores sanitized events in memory and lets SSE clients snapshot `len(events)`, send backlog `[0:n)`, then wait for appended events `[n:new_end)`.
- Existing SSE payload type is `event: append` with one sanitized event object per frame.
- No-gaps/no-duplicates semantics are provided by snapshotting a watermark and then continuing from that index.
- `_send_event()` flushes every frame.

### Browser

- The browser creates an `EventSource('/events')` and handles `append` frames by parsing one event, pushing it into `eventBuffer`, ingesting it into the active aggregator, and scheduling a render.
- `eventBuffer` is retained in arrival order and replayed on every filter-list change.
- Filtering is fully client-side.
- The browser currently has helpers and tests for selection pruning, filter pattern normalization, and event replay.
- Selection pruning collects all paths from the snapshot tree even when `state.selectedPaths` is empty.

## Required behavior

### Tailer performance and correctness

- MUST process large startup chunks in linear time relative to bytes read plus number of lines, avoiding repeated copies of the unprocessed remainder.
- MUST preserve existing partial trailing line behavior: incomplete final fragments stay buffered until a newline arrives and are warned about exactly once on shutdown if still incomplete.
- MUST preserve malformed-line, non-object, unsupported-schema, truncation, disappearance, and inode-rotation behavior.
- MUST preserve event ordering within a chunk and across polls.
- MUST continue to invoke the consumer callback only with sanitized schema-v1 event dictionaries.
- SHOULD keep memory usage proportional to the currently read chunk plus any trailing partial line, not to repeated temporary full-buffer copies.

### Server delivery semantics

- MUST preserve no-gaps/no-duplicates delivery for each SSE client:
  - every client receives a backlog snapshot from event index 0 through the snapshot watermark;
  - events appended after that watermark are delivered from the next index exactly once while the client stays connected.
- MUST preserve compatibility with existing single-event `append` frames in browser code and tests.
- MAY introduce `append_batch` SSE frames carrying an array of sanitized event objects.
- If `append_batch` is introduced:
  - batch payloads MUST be arrays in event order;
  - empty batches SHOULD NOT be emitted;
  - clients MUST accept both `append` and `append_batch` during the transition;
  - tests MUST cover backlog and live no-gap/no-duplicate behavior across batch boundaries.
- SHOULD reduce Python flush count for large backlogs by flushing once per batch or a similarly bounded number of times.
- MUST continue to send `shutdown` so browsers stop reconnecting on server shutdown.
- MUST keep all SSE payloads limited to sanitized event fields.

### Browser ingestion/rendering

- MUST ingest both legacy `append` frames and any new `append_batch` frames, if batching is implemented.
- MUST retain all received sanitized events in `eventBuffer` in arrival order.
- MUST ingest each event exactly once into the active aggregator while connected.
- MUST avoid scheduling excessive renders during large backlogs; batched delivery or render coalescing SHOULD produce bounded render work rather than one meaningful render per event.
- MUST preserve live badge, event counts, filtered counts, current root, selection, metric, sorting, expansion, and filter editor behavior.
- MUST preserve full replay from `eventBuffer` when filters change unless a replacement design is explicitly proven equivalent by tests.
- SHOULD keep JavaScript helper functions testable under Node.

### Selection pruning

- MUST add a fast no-op path for selection pruning when no paths are selected.
- MUST preserve existing behavior when one or more selected paths exist: remove selections no longer present in the current snapshot tree and update/clear the selection anchor consistently.
- MUST add or update tests for the no-selection fast path and existing selected-path behavior.

### Filter semantics

- MUST preserve Spec 7 semantics exactly:
  - active filters are client-side;
  - browser-retained event buffer remains the source for filter changes;
  - `Show filtered` controls snapshot visibility of filtered non-tombstoned paths;
  - tombstoned rename sources remain hidden regardless of `Show filtered`;
  - an event contributes to `filtered_event_count` only when all non-empty path fields match active filters;
  - localStorage filter persistence remains limited to the stable origin `http://127.0.0.1:7878`.
- MAY optimize filter matching with caches or precompiled structures if behavior remains equivalent and tests cover representative patterns.

## Solution approaches considered

### Approach A: Minimal hot-path fixes

Make tailer line splitting linear and add the no-selection pruning fast path.

Pros:
- Low risk and small surface area.
- Directly addresses the most obvious pathological CPU behavior.
- Easy to test deterministically.

Cons:
- Does not reduce tens of thousands of SSE frames/callbacks for large backlogs.
- Filter replay remains full-buffer based.

### Approach B: Tailer fix plus SSE/browser batching

Implement the linear tailer fix, selection fast path, and batched SSE delivery (`append_batch`) with browser support for both batch and single append frames.

Pros:
- Addresses both observed server startup and delivery/callback bottlenecks.
- Keeps schema and filter semantics unchanged.
- Allows compatibility with existing single-event clients/tests.
- Bounded flush/callback counts improve perceived startup time for large traces.

Cons:
- Larger cross-layer change requiring careful no-gap/no-duplicate tests.
- Introduces a second SSE event format that must be maintained.

### Approach C: Browser-side aggregate/index refactor

Add filter-independent aggregate indexes or per-path metrics so filter changes avoid full event replay.

Pros:
- Could improve repeated filter-edit interactions on very large buffers.
- May help future drill-down/indexing features.

Cons:
- Higher risk to Spec 7 semantics, especially rename/tombstone and all-paths-match event filtering.
- More complex parity testing.
- Current evidence suggests JavaScript aggregation is not the primary startup bottleneck.

## Preferred strategy

Use Approach B for this feature unless implementation reveals a blocker. It directly targets the measured bottlenecks while retaining the current mental model:

1. Make `JsonlTailer` chunk line processing linear while preserving trailing partial lines and rotation/truncation behavior.
2. Add a no-selected-paths fast path before walking the full snapshot tree for pruning.
3. Batch server-to-browser delivery with an `append_batch` SSE event, retaining legacy `append` compatibility.
4. Batch browser ingestion per SSE callback and rely on render coalescing so large backlogs produce bounded render work.
5. Defer deeper filter replay/indexing work unless tests or measurements after the first changes show filter interactions are still unacceptable.

## Acceptance criteria

- Large static JSONL startup/backlog handling no longer exhibits pathological multi-tens-of-seconds CPU behavior on traces around 80k events / hundreds of MB on a typical developer laptop.
- Existing viewer functionality and all current tests continue to pass.
- Tailer tests cover:
  - large complete chunks without quadratic-style repeated buffer slicing;
  - partial trailing lines completed by a later poll;
  - malformed lines and unsupported schema events;
  - truncation and/or rotation behavior where existing tests cover it or where practical to add.
- SSE tests cover batching if implemented:
  - backlog delivery includes all events exactly once;
  - live delivery after the backlog includes appended events exactly once;
  - shutdown behavior remains intact;
  - payloads remain sanitized.
- Browser tests cover:
  - legacy `append` compatibility;
  - `append_batch` ingestion compatibility if batching is implemented;
  - event buffer ordering and exact-once ingestion;
  - no-selection pruning no-op behavior.
- Review notes document the chosen performance strategy, test coverage, and any remaining practical envelope limits.

## Test strategy

- Use existing Python unit tests for `JsonlTailer`, `ViewerServer`, CLI, and aggregation parity as the baseline.
- Add deterministic tailer tests that build many JSONL lines in one write/read and assert all events are delivered in order with no trailing buffer for complete chunks.
- Where practical, use a monkeypatch/wrapper or bench-style assertion that would fail with repeated one-line buffer slicing, without relying on exact wall-clock timings.
- Add server tests by connecting to `/events` and parsing SSE frames. The tests should verify event IDs by content/order, not timing.
- Add Node-based browser helper tests for new ingestion helpers rather than relying only on full DOM behavior.
- Keep performance assertions coarse and deterministic enough for CI; avoid brittle absolute time limits unless generously bounded.

## Security and privacy requirements

- The server MUST bind only to `127.0.0.1`.
- New server code MUST NOT introduce endpoints that accept, persist, or log path/filter data.
- SSE batches MUST contain only sanitized fields already allowed by `sanitize_event`.
- Browser code MUST continue using text APIs rather than HTML injection for paths and filters.
- Filter persistence MUST remain browser-local and stable-origin-only.

## Open questions

### Important

- What batch size provides the best balance between startup throughput, browser responsiveness, and memory usage? The implementation should choose a conservative constant and document it in code/review notes.
- Should live events be sent immediately as single-event frames when traffic is sparse, or always through the same batch path? Either is acceptable if no-gap/no-duplicate semantics and compatibility are preserved.

### Nice-to-know

- After tailer and SSE batching, are filter replay interactions still slow enough at 80k events to justify a future aggregate/index refactor?
- Should the documented recommended trace envelope be updated after measurements from this work?

