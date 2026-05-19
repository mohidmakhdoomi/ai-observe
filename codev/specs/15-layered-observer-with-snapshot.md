# Specification: Layered observer with snapshot reconciliation and provenance

## Summary

`ai-observe` currently observes filesystem mutations by launching one command under `strace -f`, parsing the traced process tree, and streaming schema-v1 JSONL into a local browser viewer. That default must remain: it is no-root, drop-in, and provides direct syscall evidence for the wrapped CLI and its descendants.

This feature changes the product from a single-live-backend observer into a layered observer. The target release combines:

1. stronger `strace` parser reliability;
2. schema-v2 provenance (`source`, `confidence`, optional attribution/object identity);
3. start/end snapshot reconciliation for explicit watched roots; and
4. viewer support for mixed direct and inferred event streams.

The intended promise after the feature is:

> ai-observe reports every net file create, modify, or delete visible under configured watched roots during a session by combining a live event stream from the wrapped Linux process tree with session-boundary snapshot reconciliation. Events carry provenance so users can distinguish directly observed changes from inferred changes. Activity outside watched roots and changes by remote or hosted agents are not observed.

The feature must not claim that `strace`, fanotify, eBPF, or any single live backend captures every mutation perfectly.

## Background and current state

### Existing behavior

- `src/ai_observe/observe.py` resolves a real command, runs it under `strace -f`, and writes `.trace`, `.jsonl`, and parser-failure `.jsonl.partial` artifacts below `.codev/observe/` by default.
- `src/ai_observe/trace_parser.py` parses selected file, descriptor, and process syscalls into JSONL events with `schema_version: 1`.
- The live parser appends events while the child runs; post-hoc parsing from the full `.trace` is available for some failure paths.
- The viewer tailer accepts schema-v1 events, sanitizes sensitive fields before sending them to the browser, and aggregates by path for the treemap/table UI.
- Public configuration prefers `AI_OBSERVE_*`, with `CODEV_OBSERVE_*` compatibility aliases for older Codex-oriented workflows.

### Current limitations

The current backend is useful but cannot honestly guarantee that every file created, modified, or deleted by an AI CLI session is tracked:

- **Process-tree scope:** external MCP servers, already-running daemons, IDE extensions, remote/hosted agents, and helper processes outside the traced tree are invisible to `strace -f`.
- **Parser/syscall coverage:** gaps exist for operations such as `open/openat/openat2(... O_CREAT ...)` without `O_EXCL`, `copy_file_range`, `sendfile`, some `splice` cases, extended attributes, `io_uring`, and other less common paths.
- **`mmap` dirty-page semantics:** a byte store to a mapped page is not cleanly represented as a live write syscall.
- **Live parsing reliability:** timeout or partial-artifact branches can leave users with incomplete or hard-to-discover data even when the full `.trace` could be rebuilt.
- **No event provenance:** future mixed sources need explicit source/confidence metadata before the viewer can represent their different levels of evidence.

## Baked decisions

The issue body contains no section named “Baked Decisions.” The following constraints are derived from the issue’s desired architecture and non-goals rather than from a baked-decisions section:

- Keep `strace` as the default live attribution backend.
- Add watched-root snapshot reconciliation as the completeness backstop.
- Add schema/source/confidence provenance before broadly mixing sources.
- Preserve viewer privacy posture; do not expose raw syscall, argv, PID, or process details to the browser unless a later spec deliberately changes that.
- Defer fanotify, inotify, and eBPF backends.

## Stakeholders and needs

- **AI CLI users** need a defensible answer to “what files changed during this session?” even when a helper, parser gap, or `mmap` write bypasses direct syscall events.
- **Users debugging trust/completeness** need to distinguish direct `strace` evidence from inferred snapshot reconciliation.
- **Viewer users** need mixed source streams to remain comprehensible through badges, filters, and existing aggregation semantics.
- **Project maintainers** need an architecture that keeps the no-root default but leaves a clean seam for optional future backends.
- **Security-conscious users** need the existing local-only viewer and sensitive-field sanitization preserved.

