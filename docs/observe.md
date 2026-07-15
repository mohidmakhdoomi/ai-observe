# ai-observe layered filesystem observer

`ai-observe` observes filesystem changes made during a wrapped command session.
The default mode is a **layered observer**:

- `strace` supplies live, process-tree-scoped direct evidence.
- start/end snapshots over watched roots backstop missed net changes.
- emitted events carry provenance so direct and inferred evidence stay distinct.

The intended product promise is:

> ai-observe reports every **net** file create, modify, or delete visible under configured watched roots during a session by combining a live event stream from the wrapped Linux process tree with session-boundary snapshot reconciliation. Events carry provenance so users can distinguish directly observed changes from inferred changes. Activity outside watched roots and changes by remote or hosted agents are not observed.

It does **not** promise that any single live backend captures every mutation perfectly.

## Severe sensitive-data risk

Observer artifacts can contain absolute paths, command arguments, raw syscall text,
file metadata, snapshot/manifest-derived metadata, warnings, and derived session state.
Treat all of these as sensitive:

- `.trace`
- `.jsonl`
- `.jsonl.partial`
- `.jsonl.rebuilt`
- `.meta.json`

Store `.codev/observe/` carefully, and keep it **out of commits, uploads, and
public logs until you have reviewed its contents**. Redaction is not
implemented.

## Quick start

### Install and observe a command

Installing the package (`pip install .` from a checkout) provides two console
scripts: `ai-observe` and `ai-observe-viewer`.

```bash
ai-observe --session my-run -- python -c 'from pathlib import Path; Path("x").write_text("y")'
ai-observe -- bash -lc 'echo hi > generated.txt'
```

If you need to force the executable while preserving arguments:

```bash
AI_OBSERVE_REAL_COMMAND=/opt/tools/tool-real ai-observe -- tool arg1 arg2
```

From a checkout without installing, `bin/ai-observe` behaves the same way.

### Named shims (checkout-only, opt-in)

The named tool shims (`bin/claude`, `bin/codex`, `bin/gemini`,
`bin/opencode`) observe an AI tool transparently under its own name. They are
deliberately **not** installed as console scripts — they would shadow the
real tools — and remain checkout-only. To opt in, symlink or copy the shims
you want into a directory you control (or use `bin/` directly) and prepend it
to `PATH`:

```bash
export AI_OBSERVE_REAL_CODEX="/absolute/path/to/real/codex"
export PATH="$PWD/bin:$PATH"
codex "implement feature"
```

Other named shims work the same way:

- `AI_OBSERVE_REAL_CLAUDE` → `bin/claude`
- `AI_OBSERVE_REAL_GEMINI` → `bin/gemini`
- `AI_OBSERVE_REAL_OPENCODE` → `bin/opencode`

Always set the real executable to an **absolute** path before prepending the
shim directory to `PATH`, or the shim can resolve itself. For Codex
compatibility, `CODEV_OBSERVE_REAL_CODEX` still works during the
compatibility window.

## Runtime model

Default mode is Linux-first and uses:

```bash
strace -f -qq -ttt -s 4096 -yy -o <trace-file> -e trace=%file,%desc,%process <real-command> <args...>
```

The wrapper uses argv arrays, not shell interpolation.

### Backend selection

`AI_OBSERVE_BACKENDS` controls which backends run:

- `strace,snapshot` (default)
- `strace`
- `snapshot`

Use `strace` or `snapshot` mainly for troubleshooting. The supported low-friction product path remains the default layered mode.

- `strace` mode keeps live direct attribution but has no snapshot backstop.
- `snapshot` mode skips strace wrapping and reports only inferred net changes under watched roots.
- invalid backend names fail before the child command launches.

## Watched roots and snapshot reconciliation

Snapshot reconciliation runs only under explicit watched roots.

In the default layered mode, those watched roots are also the visibility
boundary for direct `strace` events: direct events outside the watched roots are
not emitted into the session JSONL.

- `AI_OBSERVE_ROOTS` is a path list; on Linux use `:` separators, for example:

  ```bash
  AI_OBSERVE_ROOTS=/repo:/tmp/agent-work
  ```

- if unset or empty, watched roots default to the launch cwd.
- roots are resolved to absolute paths.
- missing roots are warned about and skipped.
- overlapping roots keep the ancestor and skip descendants.
- if no usable roots remain, the session fails before launch and records diagnostics in `<session>.meta.json`.

The snapshot baseline is captured **synchronously before the child command starts**.
A second manifest is captured after the child exits. The diff produces schema-v2
`snapshot` / `inferred` events for net creates, modifies, deletes, and conservative renames.

### Snapshot controls

