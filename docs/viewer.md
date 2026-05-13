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
6. Streams events to the page with Server-Sent Events (SSE).

Flags:

- `--port PORT`: bind a specific TCP port on `127.0.0.1`. The default is `0`, which asks the OS to choose an available ephemeral port.
- `--no-browser`: do not call `webbrowser.open`; print the URL only.

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

A page reload reconnects to `/events`; the server replays the whole file from the start so the browser rebuilds its aggregation state.

## Page layout

The page has three main areas:

- **Top bar**: metric toggle (`Bytes`, `Events`, `Recent`), `Show noise`, live/idle/shutdown badge, event counters, `▲ Up`, and a breadcrumb for treemap drill-down.
- **Treemap**: rectangles grouped by directory. Rectangle area is the selected metric. Clicking a directory rectangle drills into that subtree; clicking a file selects it. Hovering shows a tooltip with path, bytes, event count, and last-touched timestamp.
- **Tree table**: indented rows with `Path`, `Bytes written`, `Events`, and `Last touched`. Sorting is sibling-local: rows are reordered within their parent but remain in the hierarchy. Clicking a directory row expands/collapses it and selects it.

Selection and hover are linked between the treemap and table. Drill state is in-memory only; it is not written into the URL, history, or document title.

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
- `A` is tombstoned and hidden, even when `Show noise` is enabled;
- if `B` already has state, the migration is additive.

Fresh later events at `A` clear the tombstone and treat `A` as a new path.

## Default noise filter

By default, noisy system/cache paths are hidden from both panels. `Show noise` disables this filter without reconnecting because filtering happens in the browser after events are received.

An event is counted as filtered if all non-null paths on that event match the exclude list. Mixed-path events, such as a rename from a noisy temporary path into a source tree, are retained in the default view.

Default exclude patterns:

- `/home/*/.codex/**`
- `/home/*/.cache/**`
- `/tmp/**`
- `/var/tmp/**`
- `/proc/**`
- `/sys/**`
- `/dev/**`
- `/run/**`

The top bar shows both total ingested events and filtered-event count.

## Security and privacy posture

Observer JSONL can contain sensitive absolute paths, command arguments, and raw syscall text. Treat it as private audit output.

The viewer reduces accidental leakage by:

- binding only to loopback (`127.0.0.1`);
- not providing a remote-bind option;
- rendering path strings with text APIs, not `innerHTML`;
- not sending `raw_syscall`, `command`, process details, or other non-display fields to the browser;
- keeping the document title fixed as `ai_observe viewer`;
- not putting selected paths or drill state in the URL, query string, history, or title.

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

Expected v1 envelope: traces around `10^4` events should replay within a few seconds on a developer laptop and remain interactive. The reference trace is about 8,800 events.

Manual walkthrough checklist:

- page loads from an empty or completed JSONL;
- appending an event updates the page within about 1 second;
- default filtering hides `/home/*/.codex/**` noise;
- `Show noise` reveals hidden noisy paths and preserves state;
- `Bytes`, `Events`, and `Recent` produce different layouts on realistic traces;
- directory rectangles drill down; breadcrumb and `▲ Up` return toward `/`;
- table sorting stays sibling-local and keeps selected rows visible.
