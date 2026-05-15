# Implementation Plan: configurable-browser-filters-f

## Overview

Implement configurable browser-side filters for `ai_observe.viewer` in five independently testable phases. The work starts with the filter language and aggregation contracts, then adds event-buffer replay and stable-port persistence, then layers on the top-bar filter editor, item context/multi-select actions, and finally documentation plus smoke coverage.

The implementation remains client-side for filtering. The server continues to stream sanitized events only; no JSONL contents are modified and no server-side filter state is introduced. JavaScript remains the canonical browser implementation, and `tests/_aggregator_oracle.py` remains the Python oracle for parity tests.

## Phase machine-readable block

```json
{
  "phases": [
    {
      "id": "phase-1-filter-core",
      "name": "Filter core: glob syntax, defaults, aggregator/oracle parity",
      "depends_on": []
    },
    {
      "id": "phase-2-replay-persistence-port",
      "name": "Runtime wiring: event-buffer replay, stable-port persistence, port fallback",
      "depends_on": ["phase-1-filter-core"]
    },
    {
      "id": "phase-3-filter-editor",
      "name": "Top-bar filter editor and Filters wording",
      "depends_on": ["phase-2-replay-persistence-port"]
    },
    {
      "id": "phase-4-item-actions",
      "name": "Treemap/table context actions and multi-select add-to-filters",
      "depends_on": ["phase-3-filter-editor"]
    },
    {
      "id": "phase-5-docs-smoke",
      "name": "Documentation, smoke coverage, and final regression pass",
      "depends_on": ["phase-4-item-actions"]
    }
  ]
}
```

## Phases

### Phase 1: Filter core — glob syntax, defaults, aggregator/oracle parity (`phase-1-filter-core`)

- **Objective**: Replace hardcoded regex-only noise matching with a reusable configurable filter core while preserving existing default behavior and JS/Python parity.
- **Files**:
  - `src/ai_observe/viewer/static/aggregator.js` — add factory default glob patterns, a small glob compiler/matcher, active-filter support in `createAggregator`, and renamed exported helpers such as filter matching while keeping backward-compatible aliases if useful for existing tests.
  - `tests/_aggregator_oracle.py` — mirror the JS glob compiler/matcher and allow `Aggregator` to accept active filter patterns for parity tests.
  - `tests/test_viewer_aggregator.py` — update noise/filter tests, add glob syntax tests, custom-filter tests, exact-directory-vs-subtree tests, tombstone precedence tests, all-paths-match event tests, and JS parity coverage for configurable filters.
  - `tests/fixtures/viewer/*.jsonl` and `tests/fixtures/viewer/golden/*.json` — update or add only if required by changed fixture expectations; keep existing fixtures synthetic.
- **Dependencies**: None.
- **Success Criteria**:
  - Factory defaults match current hidden-path behavior for `/home/*/.codex/**`, `/home/*/.cache/**`, `/tmp/**`, `/var/tmp/**`, `/proc/**`, `/sys/**`, `/dev/**`, and `/run/**`.
  - Glob examples from the spec pass exactly, including `/tmp/**` matching `/tmp` and subtree patterns matching zero or more segments.
  - Literal regex metacharacters in patterns are escaped and treated as path text unless they are one of the supported glob tokens (`*`, `**`, `?`).
  - Invalid non-absolute patterns are rejected or surfaced as invalid without breaking aggregation.
  - Exact `/work/build` filters do not hide `/work/build/out.log`; `/work/build/**` hides both `/work/build` and descendants.
  - Event-level filtered counts use the all-non-empty-paths-match rule with custom filters.
  - Tombstoned rename sources remain absent even when matching filters would otherwise be shown.
- **Tests**:
  - Run `python3 -m unittest tests.test_viewer_aggregator`.
  - Node-backed JS parity tests should compare Python and JS snapshots for default filters and at least one custom filter list.
  - Golden snapshots should remain deterministic; update committed goldens only if a test fixture intentionally changes.

### Phase 2: Runtime wiring — event-buffer replay, stable-port persistence, port fallback (`phase-2-replay-persistence-port`)

- **Objective**: Wire the filter core into the live viewer runtime: retain sanitized events in arrival order, replay on filter changes, persist filters only on `http://127.0.0.1:7878`, and change default port behavior.
- **Files**:
  - `src/ai_observe/viewer/static/index.js` — add the flat event buffer, replay/rebuild helper, active-filter state, `Show filtered` state wiring, stable-origin storage helpers, malformed-storage fallback, and replay-vs-live event ordering safeguards.
  - `src/ai_observe/viewer/__main__.py` — change default port to `7878`; on default-port bind failure, retry with port `0`; preserve explicit `--port` semantics.
  - `src/ai_observe/viewer/server.py` — if needed, expose/handle bind failures cleanly without weakening loopback-only binding.
  - `tests/test_viewer_breadcrumb.py` or a new `tests/test_viewer_index_js.py` — Node-backed tests for exported index/runtime helpers such as stable-origin detection, storage fallback, and replay equivalence helpers.
  - `tests/test_viewer_server.py` — add CLI/server tests for default port `7878`, fallback to ephemeral when `7878` is occupied, explicit `--port`, and unchanged loopback binding.
