# Phase 4 Iteration 1 Rebuttals

## Codex — REQUEST_CHANGES

### Issue: Ctrl/Cmd deselection left the toggled-off path visually selected

Codex was correct. The original `onMultiSelect()` removed the path from `state.selectedPaths`, but then always assigned the same path to `state.selectedPath`. Since both table rows and treemap tiles render as selected when either `selectedPaths` contains the path or `selectedPath` equals the path, toggling off the active path left it highlighted.

### Change made

- Added `updateMultiSelectionState()` in `src/ai_observe/viewer/static/index.js` to centralize multi-select state transitions.
- Updated `onMultiSelect()` to use that helper.
- When Ctrl/Cmd toggles a path off and that path is also the active `selectedPath`, the helper now clears `selectedPath` to `null`, so the row/tile is no longer rendered as selected.
- Shift range selection still sets the target as the active path and preserves the selection anchor behavior.

### Test coverage added

- Extended `tests/test_viewer_index_js.py::test_item_action_helpers_build_preview_patterns_and_prune_selection` to cover:
  - toggling an active selected path off clears both `selectedPaths` and `selectedPath`,
  - toggling a path on sets it as `selectedPath`,
  - shift range selection keeps the target path active and selects the full visible range.

### Verification

- `python3 -m unittest tests.test_viewer_index_js tests.test_viewer_table_js tests.test_viewer_treemap tests.test_viewer_smoke_e2e`
- `python3 -m unittest discover -s tests`

Both pass after the fix.

## Claude — APPROVE

No blocking issues. Claude's minor observations are accepted as non-blocking for phase 4.
