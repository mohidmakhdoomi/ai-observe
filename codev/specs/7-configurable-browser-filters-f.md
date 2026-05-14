# Spec 7: Configurable browser filters for the viewer

## Summary

`ai_observe.viewer` currently hides a fixed set of noisy paths using hardcoded browser-side regular expressions. This feature replaces that fixed "noise" concept with user-configurable **Filters** for the browser UI. Users can view, add, edit, remove, and reset path filter patterns from the top bar, and can add paths to the filter list from treemap/table items through right-click and multi-select actions.

The filters affect both the treemap and the table. Filtering remains entirely client-side: the server still streams the sanitized event backlog and live appends once, while the browser retains events in arrival order and fully replays them whenever the filter list changes.

## Problem

Real observer traces include user- and project-specific noisy paths: build directories, caches, temporary outputs, scratch workspaces, project logs, and tool-specific state. The current viewer ships a hardcoded list in `src/ai_observe/viewer/static/aggregator.js` and exposes only a `Show noise` checkbox. Users who need different exclusions must edit source code, which is not acceptable for a browser viewer workflow.

## Current state

- The viewer is launched via `python -m ai_observe.viewer <jsonl>`.
- The default CLI port is ephemeral (`--port` defaults to `0`).
- The server binds to `127.0.0.1`, serves static assets, and streams sanitized events over SSE.
- Browser aggregation is canonical in `src/ai_observe/viewer/static/aggregator.js`; tests mirror it in `tests/_aggregator_oracle.py`.
- Noise filtering is a fixed regex list:
  - `/home/*/.codex/**`
  - `/home/*/.cache/**`
  - `/tmp/**`
  - `/var/tmp/**`
  - `/proc/**`
  - `/sys/**`
  - `/dev/**`
  - `/run/**`
- Events are considered filtered only when **all** non-empty path fields (`path`, `old_path`, `new_path`) match the noise list.
- Tombstoned rename sources are hidden regardless of `Show noise`.
- The UI says "Noise" (`Show noise`) rather than "Filters".

## Desired state

Users can customize the exclude list without editing source. The viewer starts with factory default filters, lets users mutate the list in the browser, persists those changes in `localStorage` when running on the stable default port, and refreshes the visible aggregation immediately after every filter change.

The hardcoded defaults become factory filter patterns rather than a permanent uneditable list. Users may remove any default pattern. A reset action wipes the entire current list and restores factory defaults.

## Stakeholders

- Developers reviewing live or completed `ai_observe` traces.
- Users who run Codex or other tools in worktrees containing project-specific generated or cache paths.
- Maintainers of the viewer and its tests, who need deterministic filtering semantics shared by JS and Python test oracle code.

## Scope

### In scope

1. **Filter editor in the top bar**
   - Open a filter editor from the top bar.
   - View current filter patterns.
   - Add a pattern.
   - Edit a pattern.
   - Remove a pattern, including factory defaults.
   - Reset the entire list to factory defaults.
   - Use user-facing wording "Filters" instead of "Noise" throughout visible UI text.

2. **Glob filter syntax**
   - Patterns are absolute-path anchored globs.
   - `*` matches zero or more characters except `/` within one path segment.
   - `**` matches any subpath, including zero or more complete segments.
   - `?` matches exactly one non-`/` character.
   - No character classes.
   - No brace expansion.
   - Patterns apply to absolute paths; relative paths are not matched unless future schema behavior explicitly requires them.

3. **Factory defaults**
   - The initial filter list is pre-populated with the existing defaults, represented as globs.
   - Defaults are individually removable.
   - Reset to defaults replaces the entire current list with the factory list.

4. **Persistence**
   - Filter patterns persist in browser `localStorage` globally, not per JSONL file.
   - The viewer default port changes to **7878**.
   - If `7878` is unavailable, the viewer falls back to an ephemeral port.
   - Persisted filters are used only when the stable default port (`7878`) is bound. This avoids cross-port/localStorage-origin surprises and ensures the default viewer URL has stable storage.
   - If the user explicitly passes a port, that port is used according to existing CLI semantics; persistence is only guaranteed for the stable default port.

5. **Add to filters from items**
   - Right-clicking a treemap tile or table row offers filter actions.
   - Right-click action choices:
     - exact path, e.g. `/work/build/log.txt`
     - subtree, e.g. `/work/build/**`
   - Right-click actions show an editable preview before committing.
   - Multi-select allows adding multiple selected items to filters.
   - Multi-select add is exact-path only.
   - Multi-select add shows all previewed patterns before committing.