## Goals

1. Keep the existing low-friction `strace` workflow as the default live backend.
2. Improve parser coverage and recoverability for known strace blind spots that can be fixed locally.
3. Introduce schema-v2 event provenance while preserving schema-v1 ingestion compatibility.
4. Add watched-root snapshot reconciliation at session start and session end.
5. Synthesize conservative snapshot events for net creates, modifies, deletes, and optional identity-supported renames.
6. Deduplicate/correlate snapshot and strace events without hiding evidence incorrectly.
7. Update the viewer to surface source/confidence and optionally filter by source.
8. Introduce a backend abstraction only after both `strace` and snapshot sources are real enough to define the interface concretely.
9. Update documentation and product language to state the layered guarantee and its limits accurately.

## Non-goals

- Replacing `strace` as the default backend.
- Building fanotify, inotify, eBPF, auditd, macOS, or Windows live backends in this release.
- Observing remote or hosted agent filesystem changes that do not occur on the local watched filesystem.
- Byte-level attribution for `mmap` writes.
- Perfect real-time ordering for snapshot-inferred events; they are session-boundary reconciliation events.
- Exposing raw syscall text, command argv, PID/process metadata, or other sensitive trace fields in the browser UI.
- Capturing files created and deleted entirely within a session when both the live backend misses them and the final snapshot no longer contains them.
- Solving malicious evasion by programs intentionally hiding writes outside configured roots.

## Release scope

This SPIR project should deliver one PR containing phase commits, not one PR per phase. The release scope is:

1. **Parser hygiene and reliability** for the existing strace backend.
2. **Schema-v2 provenance** for emitted events plus v1 normalization in consumers.
3. **Snapshot reconciliation** for configured watched roots, with built-in and user excludes.
4. **Viewer provenance UX** for mixed v1/v2 and strace/snapshot streams.
5. **Backend abstraction** once `strace` and snapshot behavior exist.
6. **Documentation and tests** covering the new promise, configuration, compatibility, and mixed-source behavior.

Optional kernel backends are explicitly deferred.

## Solution exploration

### Approach A: Keep strace-only and document limitations

**Design:** Improve docs to say `strace` is process-tree scoped and can miss mmap/helper/parser-gap writes.

**Pros:** Smallest implementation; no schema or viewer changes.

**Cons:** Does not solve the core user problem. Users still have no backstop when a file changed but no event appeared. Does not prepare event model for future sources.

**Assessment:** Insufficient for this issue.

### Approach B: Replace strace with a privileged kernel backend

**Design:** Build fanotify or eBPF as the main backend for watched roots or system-wide filesystem activity.

**Pros:** Potentially better live coverage for writes by helper processes; eBPF can eventually reduce overhead or improve attribution in privileged deployments.

**Cons:** Breaks no-root/drop-in usage; still does not fully solve `mmap`; adds kernel/version/privilege complexity; weaker or different process attribution than current strace in common cases.

**Assessment:** Rejected for this release. Keep as future optional backends behind a stable interface.

### Approach C: Layer strace with snapshot reconciliation

**Design:** Continue using `strace` for direct live process-tree attribution. Add session-boundary manifests over explicit watched roots and diff them after the command exits. Tag events with source/confidence so direct and inferred evidence are visible.

**Pros:** Preserves current UX and attribution where available; catches net changes from mmap, parser gaps, helper processes, and sandbox fallbacks under configured roots; no new privileges; portable foundation for future non-Linux snapshot-only modes.

**Cons:** Snapshot events are post-hoc and inferred; cannot see create-then-delete ephemeral files; can be expensive for very large roots without excludes/caps; attribution is unavailable for snapshot-only changes.

**Assessment:** Recommended architecture.

### Approach D: Add backend abstraction first

**Design:** Refactor around a generic backend protocol before adding snapshot.

