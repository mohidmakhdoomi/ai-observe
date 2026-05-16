# Architecture Notes

## Generic command observer

The filesystem observer is a command-oriented wrapper rather than a Codex-only
integration. `src/ai_observe/observe.py` owns the shared tracing backend:
real-executable resolution, safe observe-directory/log creation, `strace -f`
process-tree execution, live parsing, parser-failure handling, signal
forwarding, and environment-variable compatibility.

Key invariants:

- `bin/ai-observe` is the generic checkout entry point and requires `-- command
  [args...]`; named shims in `bin/codex`, `bin/claude`, `bin/gemini`, and
  `bin/opencode` are thin launchers that pass a program name to the generic
  core.
- `src/ai_observe/codex_observe.py` remains a compatibility facade by aliasing
  the generic module so existing imports, helper access, and monkeypatch-based
  tests continue to exercise the real implementation.
- Public configuration prefers `AI_OBSERVE_*`. Legacy `CODEV_OBSERVE_*` aliases
  remain supported for the compatibility window, with preferred names taking
  precedence where both are set.
- Resolver logic must avoid recursive execution of observer wrappers while
  still finding real executables via explicit env vars, PATH, or adjacent
  `.real`/`.bin` files.
- JSONL schema version remains `1`; the existing `command` field records the
  resolved real executable argv passed under `strace`. Wrapper/tool metadata is
  intentionally deferred until a deliberate schema migration.

## Browser viewer configurable filters

The browser viewer keeps filtering entirely client-side. The server continues to serve sanitized JSONL events over SSE without changing the event payload contract; the browser owns the active filter list, a flat arrival-order event buffer, and the aggregator instance used for rendering.

Key invariants:

- `src/ai_observe/viewer/static/aggregator.js` is the canonical JavaScript aggregation implementation. `tests/_aggregator_oracle.py` mirrors its filter semantics for parity tests.
- Factory filters are absolute-path anchored glob patterns. The glob compiler supports `*`, `**`, and `?`, validates that patterns start with `/`, and matches whole paths.
- Event-level filtering uses the all-paths-match rule for `path`, `old_path`, and `new_path`. Tombstoned rename sources remain hidden even when filtered paths are shown.
- `src/ai_observe/viewer/static/index.js` retains sanitized SSE events in arrival order and rebuilds the aggregator from that buffer whenever filter patterns change. Rebuilds do not reconnect to `/events`.
- Filter persistence is deliberately origin-scoped: the browser reads and writes `localStorage` only on `http://127.0.0.1:7878`. Fallback or explicit custom ports use session-only filters initialized from factory defaults.
- User-entered paths and filter patterns are rendered with DOM text APIs rather than `innerHTML`, `document.write`, or dynamic code execution.

The viewer CLI default port is `7878`. When no explicit `--port` is supplied and that port is unavailable, `src/ai_observe/viewer/__main__.py` falls back to an OS-chosen ephemeral loopback port; explicit ports retain normal bind-failure behavior.

## Browser viewer large-backlog delivery

Large static JSONL files are processed through a linear tailer and bounded SSE batching pipeline:

- `src/ai_observe/viewer/tailer.py` keeps at most an incomplete trailing JSONL fragment between polls. Complete lines from each read chunk are scanned with an index cursor rather than by repeatedly slicing the remaining buffer.
- `src/ai_observe/viewer/server.py` stores sanitized events in an append-only broadcaster. Each SSE client snapshots the current event count, receives backlog events up to that watermark, then receives later events from the next index. This preserves no-gap/no-duplicate semantics per client.
- Backlog and live slices are sent as bounded `append_batch` SSE frames. The browser remains compatible with legacy single-event `append` frames.
- `src/ai_observe/viewer/static/index.js` appends all received events to the same retained event buffer used for filter replay, regardless of whether they arrived as `append` or `append_batch`.
- Sparse live events are sent immediately as currently available batches; the server does not wait to fill a batch.