6. **Re-aggregation**
   - The browser retains sanitized SSE events in a flat arrival-order buffer.
   - On every filter list change, the browser fully resets/replays the aggregator from the retained buffer using the new filters.
   - No reconnect is required for filter changes.
   - v1 accepts linear memory usage in event count, targeting the existing documented ~10^4 event envelope.

7. **Rename/filter semantics**
   - Tombstones win over filters. Renamed-away source paths remain hidden regardless of filter configuration.
   - The current all-paths-match event-level rule applies to user filters too: an event counts as filtered only if every non-empty path field on the event matches a filter.
   - Mixed-path rename events remain retained/visible according to existing semantics when any non-empty path does not match.

### Out of scope

- Bounded retention or event windowing.
- Per-path event indexes or drill-down to all events per item.
- Include-lists / positive filters.
- Per-operation-type filters.
- Time-range filters.
- Pattern import/export.
- Per-JSONL filter persistence.
- Server-side filtering.
- Changing the observer JSONL schema.

## Functional requirements

### Filter matching

- MUST treat patterns as absolute-path anchored globs.
- MUST compile filter patterns in the browser without using `eval` or dynamic code execution.
- MUST match whole paths, not substrings.
- MUST support these examples:
  - `/tmp/**` matches `/tmp`, `/tmp/a`, and `/tmp/a/b`.
  - `/home/*/.cache/**` matches `/home/alice/.cache` and `/home/bob/.cache/pip/x`.
  - `/work/build/*` matches `/work/build/a.o` but not `/work/build/obj/a.o`.
  - `/work/build/**` matches `/work/build`, `/work/build/a.o`, and `/work/build/obj/a.o`.
  - `/work/?.txt` matches `/work/a.txt` but not `/work/ab.txt` or `/work/dir/a.txt`.
- MUST reject or clearly flag invalid patterns that are not absolute path anchored.
- SHOULD preserve the user's textual glob patterns exactly, apart from safe trimming if implemented.
- SHOULD avoid duplicate exact patterns where practical, or make duplicates harmless.

### Defaults and reset

- MUST expose factory defaults equivalent to the current hardcoded filters:
  - `/home/*/.codex/**`
  - `/home/*/.cache/**`
  - `/tmp/**`
  - `/var/tmp/**`
  - `/proc/**`
  - `/sys/**`
  - `/dev/**`
  - `/run/**`
- MUST let users remove any default pattern.
- MUST restore exactly the factory list when Reset to defaults is confirmed/activated.

### Persistence

- MUST store the global filter list in `localStorage` when running on the stable default viewer origin.
- MUST load persisted filters on page load when available and valid.
- MUST fall back to factory defaults if persisted data is missing, malformed, or unusable.
- MUST not require server persistence or JSONL modifications.
- SHOULD tolerate `localStorage` exceptions, such as private browsing or storage quota failures, by keeping filters in memory for the current page session.

### UI wording

- MUST rename user-facing "Noise" language to "Filters".
- MUST replace `Show noise` with wording that reflects the new model, for example `Show filtered`.
- MUST keep top-bar event counts understandable, e.g. total events and filtered event count.

### Filter editor UI

- MUST be reachable from the top bar.
- MUST show the current filter pattern list.
- MUST let users add, edit, remove, and reset patterns.
- MUST validate patterns before commit and surface validation failures in the UI.
- MUST trigger re-aggregation and re-render after every committed change.
- SHOULD be usable with keyboard and pointer input.
- SHOULD avoid putting path/filter data in the URL, history, or document title.

### Context actions

- MUST provide right-click/context-menu actions on treemap and table items.
- MUST offer exact-path and subtree (`/path/**`) patterns for right-click on a single item.
- MUST show an editable preview before committing right-click patterns.
- MUST provide multi-select support for table/treemap items sufficient to add multiple exact paths to filters.
- MUST show all multi-select patterns before committing.
- MUST not create subtree patterns from multi-select in v1.
- SHOULD make no-op additions safe if a selected pattern already exists.

### Aggregation behavior

- MUST retain all sanitized events received by the browser in arrival order.
- MUST replay the full event buffer after filter changes rather than reconnecting to `/events`.
- MUST continue to ingest live SSE events after filter changes.
- MUST keep filtered-event counting consistent with the current all-paths-match rule, extended to user filters.
- MUST keep tombstoned paths hidden regardless of `Show filtered` or custom filters.
- MUST keep metric semantics (`bytes`, `events`, `recent`, `last_touched_ms`) unchanged except for which paths/events are filtered from snapshots/counts.