**Pros:** Cleaner architecture in theory.

**Cons:** The right interface is speculative with only one real backend. Risk of churn before snapshot requirements are known.

**Assessment:** Defer abstraction until after strace and snapshot sources exist.

## Functional requirements

### Parser hygiene and reliability

#### MUST

- Preserve existing strace-based command execution, signal forwarding, logging, and schema-v1-compatible behavior until schema-v2 is deliberately emitted.
- Parse successful `copy_file_range` and `sendfile` syscalls as file modification events when the destination fd/path can be resolved safely.
- Preserve or improve existing `splice` handling; add coverage only where the destination can be identified safely.
- Represent successful `open`, `openat`, `openat2`, and `creat` calls with `O_CREAT` as creates when the parser can confidently determine creation, including non-`O_EXCL` cases, while avoiding duplicate/confusing events.
- Parse extended attribute metadata operations (`setxattr`, `lsetxattr`, `fsetxattr`, `removexattr`, `lremovexattr`, `fremovexattr`) as metadata events when the target path/fd is known.
- If live parsing times out or otherwise fails in a recoverable way and the full `.trace` exists, rebuild a recoverable JSONL artifact from the full `.trace` instead of leaving only an opaque partial stream.
- Make partial/rebuilt parser artifacts discoverable to the viewer and documentation.
- Add a safe nested shim escape hatch: when an outer observed session sets `AI_OBSERVE_NESTED=1`, an inner `ai-observe`/shim invocation should direct-exec the resolved real binary rather than launching another nested strace, so the outer `strace -f` can observe descendants.

#### SHOULD

- Prefer safe false negatives over false positives for ambiguous syscall arguments.
- Preserve current parser-failure strict-mode semantics unless explicitly updated in docs/tests.
- Add regression tests using trace fixtures rather than requiring privileged or timing-sensitive live tests where practical.

### Schema-v2 provenance

#### MUST

- Emit schema-v2 events for new output once this phase lands.
- Include these top-level fields on schema-v2 events:
  - `schema_version: 2`
  - `source`: at least `"strace"` or `"snapshot"`
  - `confidence`: at least `"direct"` or `"inferred"`
- Strace-derived events MUST use `source: "strace"` and `confidence: "direct"`.
- Snapshot-derived events MUST use `source: "snapshot"` and `confidence: "inferred"` unless a more specific documented confidence is introduced.
- Keep existing v1 fields and meanings where applicable: `timestamp`, `session_id`, `invocation_id`, `operation`, `path`, `old_path`, `new_path`, `command`, `raw_syscall`, `result`, `pid`, and `process` for strace events.
- Preserve v1 ingestion compatibility in Python tailer/server and browser aggregation by treating missing `schema_version`, `source`, and `confidence` as `schema_version: 1`, `source: "strace"`, `confidence: "direct"`.
- Be forward-compatible with higher schema versions by warning/skipping only when a consumer cannot safely normalize the event.
- Do not send sensitive fields to the browser page. The sanitized browser event may include `schema_version`, `source`, and `confidence`, but must not include raw syscall, command argv, PID, process tree, or unsanitized attribution details.

#### SHOULD

- Add optional `attribution` for schema-v2 events only where it does not weaken privacy guarantees. If present in raw JSONL, it should be structured and may be omitted by browser sanitization.
- Add optional `object` identity such as `{ "dev": ..., "ino": ... }` when available.
- Document schema-v2 and v1 migration policy in `docs/observe.md`, `docs/viewer.md`, and architecture notes.

### Snapshot reconciliation

#### MUST