- **Dependencies**: Phase 1.
- **Success Criteria**:
  - `python -m ai_observe.viewer <jsonl> --no-browser` defaults to `127.0.0.1:7878` when available.
  - If port `7878` is occupied and no explicit port is passed, the viewer starts on an ephemeral loopback port and prints that URL.
  - Explicit `--port PORT` continues to bind that exact port or fail normally if unavailable; it does not silently fall back.
  - The browser stores and loads filter patterns only when `location.origin` is `http://127.0.0.1:7878`.
  - Fallback/custom-port origins initialize session filters from factory defaults and do not read or write filter `localStorage`.
  - Replaying the retained event buffer after a filter change produces the same snapshot as a fresh aggregator with the same active filters ingesting those events once.
  - Live SSE events arriving around a replay are appended to the buffer and included exactly once in the final aggregate.
- **Tests**:
  - Run `python3 -m unittest tests.test_viewer_server`.
  - Run Node helper tests for `index.js`; skip cleanly if Node is unavailable, following existing test style.
  - Run `python3 -m unittest tests.test_viewer_aggregator` after wiring to ensure core semantics were not regressed.

### Phase 3: Top-bar filter editor and Filters wording (`phase-3-filter-editor`)

- **Objective**: Add the user-facing top-bar filter editor and rename visible “Noise” language to “Filters,” with add/edit/remove/reset flows and validation feedback.
- **Files**:
  - `src/ai_observe/viewer/static/index.html` — replace `Show noise` with `Show filtered`, add a `Filters` editor trigger/summary, and add accessible editor/preview DOM containers.
  - `src/ai_observe/viewer/static/index.js` — implement editor state/actions: open/close, add pattern, edit pattern, remove pattern, reset to defaults, validate before commit, persist when eligible, replay after committed changes, and keep path data out of URL/title/history.
  - `src/ai_observe/viewer/static/style.css` — style the filter button, editor panel/dialog, validation errors, pattern rows, and reset/add controls while keeping the static bundle small.
  - `tests/test_viewer_smoke_e2e.py` — update static asset/text checks for “Filters” wording and no forbidden APIs.
  - `tests/test_viewer_breadcrumb.py` or `tests/test_viewer_index_js.py` — add Node-backed tests for editor reducer/helper functions where possible without a browser DOM.
- **Dependencies**: Phase 2.
- **Success Criteria**:
  - Top-bar visible text uses “Filters” / “Show filtered”; no user-facing “Noise” remains in HTML or rendered control labels.
  - The editor displays the active filter list, including factory defaults on first load.
  - Users can add, edit, remove, and reset patterns; invalid patterns are rejected or clearly flagged before commit.
  - Reset replaces the entire list with the factory default list.
  - Every committed change triggers replay from the retained event buffer and updates counts/tree/table without reconnecting to `/events`.
  - `Show filtered` reveals matching non-tombstoned paths without mutating the active filter list.
  - Path and pattern strings are inserted with text APIs, not `innerHTML` or `document.write`.
- **Tests**:
  - Run `python3 -m unittest tests.test_viewer_smoke_e2e`.
  - Run Node-backed helper/editor tests for validation, reset, add/edit/remove, duplicate handling, and storage persistence decisions.
  - Run the full viewer-related test subset: `python3 -m unittest tests.test_viewer_aggregator tests.test_viewer_server tests.test_viewer_smoke_e2e tests.test_viewer_breadcrumb`.

### Phase 4: Treemap/table context actions and multi-select add-to-filters (`phase-4-item-actions`)

- **Objective**: Add item-level filter creation from treemap and table items, including right-click exact/subtree previews and multi-select exact-path additions.
- **Files**:
  - `src/ai_observe/viewer/static/treemap.js` — surface item `contextmenu` and multi-select pointer events through callbacks; expose enough metadata for file-vs-directory action choices.
  - `src/ai_observe/viewer/static/table.js` — add row context-menu hooks, Ctrl/Cmd-click toggling, Shift-click visible-row range selection support, selected-row styling hooks, and callbacks for multi-select state.
  - `src/ai_observe/viewer/static/index.js` — own selected path set, prune selections after snapshots, build exact/subtree pattern previews, commit previews through the same editor/filter mutation path, and expose a discoverable add-selected action when at least two items are selected.
  - `src/ai_observe/viewer/static/style.css` — style selected rows/tiles, context menu or preview panel, and add-selected controls.
  - `tests/test_viewer_table_js.py` — add Node-backed tests for visible-row flattening/range selection helper functions if implemented as exports.
  - `tests/test_viewer_treemap.py` — add Node-backed tests for context-action metadata helpers if implemented as exports.