### Port behavior

- MUST default the viewer CLI/server to port `7878`.
- MUST fall back to an OS-chosen ephemeral port if `7878` cannot be bound.
- MUST continue to bind only to `127.0.0.1`.
- MUST continue to support `--port PORT`.
- SHOULD make it clear from the printed URL which port was used.

## Non-functional requirements

- Performance: For traces around the documented ~10^4 events, full replay after a filter change SHOULD complete quickly enough for interactive use on a developer laptop.
- Memory: v1 MAY use memory linear in event count for the retained event buffer.
- Security/privacy: The viewer MUST continue to render path strings using text APIs rather than HTML injection, MUST not send raw syscall/command/process fields to the browser, MUST not log path data through new server endpoints, and MUST keep all filtering client-side.
- Reliability: Malformed persisted filter data MUST not break the viewer page.
- Maintainability: JS aggregation semantics and Python oracle tests MUST remain in lock-step.

## User experience notes

A reasonable top-bar shape is:

- `Filters` button or summary (`Filters (8)`).
- `Show filtered` checkbox.
- Existing metric controls, live badge, counts, Up button, and breadcrumb.

A reasonable editor can be a modal/dialog, popover, or inline panel. The exact visual treatment is an implementation choice, but it must support the required operations and validation feedback.

A right-click flow should not immediately mutate filters without confirmation; it should present editable pattern text first. For a directory `/work/build`, the subtree proposal is `/work/build/**`. For a file `/work/build/out.log`, the exact proposal is `/work/build/out.log`; if a subtree option is offered for files, it should be clearly based on the selected path as specified (`/path/**`) or parent subtree only if the implementation deliberately labels it that way.

## Examples

### Add a project build directory

1. User right-clicks `/home/alice/project/build` in the table.
2. User chooses `Add subtree to Filters`.
3. Preview shows `/home/alice/project/build/**`.
4. User commits.
5. The event buffer replays client-side; treemap and table hide the build subtree; filtered count updates.

### Add multiple generated files

1. User multi-selects `/work/a.log`, `/work/b.log`, and `/work/c.tmp`.
2. User chooses `Add selected paths to Filters`.
3. Preview lists the three exact paths.
4. User commits.
5. The three exact paths are added; no subtree patterns are generated.

### Mixed rename remains visible

A rename from `/tmp/build-output` to `/home/alice/project/output` is not counted as filtered if `/home/alice/project/output` does not match a filter, because not all non-empty paths on the event match filters.

## Acceptance criteria

- Starting the viewer with no saved filter state shows factory filters and hides the same default paths as before.
- Users can add, edit, remove, and reset filters from the top bar.
- Removing a default filter can reveal that path class when `Show filtered` is enabled or when the filter no longer applies in the default hidden view.
- Custom filters persist across page reloads on `http://127.0.0.1:7878/`.
- If port `7878` is occupied and no explicit `--port` is provided, the viewer still starts on an ephemeral loopback port.
- Filter changes re-render from retained events without reconnecting to `/events`.
- Right-click exact/subtree add flows work for treemap and table items with editable preview.
- Multi-select exact-path add flow previews all patterns before commit.
- Tombstoned rename sources remain hidden regardless of filter settings.
- Event-level filtered counts use the all-paths-match rule for custom filters.
- Existing viewer tests continue to pass, with updated tests for the new filter behavior and port default.

## Suggested test scenarios

- Glob compiler unit tests for `*`, `**`, `?`, anchoring, invalid relative patterns, and literal regex metacharacters in paths.
- Aggregator JS/Python parity tests with custom filters and changed filter lists.
- Event-level filter tests for all-noisy/all-filtered events, mixed rename paths, and no-path events.
- Snapshot tests confirming tombstones win over filters.
- Browser/static JS tests for editor state operations and localStorage fallback/malformed data.
- Table/treemap interaction tests for context-menu preview and multi-select exact path generation.
- Server/CLI tests for default port `7878`, collision fallback to ephemeral, explicit `--port`, and loopback-only binding.
- Documentation update checks for user-facing "Filters" terminology.

## Risks and mitigations

- **Glob edge cases:** Clearly specify a small glob language and test it directly.
- **Replay cost:** Accept O(events) replay for v1; target the existing ~10^4 event envelope and avoid heavier indexed structures in scope.
- **localStorage availability:** Treat persistence as best effort and keep the current session functional without it.
- **UI complexity:** Keep the editor and context actions simple; defer import/export and advanced filter categories.
- **Semantic drift between JS and Python oracle:** Update both implementations and parity tests together.