- Add explicit watched-root configuration via `AI_OBSERVE_ROOTS`, using a platform-appropriate path-list separator for the current platform. On Linux this is colon-separated, e.g. `/repo:/tmp/agent-work`.
- If `AI_OBSERVE_ROOTS` is unset or empty, default the watched roots sensibly to the launch cwd.
- Resolve watched roots to absolute paths. Missing roots should produce a clear warning and be skipped, not crash the observed command, unless no root remains.
- Capture a start manifest before or at session start and an end manifest after the child exits.
- Reconcile net creates, modifies, and deletes under configured roots.
- Include per-entry data sufficient for diffing and event construction:
  - absolute path;
  - file type (regular file, directory, symlink, other where applicable);
  - size where meaningful;
  - mtime/ctime or equivalent nanosecond-resolution stat fields where available;
  - mode;
  - optional `dev`/`ino` object identity;
  - optional content hash for regular files when enabled.
- Provide `AI_OBSERVE_SNAPSHOT_HASH=1` to opt into content hashing.
- Provide `AI_OBSERVE_SNAPSHOT_EXCLUDE` for additional user excludes.
- Provide `AI_OBSERVE_SNAPSHOT_MAX_FILES` as a safety cap. When the cap is exceeded, snapshot reconciliation should degrade with an explicit warning and provenance/status event or metadata rather than silently claiming completeness.
- Apply built-in excludes for high-noise/high-cost paths, including at least:
  - `.git/`
  - `node_modules/`
  - `__pycache__/`
  - `.codev/observe/`
  - `*.pyc`
  - common swap/temp files such as `*.swp`, `*.swo`, and editor backup files
  - lock files where excluding them does not hide primary user work artifacts
- Synthesize snapshot JSONL events using schema-v2 with `source: "snapshot"`, `confidence: "inferred"`, and no invented process attribution.
- Deduplicate/correlate snapshot events with direct strace events conservatively. If a direct strace event already covers the same operation/path during the session, the strace event should normally win; if the snapshot indicates a net change not represented directly, keep the snapshot event.
- Avoid watching the observer’s own trace/JSONL artifacts by default.

#### SHOULD

- Detect renames as paired delete/create or as `rename` events when object identity (`dev`/`ino`) supports a conservative match.
- Make hashing streaming and opt-in to avoid large memory use.
- Support symlink entries without following symlink loops.
- Expose warnings or status metadata for skipped roots, unreadable paths, cap exceedance, or hash errors.
- Keep start snapshot overhead from delaying child launch more than necessary, while not compromising correctness of the baseline. If background capture is used, it must be clear how races before baseline completion are handled.

### Viewer provenance UX

#### MUST

- Continue serving only on loopback and preserving current privacy/sanitization posture.
- Accept and aggregate mixed schema-v1 and schema-v2 streams.
- Preserve existing path, rename, filter, metric, SSE backlog, and live update semantics.
- Render event provenance clearly enough that users can distinguish `strace/direct` from `snapshot/inferred`.
- Add source filtering or equivalent visibility controls for at least `strace` and `snapshot` events.
- Include source/confidence in tooltips, row badges, event details, or another visible local-only UI affordance without exposing sensitive trace fields.
- Make parser partial/rebuilt artifact state discoverable when viewing a session artifact, e.g. a banner if sibling `.partial` or rebuilt files exist.

#### SHOULD

- Show aggregated source composition for a file or subtree when multiple sources contributed events.
- Keep UI additions accessible and keyboard-friendly.
- Add browser/unit tests for source filter behavior and mixed source aggregation.

### Backend abstraction

#### MUST

- Introduce the backend abstraction only after both strace and snapshot behavior are implemented.
- Define a small protocol around lifecycle, event production, stop/drain behavior, and capabilities. Candidate shape: `start(session)`, `events()`, `stop(timeout)`, `capabilities`.
- Keep default behavior low-friction and no-root.
- Refactor without changing user-visible behavior except for documented backend selection/configuration.
- Make future fanotify, inotify, or eBPF sources pluggable without rewriting the viewer/event pipeline.

#### SHOULD

- Provide backend selection/configuration only if it is simple and tested. Acceptable forms include an environment variable or CLI option such as `AI_OBSERVE_BACKENDS=strace,snapshot` / `--backend strace,snapshot`.
- Support strace-only and snapshot-only modes for troubleshooting, but keep the default layered mode once snapshot is available.