| Variable | Meaning |
| --- | --- |
| `AI_OBSERVE_ROOTS` | Watched roots for snapshot reconciliation. Defaults to launch cwd. |
| `AI_OBSERVE_SNAPSHOT_HASH=1` | Hash regular files during snapshots to detect content changes that metadata alone might miss. |
| `AI_OBSERVE_SNAPSHOT_EXCLUDE` | Extra exclude patterns, separated by `:` or newlines and matched as documented below against normalized root-relative paths / path segments. |
| `AI_OBSERVE_SNAPSHOT_MAX_FILES` | Per-session safety cap for manifest entries. Over-cap roots are marked incomplete and warned. |

Built-in excludes suppress common high-noise paths such as `.git`, `node_modules`, `__pycache__`, `.codev/observe/**`, `*.pyc`, swap files, backup files, `.DS_Store`, and `.nfs*`.
Project lockfiles are **not** excluded by default.

Exclude matching rules for `AI_OBSERVE_SNAPSHOT_EXCLUDE`:

- patterns are matched against normalized **root-relative** paths
- patterns may be separated by `:` or newlines
- a subtree glob such as `foo/**` matches `foo` and anything below it within the watched root
- a suffix glob such as `**/*.pyc` matches matching suffixes anywhere under the watched root
- a bare segment or basename such as `node_modules` or `.nfs*` matches any path segment with that name

Examples:

```text
foo/**        # match a root-relative subtree
**/*.pyc      # match Python bytecode anywhere under the root
node_modules  # match any path segment named node_modules
```

## Artifacts and precedence

Typical session artifacts:

```text
.codev/observe/<session>.trace
.codev/observe/<session>.jsonl
.codev/observe/<session>.jsonl.partial
.codev/observe/<session>.jsonl.rebuilt
.codev/observe/<session>.meta.json
```

Meaning:

- `.trace`: raw strace output when the strace backend runs.
- `.jsonl`: canonical event stream in normal operation.
- `.jsonl.partial`: partial direct events when parsing fails before a complete canonical direct stream exists.
- `.jsonl.rebuilt`: full-trace rebuild used when a live-timeout recovery leaves `.jsonl` partial.
- `.meta.json`: warning/diagnostic sidecar plus artifact roles.

The sidecar records which event artifact is authoritative.
In normal sessions that is `.jsonl`. In live-timeout rebuild sessions it can be `.jsonl.rebuilt`.
The browser viewer reads `<session>.meta.json` and exposes non-sensitive banner state for rebuilt / partial artifacts and snapshot diagnostics.

## Event schema and provenance

New output uses **schema version 2**.

Required provenance fields:

- `schema_version: 2`
- `source`: `strace` or `snapshot`
- `confidence`: `direct` or `inferred`

Strace events use:

```json
{
  "schema_version": 2,
  "source": "strace",
  "confidence": "direct"
}
```

Snapshot events use:

```json
{
  "schema_version": 2,
  "source": "snapshot",
  "confidence": "inferred"
}
```

Example direct event:

```json
{
  "schema_version": 2,
  "timestamp": "2026-05-19T13:00:00.000000Z",
  "session_id": "20260519T130000Z-12345-abcd",
  "invocation_id": "20260519T130000Z-12345-abcd",
  "operation": "modify",
  "path": "/repo/app.py",
  "old_path": null,
  "new_path": null,
  "source": "strace",
  "confidence": "direct",
  "result": 1
}
```

Example inferred event:

```json
{
  "schema_version": 2,
  "timestamp": "2026-05-19T13:05:00.000000Z",
  "session_id": "20260519T130000Z-12345-abcd",
  "invocation_id": "20260519T130000Z-12345-abcd",
  "operation": "modify",
  "path": "/repo/app.py",
  "old_path": null,
  "new_path": null,
  "source": "snapshot",
  "confidence": "inferred",
  "snapshot": {
    "before": {"type": "file", "size": 1200, "mtime_ns": 1, "mode": 33188},
    "after": {"type": "file", "size": 1250, "mtime_ns": 2, "mode": 33188}
  },
  "result": 0
}
```

### Schema compatibility

- existing schema-v1 JSONL remains viewable.
- viewer/tailer code treats missing provenance as `strace` / `direct`.
- higher schema versions are accepted only when they still carry the viewer-safe fields that current consumers can normalize.

## Browser privacy posture

The browser viewer is local-only and intentionally strips sensitive fields before sending events to the page.
The page receives safe display fields such as:

- `schema_version`
- `timestamp`
- `operation`
- `path`, `old_path`, `new_path`
- `result`
- `source`
- `confidence`

It does **not** receive raw syscall text, command argv, PID/process data, session ids, or full snapshot manifests.

See [docs/viewer.md](viewer.md) for provenance badges, source filters, and artifact banners.

## Environment variables

Preferred names are `AI_OBSERVE_*`. Where legacy aliases still exist, `AI_OBSERVE_*` wins when both are set.

