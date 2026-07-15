# Browser viewer for observer JSONL

`ai_observe.viewer` is a local browser UI for observer event artifacts. It accepts mixed direct and inferred streams, shows provenance in the UI, and lets you switch between canonical, rebuilt, and partial artifacts when sibling files exist.

The viewer is read-only and binds only to `127.0.0.1`.

## Invocation

With the package installed (`pip install .`):

```bash
ai-observe-viewer .codev/observe/<session>.jsonl
# equivalently:
python -m ai_observe.viewer .codev/observe/<session>.jsonl
```

From a checkout without installing:

```bash
PYTHONPATH=src python3 -m ai_observe.viewer .codev/observe/<session>.jsonl
```

You usually point it at the canonical `.jsonl`. If sibling `.jsonl.partial`, `.jsonl.rebuilt`, or `.meta.json` files exist, the viewer detects them and shows a session banner with artifact buttons and warning state.

Flags:

- `--port PORT`: bind a specific TCP port on `127.0.0.1`
- `--no-browser`: print the URL without calling `webbrowser.open`

The viewer tries `127.0.0.1:7878` by default. If that port is busy and you did not request one explicitly, it falls back to an OS-chosen ephemeral loopback port.

## What the page shows

Main UI areas:

- **Top bar**: metric toggle (`Bytes`, `Events`, `Recent`), `Filters`, `Add selected to Filters`, `Show filtered`, source toggles (`Strace`, `Snapshot`), live badge, counters, and navigation controls.
- **Session banner**: non-sensitive artifact state from `<session>.meta.json`, including rebuilt-authoritative notices, partial-direct notices, parser status, warning counts, and snapshot diagnostic counts.
- **Treemap**: WinDirStat-style area view.
- **Tree table**: hierarchical rows with sortable columns.

## Provenance and compatibility

The viewer accepts:

- schema-v1 events
- schema-v2 events
- mixed streams containing both

Compatibility rules:

- missing `schema_version` is treated as schema v1
- missing `source` becomes `strace`
- missing `confidence` becomes `direct`
- future schema versions are accepted only if the current viewer can still normalize the safe display fields

### Provenance rendering

Rows and tooltips surface provenance without exposing raw trace details.

Typical cues include:

- source badges such as `strace` and `snapshot`
- confidence badges such as `direct` and `inferred`
- tooltip summaries that show mixed-source composition for a node or file

## Source filtering

The top bar includes source visibility toggles:

- **Strace**
- **Snapshot**

These source filters work alongside path filters. Hiding one source does not mutate the retained browser event buffer; the browser rebuilds the aggregate from the same sanitized event stream.

## Artifact handling and banners

The viewer understands sibling artifact sets:

- `<session>.jsonl`
- `<session>.jsonl.rebuilt`
- `<session>.jsonl.partial`
- `<session>.meta.json`

Banner behavior is driven by sanitized session metadata:

- if `.rebuilt` is authoritative, the banner says so and exposes an artifact switch button
- if `.partial` exists, the banner warns that partial direct evidence is available separately
- parser timeout / rebuild / partial statuses are surfaced without exposing raw trace text
- snapshot diagnostics and warning counts are summarized numerically, not by dumping full manifests or raw warning payloads into the page

## Security and privacy posture

The page receives only sanitized event fields:

- `schema_version`
- `timestamp`
- `operation`
- `path`, `old_path`, `new_path`
- `result`
- `source`
- `confidence`

The browser does **not** receive:

- `raw_syscall`
- `command`
- `pid`
- `process`
- `session_id`
- `invocation_id`
- unsanitized snapshot manifests or attribution details

The server is loopback-only. There is no remote-bind flag.

## Metrics and aggregation

The viewer aggregates events by path and directory tree.

Metrics:

- **Bytes**: positive-byte `modify` results
- **Events**: event count
- **Recent**: recency-weighted count

Rename handling and filtering remain compatible with earlier viewer behavior; provenance and artifact state are layered on top of that existing aggregation model.

## Path filters

Path filters are still browser-side and replay against the retained sanitized event buffer.

Highlights:

- factory defaults hide common noise paths
- filters can be added, edited, removed, and reset in the UI
- multi-selection exposes an `Add N selected to Filters` action in the top bar
- Right-click a table row or treemap tile to preview exact-path or subtree filters
- `Show filtered` reveals matching non-tombstoned paths without changing the active filter list
- filter persistence remains scoped to `http://127.0.0.1:7878`

## Live behavior

The server replays the selected artifact from the beginning, then tails it for appended events.
Backlog delivery uses bounded `append_batch` frames; sparse live events still appear promptly. The browser model is still memory linear in event count.

A page reload rebuilds state by replaying the artifact from the start.
Filter or source-visibility changes rebuild in-browser from the retained event buffer; they do not reconnect to `/events`.

## Practical workflow

```bash
# Terminal A (codex here is the opt-in checkout shim; see docs/observe.md)
codex "implement feature"

# Terminal B
ai-observe-viewer .codev/observe/<session>.jsonl
```

Useful things to check in the UI:

- source badges distinguish direct `strace` from inferred `snapshot`
- hiding `snapshot` shows only direct evidence
- hiding `strace` shows only inferred backstop events
- rebuilt / partial artifact buttons appear when the sidecar says they exist
- snapshot diagnostics are summarized in the session banner