### Documentation

#### MUST

- Update `docs/observe.md` to describe watched roots, snapshot reconciliation, provenance, schema-v2, new environment variables, and the revised product promise.
- Update `docs/viewer.md` to describe provenance rendering and source filters.
- Update `codev/resources/arch.md` with the layered observer architecture and backend abstraction invariants.
- State limitations explicitly: configured roots only, remote/hosted agents out of scope, snapshot is net-change/post-hoc, ephemeral create-delete can be missed if live tracing misses it, and snapshot events do not imply process attribution.

## Event model

### Schema-v2 examples

Direct strace event:

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
  "object": { "dev": 2049, "ino": 123456 },
  "command": ["/usr/bin/python", "script.py"],
  "raw_syscall": "write(3</repo/app.py>, \"x\", 1) = 1",
  "result": 1
}
```

Snapshot-inferred event:

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
  "object": { "dev": 2049, "ino": 123456 },
  "snapshot": {
    "before": { "size": 1200 },
    "after": { "size": 1250 }
  }
}
```

The exact `snapshot` metadata shape may be refined in the plan, but raw JSONL must remain structured, parseable, and safe for downstream consumers that ignore unknown fields.

## Configuration

| Variable | Purpose |
| --- | --- |
| `AI_OBSERVE_ROOTS` | Watched roots for snapshot reconciliation; Linux examples use colon-separated absolute or relative paths. Defaults to launch cwd. |
| `AI_OBSERVE_SNAPSHOT_HASH=1` | Opt into content hashing for regular files. |
| `AI_OBSERVE_SNAPSHOT_EXCLUDE` | Additional exclude patterns. Pattern syntax must be documented and tested. |
| `AI_OBSERVE_SNAPSHOT_MAX_FILES` | Maximum number of manifest entries before snapshot reconciliation degrades with warning/status. |
| `AI_OBSERVE_NESTED=1` | Internal/direct-exec escape hatch for nested observed sessions. |
| `AI_OBSERVE_BACKENDS` or `--backend` | Optional backend selection if included after abstraction; default should be layered strace+snapshot. |

Existing `AI_OBSERVE_*` and `CODEV_OBSERVE_*` compatibility variables must keep their documented behavior unless explicitly updated.

## Deduplication and correlation requirements

- Deduplication must be conservative. It is better to show both a direct and inferred event than to suppress a real user-visible change.
- Minimum suppression rule: if a schema-v2 `strace/direct` event exists for the same normalized operation/path during the session, a matching `snapshot/inferred` event may be suppressed.
- A snapshot `modify` should not be suppressed solely because any strace event touched the same path if the operation class differs materially or the net snapshot state suggests a later unobserved change.
- Rename detection should require strong object-identity evidence; otherwise represent as delete/create.
- Deduplication should be testable independently of live strace.

## Non-functional requirements

### Compatibility

- Existing tests for observer CLI, parser, live tracing, environment variables, and viewer behavior must continue to pass.
- Existing schema-v1 JSONL files must remain viewable.
- Existing consumers that ignore unknown JSON fields should continue to function with schema-v2 raw JSONL.

### Security and privacy

- Do not weaken artifact permissions or symlink protections.
- Keep the severe sensitive-data warnings for `.trace` and `.jsonl` artifacts.
- Keep the browser viewer local-only.
- Do not expose raw syscall, command argv, PID, process tree, or raw attribution metadata in the browser page.

### Performance and reliability

- Snapshot traversal must use bounded memory relative to the number of entries and enforce the max-files cap.
- Hashing must be opt-in.
- Built-in excludes must avoid common repository hot spots and observer-generated artifacts.
- Warnings for incomplete snapshot coverage must be explicit and visible enough that users do not infer a false completeness guarantee.
- Parser rebuild behavior must prefer recoverable artifacts over silent partial output.