| Preferred variable | Legacy alias | Purpose |
| --- | --- | --- |
| `AI_OBSERVE_REAL_CODEX` | `CODEV_OBSERVE_REAL_CODEX` | Real Codex executable for `bin/codex`. |
| `AI_OBSERVE_REAL_CLAUDE` | none | Real Claude executable for `bin/claude`. |
| `AI_OBSERVE_REAL_GEMINI` | none | Real Gemini executable for `bin/gemini`. |
| `AI_OBSERVE_REAL_OPENCODE` | none | Real OpenCode executable for `bin/opencode`. |
| `AI_OBSERVE_REAL_COMMAND` | none | Forced executable for generic mode; replaces only `argv[0]`. |
| `AI_OBSERVE_DIR` | `CODEV_OBSERVE_DIR` | Observe directory. Relative paths resolve from launch cwd. |
| `AI_OBSERVE_DISABLE=1` | `CODEV_OBSERVE_DISABLE=1` | Bypass observation and exec the resolved real command. |
| `AI_OBSERVE_SESSION_ID` | `CODEV_OBSERVE_SESSION_ID` | Requested session id. Unsafe filename characters become `_`. |
| `AI_OBSERVE_STRICT_PARSE=1` | `CODEV_OBSERVE_STRICT_PARSE=1` | Parsing failures make the wrapper exit nonzero after the child exits. |
| `AI_OBSERVE_INCLUDE_LOG_WRITES=1` | `CODEV_OBSERVE_INCLUDE_LOG_WRITES=1` | Include active trace/JSONL artifacts if the child touches them. |
| `AI_OBSERVE_ALLOW_SYMLINK_DIR=1` | `CODEV_OBSERVE_ALLOW_SYMLINK_DIR=1` | Allow a symlink final observe dir. |
| `AI_OBSERVE_QUIET=1` | `CODEV_OBSERVE_QUIET=1` | Suppress the sensitive-log warning. |
| `AI_OBSERVE_LIVE_PARSE=0` | `CODEV_OBSERVE_LIVE_PARSE=0` | Disable live streaming; parse post-hoc only. |
| `AI_OBSERVE_LIVE_POLL_MS` | `CODEV_OBSERVE_LIVE_POLL_MS` | Live tailer poll interval in milliseconds. |
| `AI_OBSERVE_LIVE_JOIN_TIMEOUT` | `CODEV_OBSERVE_LIVE_JOIN_TIMEOUT` | Seconds to wait for live tailer drain after strace exits. |
| `AI_OBSERVE_SIGNAL_GRACE` | `CODEV_OBSERVE_SIGNAL_GRACE` | Seconds to wait between forwarded termination signals before escalation. |
| `AI_OBSERVE_ROOTS` | none | Watched roots for snapshot reconciliation. |
| `AI_OBSERVE_SNAPSHOT_HASH=1` | none | Enable content hashing in snapshots. |
| `AI_OBSERVE_SNAPSHOT_EXCLUDE` | none | Extra snapshot exclude patterns. |
| `AI_OBSERVE_SNAPSHOT_MAX_FILES` | none | Snapshot manifest entry cap. |
| `AI_OBSERVE_BACKENDS` | none | Backend selection. Default `strace,snapshot`; troubleshooting values `strace` and `snapshot`. |
| `AI_OBSERVE_NESTED=1` | none | Internal recursion guard passed into traced children so nested shims direct-exec the real binary instead of launching nested strace. Not a general user toggle. |

## Limits and non-goals

What the layered observer **does not** cover:

- activity outside configured watched roots
- remote or hosted agent filesystem changes that never occur locally
- perfect byte-level attribution for `mmap` writes
- full macOS / Windows live tracing backends
- fanotify / inotify / eBPF in this release

Important caveats:

- snapshot events are **post-hoc net changes**, not a real-time stream.
- create-then-delete ephemeral files can still be missed if no direct event is captured and the final snapshot no longer contains the file.
- snapshot events do not imply process attribution.
- already-running helpers or external daemons outside the traced process tree are invisible to `strace`; snapshot only backstops the net effect under watched roots.

## Troubleshooting

- **Missing `strace`**: install `strace`, or use `AI_OBSERVE_BACKENDS=snapshot` for snapshot-only troubleshooting, or `AI_OBSERVE_DISABLE=1` to bypass observation entirely.
- **Ptrace denied**: sandbox, seccomp, or Yama may block the default backend.
- **Recursion / wrong binary**: set an absolute `AI_OBSERVE_REAL_<PROGRAM>` path before prepending `bin/` to `PATH`.
- **No usable roots remain**: inspect `<session>.meta.json` for `missing_root`, `overlapping_root`, and related snapshot diagnostics.
- **Partial or rebuilt artifacts**: inspect `.jsonl.partial`, `.jsonl.rebuilt`, and `.meta.json`; the viewer banner will also surface artifact status.
