# Architecture Notes

## Layered observer architecture

`ai-observe` is a command-oriented observer with a **layered backend model**.
The default user-facing behavior is low-friction and no-root:

1. `strace` provides live, process-tree-scoped direct evidence.
2. session-boundary snapshots over watched roots provide inferred net-change reconciliation.
3. raw events carry provenance so direct and inferred evidence can coexist safely.
4. the browser viewer remains local-only and privacy-preserving.

This architecture exists to improve completeness without pretending that any single live Linux backend sees every mutation.

### Product boundary

The supported promise is limited to:

- net creates / modifies / deletes
- under configured watched roots
- during the observed session
- on the local filesystem

Out of scope:

- remote or hosted agents that do not touch the local watched filesystem
- byte-level attribution for `mmap`
- fanotify / inotify / eBPF in this release

## Backend abstraction

`src/ai_observe/backends/` defines the first concrete backend seam.

Key pieces:

- `BackendCapabilities`
- `BackendState`
- `BackendSession`
- `Backend` protocol (`prepare`, `stop`, `finalize`)

Current concrete backends:

- `StraceBackend`
- `SnapshotBackend`

### Ordering invariants

The orchestration order is deliberate:

- **prepare order**: `snapshot`, then `strace`
- **finalize order**: `strace`, then `snapshot`

Why:

- snapshot baseline must complete before the child command launches
- strace must finalize first so snapshot deduplication can compare inferred events against the authoritative direct stream and artifact choice already determined by parser recovery

### Backend selection

`AI_OBSERVE_BACKENDS` is the public selection surface.

Supported values:

- `strace,snapshot` (default)
- `strace`
- `snapshot`

Invalid names fail before child launch.

The abstraction is intentionally small: it is concrete enough to support future fanotify / inotify / eBPF implementations later without forcing the viewer or event pipeline to be rewritten now.

## Generic command observer core

`src/ai_observe/observe.py` owns the shared wrapper concerns around the backend layer:

- real-executable resolution for named shims and generic mode
- safe observe-directory and artifact creation
- signal forwarding and exit-code normalization
- parser recovery artifact handling
- compatibility env-var aliases
- final meta-sidecar emission

Important invariants:

- named shims in `bin/codex`, `bin/claude`, `bin/gemini`, and `bin/opencode` are thin launchers over the same generic core
- `src/ai_observe/codex_observe.py` remains a compatibility alias to the generic module so existing imports and monkeypatch-heavy tests still hit the real implementation
- public configuration prefers `AI_OBSERVE_*`, with `CODEV_OBSERVE_*` aliases preserved where documented
- resolver logic must avoid recursive execution of observer shims

## Artifact contract

The observer may produce these sibling artifacts:

- `<session>.trace`
- `<session>.jsonl`
- `<session>.jsonl.partial`
- `<session>.jsonl.rebuilt`
- `<session>.meta.json`

Sidecar responsibilities:

- record parser status
- record artifact roles / authoritative event path
- summarize warnings
- summarize snapshot completeness / diagnostic counts

This keeps session-wide diagnostics out of the event stream itself.

## Provenance model

Schema-v2 raw events add:

- `schema_version: 2`
- `source`
- `confidence`

Current provenance mapping:

- direct strace event → `source: "strace"`, `confidence: "direct"`
- inferred snapshot event → `source: "snapshot"`, `confidence: "inferred"`

Viewer consumers normalize schema-v1 events as `strace` / `direct` so existing artifacts remain usable.

## Browser viewer invariants

The browser viewer continues to prioritize privacy and local-only access.

Key invariants:

- bind only to `127.0.0.1`
- never expose raw syscall text, argv, PID/process details, session ids, or unsanitized snapshot manifests to the page
- treat provenance, artifact state, and warning counts as safe display metadata
- preserve existing path-filter, rename, metric, and backlog semantics while layering in source visibility toggles and session banners

The browser aggregation model keeps filtering client-side:

- sanitized events are retained in arrival order
- path-filter changes replay that buffer
- source-visibility changes replay that buffer
- rebuild / partial artifact switching reconnects to the selected artifact stream, not to raw trace data

## Deferred kernel backends

The backend seam is specifically meant to keep future options possible:

- fanotify
- inotify
- eBPF

They are deferred until there is a concrete reason to pay the privilege, portability, or kernel-version cost.
The default release remains `strace` plus snapshot reconciliation.
