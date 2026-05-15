# Browser viewer for observer JSONL

`ai_observe.viewer` is a local, browser-based visualizer for the filesystem-event `.jsonl` streams produced by the Codex filesystem observer. It is modeled on WinDirStat / TreeSize: a hierarchical treemap shows where activity concentrates, and an indented table shows the same tree with sortable columns.

The viewer is read-only. It never writes to the source JSONL.

## Invocation

```bash
PYTHONPATH=src python3 -m ai_observe.viewer .codev/observe/<session-id>.jsonl
```

Behavior:

1. Validates that the path exists and is a regular file.
2. Starts a small HTTP server bound to `127.0.0.1` only.
3. Prints the localhost URL to stderr.
4. Opens the URL in a browser unless `--no-browser` is passed.
5. Replays the JSONL from the beginning, then keeps tailing the file for appended events.
6. Streams sanitized display events to the page with Server-Sent Events (SSE).

Flags:

- `--port PORT`: bind a specific TCP port on `127.0.0.1`.
- `--no-browser`: do not call `webbrowser.open`; print the URL only.

When `--port` is not supplied, the viewer tries `127.0.0.1:7878`. If that stable default port is already in use, it falls back to an OS-chosen ephemeral loopback port and prints the actual URL. If `--port PORT` is supplied explicitly, that exact port is used and normal bind failures are reported instead of falling back.

There is intentionally no `--host` flag. The viewer is local-only.

## Live and static use

Live mode is the primary workflow:

```bash
# Terminal A: run Codex through the observer wrapper
codex "implement feature"

# Terminal B: visualize the in-flight stream
PYTHONPATH=src python3 -m ai_observe.viewer .codev/observe/<session-id>.jsonl
```

Static review uses the same command against a completed JSONL. The server reaches EOF and idles; the page shows the final aggregate state.

A page reload reconnects to `/events`; the server replays the whole file from the start so the browser rebuilds its aggregation state. Filter changes do not reconnect: the browser retains received events in arrival order and replays that in-memory buffer through the aggregator.

## Page layout

The page has three main areas:

- **Top bar**: metric toggle (`Bytes`, `Events`, `Recent`), `Filters` editor button, optional `Add selected to Filters` button, `Show filtered`, live/idle/shutdown badge, event counters, `▲ Up`, and a breadcrumb for treemap drill-down.
- **Treemap**: rectangles grouped by directory. Rectangle area is the selected metric. Clicking a directory rectangle drills into that subtree; clicking a file selects it. Ctrl/Cmd-click toggles a rectangle in the multi-selection set. Right-click opens an add-to-filters preview. Hovering shows a tooltip with path, bytes, event count, and last-touched timestamp.
- **Tree table**: indented rows with `Path`, `Bytes written`, `Events`, and `Last touched`. Sorting is sibling-local: rows are reordered within their parent but remain in the hierarchy. Clicking a directory row expands/collapses it and selects it. Ctrl/Cmd-click toggles a row in the multi-selection set, and Shift-click selects a visible row range from the last multi-selection anchor. Right-click opens an add-to-filters preview.

Selection and hover are linked between the treemap and table. Drill state, selections, and filter preview data are in-memory UI state only; they are not written into the URL, history, or document title.

## Metrics

Per file path `p`, with `events(p)` being events touching `path`, `old_path`, or `new_path`:

- **Bytes**: sum of positive integer `result` values for `modify` events only. Other operations and non-positive results contribute zero bytes.
- **Events**: count of events for the path, regardless of operation.
- **Recent**: recency-weighted event count using a fixed exponential decay. The decay is computed relative to the most recently ingested event timestamp so replay is deterministic.

Directory totals are sums of child totals for all three metrics. `Last touched` is the maximum child timestamp.

## Rename behavior

Rename events are detected by `operation == "rename"`, not by missing paths. The observer schema carries `old_path`, `new_path`, and `path` for renames.

When `A` is renamed to `B`:

- bytes accumulated at `A` move to `B`;
- event count at `B` receives `events(A) + 1`, charging the rename to the destination;
- recency state moves to `B` and receives one fresh contribution;
- `last_touched(B)` becomes the max of the old source, old destination, and rename timestamp;
- `A` is tombstoned and hidden, even when `Show filtered` is enabled;
- if `B` already has state, the migration is additive.

Fresh later events at `A` clear the tombstone and treat `A` as a new path.

## Filters

Filters are browser-side exclude patterns. They hide matching paths from both panels by default, but they never discard events from the retained event buffer. `Show filtered` reveals matching non-tombstoned paths without changing the active filter list.

An event is counted as filtered if all non-null paths on that event match the active filter list. Mixed-path events, such as a rename from a temporary directory into a source tree, are retained in the default view when any non-empty path does not match a filter.

Factory default filters:

- `/home/*/.codex/**`
- `/home/*/.cache/**`
- `/tmp/**`
- `/var/tmp/**`
- `/proc/**`
- `/sys/**`
- `/dev/**`
- `/run/**`

The top bar shows both total ingested events and filtered-event count for the current active filters.

### Glob syntax

Filter patterns are absolute-path anchored globs and match whole paths:

- `*` matches zero or more non-`/` characters within one path segment.
- `**` matches zero or more complete path segments when used as its own segment.
- `?` matches exactly one non-`/` character.
- Character classes and brace expansion are not supported.

Examples:

