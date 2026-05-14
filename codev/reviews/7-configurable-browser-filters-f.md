# Review: configurable-browser-filters-f

## Summary

Implemented configurable browser Filters for `ai_observe.viewer`. The viewer now uses factory glob filters that users can edit, remove, reset, persist on the stable default origin, and extend from treemap/table items through right-click previews or multi-select exact-path additions. Filter changes replay the retained browser event buffer client-side without reconnecting.

## Spec Compliance

- [x] Filter editor in the top bar: implemented view/add/edit/remove/reset flows, validation errors, and `Filters (N)` summary.
- [x] Glob filter syntax: implemented absolute-path anchored glob matching with `*`, `**`, and `?`; invalid non-absolute patterns are rejected.
- [x] Factory defaults: existing hardcoded noise paths are now removable factory filter patterns and reset restores exactly those defaults.
- [x] Persistence: filter lists are read/written only on `http://127.0.0.1:7878`; fallback and custom ports use session-only defaults.
- [x] Add to Filters from items: treemap/table right-click previews exact/subtree patterns where appropriate; multi-select add previews exact paths only.
- [x] Re-aggregation: browser retains sanitized events in arrival order and rebuilds the aggregator from that buffer on every committed filter change.
- [x] Rename/filter semantics: tombstones remain hidden, and the existing all-paths-match rule applies to user filters.
- [x] UI wording: visible UI and docs use Filters/filtered terminology; legacy `isNoise`/`include_noise` internals remain only for compatibility.
- [x] Tests and documentation: unit/parity/helper/server/smoke coverage updated, `docs/viewer.md` documents the new workflow, and the full suite passes.

## Deviations from Plan

- **Consultation model set**: Gemini was intentionally excluded for this project per architect instruction; porch consultations used Codex and Claude.
- **Review artifact timing**: The review document was created only after implementation phases completed, as required by the plan.
- **Internal naming**: Some compatibility names such as `isNoise`, `eventIsNoise`, and `include_noise` remain internally to avoid breaking existing helper/tests while the user-facing text was renamed.

## Architecture Updates

Created `codev/resources/arch.md` because the project introduced durable viewer architecture details: client-side filter ownership, event-buffer replay, stable-origin persistence, default-port fallback, and security invariants for rendering user-provided path/filter text.

## Lessons Learned Updates

Created `codev/resources/lessons-learned.md` with generalizable lessons about keeping replay state outside aggregators, centralizing UI mutations in pure helpers for Node-backed tests, and explicitly specifying synthesized directory-node behavior for exact versus subtree filters.

## Lessons Learned

### What Went Well

- Splitting the work into filter-core, replay/persistence/port, editor, item actions, and docs phases kept the feature understandable despite touching server, aggregator, browser UI, docs, and tests.
- Exported helper functions made the browser-only behavior testable without adding a browser automation dependency.
- Consultation caught real ambiguity early in the spec and one concrete multi-select selection-state bug during implementation.

### Challenges Encountered

- **Filter source-of-truth ambiguity**: Initial spec wording did not fully define ingest/replay versus render filtering. The spec was clarified so the active filter list drives replay counts and snapshot visibility, while the event buffer remains unfiltered.
- **Directory exact filters**: Synthesized directory rows/tiles made exact path semantics easy to misunderstand. The spec, docs, and action labels now distinguish exact `/dir` from subtree `/dir/**`.
- **Selection toggling**: Ctrl/Cmd deselection originally left the active path highlighted. The selection transition was centralized in `updateMultiSelectionState()` and covered by tests.
- **Static asset size**: The smoke test has a 50KB static asset budget, so phase 5 copy/CSS polish had to stay compact.
- **Provider availability**: The final Claude consultation was delayed by a provider rate limit and resumed after the reset window.

### What Would Be Done Differently

- Define replay/live-arrival and filter source-of-truth semantics in the first spec draft rather than waiting for consultation feedback.
- Put selection-state transitions behind a pure helper before wiring UI callbacks to reduce the chance of renderer/UI mismatch.
- Leave a little more static bundle headroom or revisit the smoke limit before adding additional browser UI features.

### Methodology Improvements

