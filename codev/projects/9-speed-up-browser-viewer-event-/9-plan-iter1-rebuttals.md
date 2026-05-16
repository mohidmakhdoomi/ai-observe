# Plan Iteration 1 Rebuttal

## Codex: REQUEST_CHANGES

### 1. Phase 2 was not independently shippable because the server could emit `append_batch` before the browser consumed it.

Accepted. I updated the plan so the SSE protocol change is implemented as a single compatible server+browser phase:

- Renamed Phase 2 to `phase-2-sse-browser-batching` / “Compatible bounded SSE and browser batch ingestion”.
- Added `src/ai_observe/viewer/static/index.js` and `tests/test_viewer_index_js.py` to Phase 2.
- Added a Phase 2 success criterion that the checked-in server must never emit `append_batch` without the checked-in browser being able to consume it.
- Moved browser append/batch ingestion helper tests into Phase 2.
- Narrowed Phase 3 to the independently shippable selection-pruning fast path.
- Updated cross-phase notes to preserve compatibility at every committed phase.

This addresses the protocol-compatibility concern without delaying batching until a later incompatible phase.

### 2. Phase 4 should explicitly update or reaffirm the existing documented envelope in `docs/viewer.md`.

Accepted. I updated Phase 4 to require `docs/viewer.md` to explicitly update or reaffirm the existing concrete trace envelope and to include a rationale if it remains unchanged.

## Claude: APPROVE

No blocking changes requested. The minor nuance about selection-anchor cleanup is covered by keeping the fast path in `pruneSelections()` and preserving existing selected-path behavior tests when selections are present.