- **Dependencies**: Phase 3.
- **Success Criteria**:
  - Right-clicking a directory row/tile can propose exact `/dir` and subtree `/dir/**` patterns with editable preview before commit.
  - Right-clicking a file row/tile proposes exact `/file`; subtree is omitted unless explicitly labeled as `/file/**`.
  - Committing a context preview adds safe, validated, duplicate-harmless patterns and triggers replay.
  - Ctrl/Cmd-click toggles selection on both table rows and treemap tiles.
  - Table Shift-click selects a range over currently visible rows if practical; if not practical, the reason must be documented in the review and Ctrl/Cmd multi-select remains the required minimum.
  - Multi-select add generates exact paths only, previews all proposed patterns, and commits through the same filter mutation path.
  - Selection survives ordinary re-renders while paths remain visible and is pruned for paths absent from the current snapshot.
- **Tests**:
  - Run `python3 -m unittest tests.test_viewer_table_js tests.test_viewer_treemap`.
  - Add helper tests for exact-vs-subtree proposal generation, duplicate handling, selected-set pruning, and multi-select exact pattern generation.
  - Run `python3 -m unittest tests.test_viewer_aggregator tests.test_viewer_breadcrumb tests.test_viewer_table_js tests.test_viewer_treemap`.

### Phase 5: Documentation, smoke coverage, and final regression pass (`phase-5-docs-smoke`)

- **Objective**: Update user documentation, ensure all visible terminology and tests align with the new Filters model, and perform final regression before PR/review.
- **Files**:
  - `docs/viewer.md` — update invocation default port, collision fallback behavior, Filters editor, glob syntax, persistence rules, Show filtered behavior, context actions, multi-select flow, replay/memory envelope, and manual walkthrough checklist.
  - `src/ai_observe/viewer/static/index.html` and static JS/CSS files — final small copy/accessibility polish if discovered while documenting.
  - `tests/test_viewer_smoke_e2e.py` — update expected asset/text checks and forbidden API checks if final UI copy changes.
  - `codev/reviews/7-configurable-browser-filters-f.md` — create review notes only during the Review phase, not during implementation, documenting final tests and any flaky-test skips if they occur.
- **Dependencies**: Phase 4.
- **Success Criteria**:
  - `docs/viewer.md` accurately describes factory defaults, configurable filters, glob syntax, `Show filtered`, stable-port-only persistence, and right-click/multi-select add flows.
  - The manual walkthrough checklist includes adding/removing/resetting filters, stable-port reload persistence, fallback-port non-persistence, right-click exact/subtree, and multi-select exact add.
  - No user-facing “Noise” terminology remains except in historical/current-state documentation where explicitly appropriate.
  - Static asset smoke tests still confirm no `innerHTML`, `document.write`, `eval(`, `raw_syscall`, or `document.title` leakage.
  - Full test suite passes, or any pre-existing flaky tests are skipped with explicit annotations and documented in the review per builder instructions.
- **Tests**:
  - Run `python3 -m unittest discover -s tests`.
  - If Node is available, verify all Node-backed JS parity/helper tests run as part of the suite.
  - Manually smoke the viewer with a synthetic JSONL if needed: confirm the page loads, filters can be edited, replays happen without reconnecting, and the printed URL reflects the bound port.

## Cross-phase implementation notes

- Do not introduce server-side filtering or new server persistence. The only persisted state is browser `localStorage` on `http://127.0.0.1:7878`.
- Keep the sanitized SSE payload contract unchanged: timestamp, operation, path, old_path, new_path, and result only.
- Prefer small exported pure helpers for glob compilation, storage decisions, replay equivalence, pattern proposal, and selection pruning so tests can run under Node without a browser automation dependency.
- Preserve existing snapshot field names (`include_noise` may remain internally for compatibility if the implementation chooses), but visible UI text must say Filters/filtered.
- Keep path/pattern rendering through text APIs. Do not add `innerHTML`, `document.write`, or `eval`.
- If phase implementation reveals a spec ambiguity, stop and ask the architect rather than silently changing scope.

## Risk Assessment

- **Glob compiler mismatch between JS and Python**: Mitigate by implementing the same examples and edge cases in both code paths and enforcing Node-backed parity tests.
- **Replay/event ordering bugs while live events arrive**: Mitigate by making the retained event buffer the source for rebuilds and adding replay equivalence tests.
- **UI scope creep from editor plus context menus plus multi-select**: Mitigate by routing every mutation through one filter-state API and by keeping import/export and advanced filter types out of scope.
- **localStorage origin confusion**: Mitigate with a single helper that returns true only for `http://127.0.0.1:7878` and tests for stable, fallback, and explicit custom origins.
- **Port fallback accidentally hiding explicit bind failures**: Mitigate by applying fallback only when the user did not provide `--port` and by testing explicit-port failure behavior separately.
- **Security regressions from dynamic UI**: Mitigate with static smoke checks for forbidden APIs and by using `textContent`/DOM APIs for all path and pattern text.