## Acceptance criteria

### Parser and reliability

- Unit tests show `copy_file_range`, `sendfile`, covered `splice`, `O_CREAT`, and xattr traces produce expected operations where target paths are known.
- Tests show ambiguous syscalls are skipped safely rather than producing misleading paths.
- Tests show live parser timeout/recoverable failure produces a discoverable rebuilt or partial artifact according to the documented behavior.
- Tests show `AI_OBSERVE_NESTED=1` causes direct execution of the resolved real command and avoids recursive tracing.

### Schema and compatibility

- New emitted events use `schema_version: 2`, `source`, and `confidence`.
- Existing v1 fixture files are accepted by the tailer/server/browser and normalized as `strace/direct` for display/aggregation.
- Mixed v1/v2 JSONL streams aggregate correctly.
- Sanitized SSE payloads include provenance but do not include sensitive raw fields.

### Snapshot reconciliation

- Tests show create, modify, delete, and conservative rename/delete-create behavior from manifest diffs.
- Tests show external writes under `AI_OBSERVE_ROOTS` that are not in the traced process tree appear as `snapshot/inferred` events.
- Tests show changes outside configured roots do not appear and are documented as out of scope.
- Tests show built-in and user excludes suppress expected paths, including `.codev/observe/` artifacts.
- Tests show max-files cap and unreadable/missing root warnings are surfaced without false completeness claims.
- Tests show optional hashing can distinguish content changes where metadata-only diff would otherwise be insufficient, when enabled.

### Viewer

- Viewer tests show source/confidence badges or equivalent rendering for strace and snapshot events.
- Source filtering can hide/show strace and snapshot events without breaking existing path filters.
- Mixed v1/v2 streams remain compatible with existing treemap/table metrics.
- Partial/rebuilt artifact indicators render without exposing sensitive fields.

### Backend abstraction

- Tests or type checks cover strace and snapshot implementations behind the new backend protocol.
- Default backend selection remains no-root and low-friction.
- Strace-only and snapshot-only modes, if exposed, are documented and tested.

### Documentation

- Docs state the revised product promise and limits in product-facing language.
- Docs list and explain all new environment variables.
- Docs distinguish direct attribution from inferred snapshot detection.

## Open questions

### Critical

None. The issue provides enough direction to specify the layered architecture.

### Important

- Exact snapshot exclude pattern syntax: gitignore-like, glob-like absolute paths, or both.
- Exact raw JSONL shape for snapshot-specific metadata and warnings/status records.
- Whether snapshot baseline must complete before child launch for maximum correctness, or whether an explicitly documented background baseline trade-off is acceptable.
- Whether backend selection should be CLI, environment variable, or both in the first abstraction phase.

### Nice-to-know

- Whether source composition should be shown per aggregate row, per tooltip, or in a separate event detail panel.
- Whether future snapshot-only mode should be positioned as an experimental non-Linux stepping stone.
- Whether docs should recommend hashing in CI or only for small/high-trust roots.

## Test scenarios

- `ai-observe -- python -c 'open("x", "w").close()'` emits a create event with strace/direct provenance where detectable and does not duplicate confusing modify/create rows.
- A subprocess uses `copy_file_range` or `sendfile` into a watched file; parser emits a direct modify event when destination fd is known.
- A helper process outside the traced tree writes under `AI_OBSERVE_ROOTS` during the session; final JSONL includes a snapshot/inferred modify or create.
- A file is modified via `mmap` under a watched root; final JSONL includes a snapshot/inferred modify even if no direct write syscall exists.
- A file outside `AI_OBSERVE_ROOTS` changes; no event is produced and docs explain why.
- A v1 JSONL fixture and a v2 JSONL fixture loaded together in the viewer show correct aggregation and provenance filters.
- A malformed or partial live parse branch leaves a user-discoverable partial/rebuilt artifact and a viewer-visible indication.

