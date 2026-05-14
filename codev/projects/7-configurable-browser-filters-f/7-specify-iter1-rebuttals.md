# Rebuttal: Spec 7 specify iteration 1

## Consultation set

Per architect instruction, Gemini is intentionally excluded for this project. Iteration 1 used:

- Codex: `REQUEST_CHANGES`
- Claude: `APPROVE`

## Codex feedback

### 1. Filtering source of truth / `Show filtered` behavior

**Feedback:** The spec did not define whether filters apply at ingest/replay time, snapshot/render time, or both, and how that interacts with `Show filtered`.

**Resolution:** Changed the spec to define the active filter list as the single source of truth. The browser retains the external event buffer, replays that buffer into the aggregator on filter changes, and applies the active filters during ingest/replay so `filtered_event_count` is recomputed. Snapshot/rendering uses the same active filters plus `Show filtered`: unchecked hides matching non-tombstoned paths; checked includes them again. Tombstones remain hidden in both modes.

### 2. Exact-path actions for directory items

**Feedback:** Directory rows/tiles are synthesized, so exact-path filters for directories could be ambiguous or no-op-like.

**Resolution:** Clarified that exact-path patterns always match only the literal full path. For synthesized directory nodes, an exact `/work/build` filter does not match `/work/build/out.log`; users must choose `/work/build/**` for subtree filtering. The context action wording should distinguish exact directory path from subtree filtering.

### 3. Multi-select interaction underspecified

**Feedback:** Multi-select had no defined interaction pattern, persistence, or invocation path.

**Resolution:** Added minimum interaction requirements: Ctrl/Cmd-click toggling on table rows and treemap tiles; Shift-click range selection for visible table rows as a SHOULD. Selected paths should persist across re-renders while still present and be pruned when no longer present. The add-selected action may be in the top bar, context menu, or both, but must be discoverable when at least two items are selected.

### 4. Persistence on non-stable origins

**Feedback:** The spec should explicitly say whether fallback/custom ports read/write `localStorage` or not.

**Resolution:** Clarified that filter `localStorage` is read and written only on `http://127.0.0.1:7878`. Fallback ephemeral ports and explicit custom ports use session-only in-memory filters initialized to factory defaults and do not read or write persisted filter state.

## Claude comments

Claude approved the spec and highlighted several plan-phase concerns. I also folded the actionable clarifications into the spec:

- Reaffirmed that replay recomputes filtered-event counts for the active filters.
- Clarified file subtree behavior: v1 should omit subtree for files unless explicitly labeled as `/file/**`.
- Added tests for stable-port-only persistence detection and replay equivalence.
- Kept event-buffer ownership as a plan-phase detail, while preserving the spec requirement that all sanitized SSE events are retained in arrival order.

## Conclusion

The spec now addresses the blocking ambiguity called out by Codex while staying within the user/architect pre-spec decisions. It is ready for re-verification.
