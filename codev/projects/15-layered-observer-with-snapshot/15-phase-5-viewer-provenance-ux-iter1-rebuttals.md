# Phase 5 Iteration 1 Rebuttal

## Summary

All three reviews requested the same substantive Phase 5 work: the viewer was still missing provenance rendering, source filters, and artifact/meta banner handling. I agreed with that feedback, implemented the missing Phase 5 functionality in the viewer stack, added the missing mixed-source fixtures/tests, and re-ran the exact phase-5 test command from the plan successfully.

## Gemini / Codex / Claude: provenance UX, source filters, and artifact banners were missing

**Feedback:** The reviews all correctly identified that the pre-review worktree still lacked the core Phase 5 deliverables:
- no visible `strace` / `snapshot` provenance in the browser UI;
- no source visibility controls;
- no viewer-side handling for `.jsonl.partial`, `.jsonl.rebuilt`, and `.meta.json` sibling artifacts;
- no mixed-source fixtures or end-to-end tests proving the behavior.

**Resolution:** Implemented the missing Phase 5 work across the targeted viewer files:

### 1. Source-aware aggregation and mixed-stream provenance
- Updated `src/ai_observe/viewer/static/aggregator.js`
- Updated `tests/_aggregator_oracle.py`
- Updated `tests/test_viewer_aggregator.py`
- Added `tests/fixtures/viewer/mixed_sources.jsonl`
- Regenerated the viewer golden snapshots

The aggregator now:
- normalizes missing v1 provenance as `strace/direct`;
- tracks `sources` and `confidences` on file and directory nodes;
- supports source-scoped replay via enabled-source selection;
- preserves mixed v1/v2 aggregation parity between the JS implementation and the Python oracle.

### 2. Visible provenance in the browser UI
- Updated `src/ai_observe/viewer/static/index.html`
- Updated `src/ai_observe/viewer/static/style.css`
- Updated `src/ai_observe/viewer/static/table.js`
- Updated `src/ai_observe/viewer/static/treemap.js`
- Updated `tests/test_viewer_table_js.py`
- Updated `tests/test_viewer_treemap.py`
- Updated `tests/test_viewer_smoke_e2e.py`

The browser UI now:
- renders provenance badges in table rows;
- includes source/confidence text in treemap tooltips;
- exposes explicit `Strace` / `Snapshot` source toggles;
- keeps the existing path-filter behavior intact.

### 3. Sanitized session/artifact state and banner-driven artifact switching
- Updated `src/ai_observe/viewer/server.py`
- Updated `src/ai_observe/viewer/static/index.js`
- Updated `tests/test_viewer_server.py`
- Updated `tests/test_viewer_index_js.py`
- Updated `tests/test_viewer_smoke_e2e.py`

The viewer server now:
- detects sibling `.jsonl`, `.jsonl.rebuilt`, `.jsonl.partial`, and `.meta.json` artifacts for a session;
- exposes a sanitized `/session` payload with artifact roles, authoritative-artifact selection, parser status, warning counts, and snapshot diagnostic counts only;
- supports artifact-specific SSE streaming via `/events?artifact=...`;
- defaults to the authoritative rebuilt stream when meta indicates `.jsonl.rebuilt` is authoritative.

The browser runtime now:
- renders a non-sensitive banner for rebuilt/partial/meta state;
- allows switching between canonical, rebuilt, and partial-direct artifacts;
- auto-follows the authoritative artifact unless the user explicitly selects another artifact.

## Review-specific follow-up notes

### Codex
The review called out the lack of source controls, provenance rendering, artifact detection, and mixed-source tests. All of those are now implemented and covered by the updated viewer server, runtime, aggregator, and fixture-based tests.

### Gemini
The review noted that the UI files and source-aware aggregation were unchanged. They are now updated, and the mixed v1/v2 loading path in `tests/test_viewer_aggregator.py` no longer drops schema-v2 events.

### Claude
The review correctly described the pre-fix state. The missing table/treemap provenance UX, source filtering, artifact banners, and meta-sidecar handling are now present and validated.

## Verification

- `PYTHONPATH=src python3 -m unittest tests.test_viewer_tailer tests.test_viewer_server tests.test_viewer_aggregator tests.test_viewer_index_js tests.test_viewer_table_js tests.test_viewer_treemap tests.test_viewer_smoke_e2e` — passed (82 tests)