- `/tmp/**` matches `/tmp`, `/tmp/a`, and `/tmp/a/b`.
- `/home/*/.cache/**` matches `/home/alice/.cache` and `/home/bob/.cache/pip/x`.
- `/work/build/*` matches `/work/build/a.o` but not `/work/build/obj/a.o`.
- `/work/build/**` matches `/work/build`, `/work/build/a.o`, and `/work/build/obj/a.o`.
- `/work/?.txt` matches `/work/a.txt` but not `/work/ab.txt` or `/work/dir/a.txt`.

Exact path filters match only the literal path. To hide a directory and descendants, use a subtree pattern such as `/work/build/**`.

### Filter editor

Use the `Filters (N)` top-bar button to open the editor. The editor shows the current list and supports:

- adding a pattern;
- editing a pattern inline and saving it;
- removing any pattern, including factory defaults;
- `Reset to defaults`, which replaces the entire current list with the factory default list.

Invalid patterns, such as relative paths, are rejected before commit and shown in the UI. Every committed change persists when eligible, rebuilds the aggregate from the retained event buffer, and re-renders the treemap/table without reconnecting to `/events`.

### Persistence

Filter persistence uses browser `localStorage` only on the stable default origin:

```text
http://127.0.0.1:7878
```

On that origin, custom filters survive page reloads and later viewer runs. On fallback ephemeral ports or explicit custom ports, the page starts with factory defaults and keeps filter edits in memory for the current session only; it does not read or write filter storage for those origins. Malformed or unavailable stored data falls back to factory defaults.

### Add to Filters from items

Right-click a table row or treemap tile to open an editable preview:

- directory items offer an exact path choice such as `/work/build` and a subtree choice such as `/work/build/**`;
- file items offer exact path only.

Commit the preview to add the selected pattern through the same validation, persistence, and replay path as the editor.

For multiple items, Ctrl/Cmd-click rows or tiles to toggle them in the selection set. In the table, Shift-click also selects a range over currently visible rows. When at least two paths are selected, the top bar shows `Add N selected to Filters`; this opens a preview containing exact-path patterns only. Duplicate additions are harmless.

## Replay and memory envelope

The browser retains sanitized SSE events in a flat arrival-order buffer. Filter list changes rebuild a fresh aggregator by replaying that buffer with the current filters, then render a new snapshot. Live events appended while the page is open are appended to the same buffer and included exactly once.

Startup and backlog delivery are optimized for larger local traces:

- the JSONL tailer scans newly read chunks linearly and keeps only an incomplete trailing line between polls;
- the SSE stream delivers sanitized events in bounded `append_batch` frames while the browser remains compatible with legacy single-event `append` frames;
- sparse live appends are sent as soon as they are observed rather than delayed to fill a batch;
- selection pruning skips the snapshot-tree walk when no paths are selected.

This v1 design still uses memory linear in event count. The expected interactive filter-replay envelope remains roughly `10^4` events because filter edits intentionally replay the retained browser buffer on the main thread. Larger traces, including traces around `8 * 10^4` schema-v1 events, should no longer hit the previous tailer/SSE startup pathologies, but bounded retention/windowing and filter-independent indexes remain out of scope for this version.

## Security and privacy posture

Observer JSONL can contain sensitive absolute paths, command arguments, and raw syscall text. Treat it as private audit output.

The viewer reduces accidental leakage by:

- binding only to loopback (`127.0.0.1`);
- not providing a remote-bind option;
- rendering path and pattern strings with text APIs, not `innerHTML`;
- not sending `raw_syscall`, `command`, process details, or other non-display fields to the browser;
- keeping the document title fixed as `ai_observe viewer`;
- not putting selected paths, filter data, or drill state in the URL, query string, history, or title.

The page still displays absolute paths to the operator. Be careful when sharing screenshots or screen recordings.

## Smoke testing

Synthetic fixtures used by the test suite live under:

```text
tests/fixtures/viewer/
```

For a larger local walkthrough, use the reference trace mentioned in the feature spec when it is present in the repository checkout:

```bash
PYTHONPATH=src python3 -m ai_observe.viewer --no-browser .codev/observe/20260513T165110Z-16975-8f23.jsonl
```

Expected v1 envelope: traces around `10^4` events should replay filters within a few seconds on a developer laptop and remain interactive. Larger static backlogs should start substantially faster than the pre-batching implementation because tailer processing is linear and SSE callbacks are batched, but repeated filter changes on very large retained buffers can still be limited by full client-side replay. The reference trace is about 8,800 events.

Manual walkthrough checklist:

- page loads from an empty, live, or completed JSONL;
- printed URL shows `127.0.0.1:7878` when available;
- occupying `7878` before launch falls back to another loopback port;
- appending an event updates the page within about 1 second;
- default filters hide `/home/*/.codex/**` paths;
- `Show filtered` reveals hidden non-tombstoned paths and preserves state;
- the Filters editor can add, edit, remove, and reset patterns;
- valid custom filters persist across reloads on `http://127.0.0.1:7878`;
- fallback/custom-port filter edits do not load or save stable-port custom filters;
- right-clicking a directory previews exact and subtree patterns before commit;
- right-clicking a file previews an exact pattern before commit;
- Ctrl/Cmd multi-select plus `Add N selected to Filters` previews exact paths only;
- table Shift-click range selection selects visible rows for exact-path filtering;
- `Bytes`, `Events`, and `Recent` produce different layouts on realistic traces;
- directory rectangles drill down; breadcrumb and `▲ Up` return toward `/`;
- table sorting stays sibling-local and keeps selected rows visible.
