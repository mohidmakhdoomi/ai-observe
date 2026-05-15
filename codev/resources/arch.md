# Architecture Notes

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