- For browser features with no automation harness, plans should explicitly identify pure helper seams for every complex UI state transition.
- Porch/consult output could surface provider rate-limit failures as a resumable blocked state with the retry time in the next-task output.

## Technical Debt

- Static asset size is very close to the existing 50KB smoke limit, leaving little margin for future UI copy or styling.
- Backward-compatible internal names containing `Noise` remain in the aggregator API; a later cleanup could rename them if no downstream compatibility is needed.
- The browser event buffer is intentionally unbounded for v1, matching the existing documented ~10^4 event envelope.

## Consultation Feedback

### Specify Phase (Round 1)

#### Codex
- **Concern**: The spec did not clearly define whether filters apply at ingest/replay time, snapshot/render time, or both.
  - **Addressed**: Clarified that the active filter list is the source of truth, replay recomputes filtered event counts, snapshot visibility uses the same filters plus `Show filtered`, and tombstones remain hidden.
- **Concern**: Exact-path actions for synthesized directory items were ambiguous.
  - **Addressed**: Clarified exact directory filters match only the literal directory path and subtree filters require `/dir/**`.
- **Concern**: Multi-select interaction, persistence, and invocation were underspecified.
  - **Addressed**: Added Ctrl/Cmd toggle requirements, table Shift range as a SHOULD, selection persistence/pruning, and discoverable add-selected behavior.
- **Concern**: Persistence on non-stable origins needed an explicit contract.
  - **Addressed**: Spec now requires no `localStorage` read/write outside `http://127.0.0.1:7878`.

#### Claude
- **Concern**: Minor clarifications around replay counts, file subtree behavior, persistence tests, and event-buffer ownership.
  - **Addressed**: Folded actionable clarifications into the spec and left event-buffer ownership for plan/implementation.

### Plan Phase (Round 1)

#### Codex
- **Concern**: Phase 2 should describe replay/live-event concurrency more concretely.
  - **Addressed**: Implementation retained an append-only browser event buffer and rebuilds the aggregator from that buffer on filter changes; live arrivals append to the same buffer and are ingested once.
- **Concern**: Dedicated runtime-helper tests would be clearer than extending breadcrumb tests.
  - **Addressed**: Added `tests/test_viewer_index_js.py` for Node-backed helper/editor tests.
- **Concern**: Empty-string/whitespace pattern handling should be tested.
  - **Addressed**: Validation tests cover invalid and trimmed pattern behavior.

#### Claude
- No blocking concerns raised — APPROVE.

### Implement Phase 1: Filter core (Round 1)

#### Codex
- No concerns raised — APPROVE.

#### Claude
- No concerns raised — APPROVE.

### Implement Phase 2: Replay, persistence, and port (Round 1)

#### Codex
- No concerns raised — APPROVE.

#### Claude
- No concerns raised — APPROVE.

### Implement Phase 3: Filter editor (Round 1)

#### Codex
- No concerns raised — APPROVE.

#### Claude
- No concerns raised — APPROVE.

### Implement Phase 4: Item actions (Round 1)

#### Codex
- **Concern**: Ctrl/Cmd-click deselection removed a path from `selectedPaths` but immediately set it as `selectedPath`, leaving the row/tile visually selected.
  - **Addressed**: Added `updateMultiSelectionState()`, updated `onMultiSelect()`, and added tests verifying toggling off the active path clears both multi-selection and active selection.

#### Claude
- No blocking concerns raised — APPROVE.

### Implement Phase 5: Docs and smoke (Round 1)

#### Codex
- No concerns raised — APPROVE.

#### Claude
- No concerns raised — APPROVE.

## Flaky Tests

No flaky tests encountered. No tests were skipped.

## Follow-up Items

- Consider increasing or rethinking the 50KB static asset smoke limit before adding more viewer UI.
- Consider a future internal API cleanup from `Noise` compatibility names to `Filter` names if compatibility constraints allow.
- Future projects may add bounded retention/windowing, per-path event indexes, import/export, positive filters, or per-JSONL persistence; these remain out of scope for v1.

## Final Verification

- `python3 -m unittest discover -s tests` — passing (123 tests).
- Build check — skipped by project config.
- Lint check — no separate lint command configured.
