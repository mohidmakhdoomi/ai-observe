# Implementation Plan: layered-observer-with-snapshot

## Overview

Implement the approved layered-observer specification in ordered, independently testable phases. The work first tightens the existing strace parser and reliability paths, then migrates raw events and viewer ingestion to schema-v2 provenance, then adds synchronous watched-root snapshot reconciliation and conservative deduplication. After strace and snapshot both exist, the viewer gains provenance controls and the backend interface is extracted around the concrete behavior. Documentation and full regression complete the single-PR delivery.

The default user experience remains no-root and low-friction. `strace` remains the live direct-attribution backend. Snapshot reconciliation becomes the default completeness backstop for configured roots. Kernel backends remain deferred.

## Phase machine-readable block

```json
{
  "phases": [
    {
      "id": "phase-1-strace-parser-reliability",
      "name": "Strace parser hygiene and recovery contract",
      "depends_on": []
    },
    {
      "id": "phase-2-schema-v2-provenance-core",
      "name": "Schema-v2 provenance emission and compatibility normalization",
      "depends_on": ["phase-1-strace-parser-reliability"]
    },
    {
      "id": "phase-3-snapshot-manifest-diff",
      "name": "Snapshot manifest capture, diff, excludes, and diagnostics",
      "depends_on": ["phase-2-schema-v2-provenance-core"]
    },
    {
      "id": "phase-4-snapshot-observe-integration",
      "name": "Observe wrapper integration and strace/snapshot deduplication",
      "depends_on": ["phase-3-snapshot-manifest-diff"]
    },
    {
      "id": "phase-5-viewer-provenance-ux",
      "name": "Viewer provenance rendering, source filters, and artifact banners",
      "depends_on": ["phase-4-snapshot-observe-integration"]
    },
    {
      "id": "phase-6-backend-abstraction",
      "name": "Concrete backend protocol and backend selection",
      "depends_on": ["phase-5-viewer-provenance-ux"]
    },
    {
      "id": "phase-7-docs-architecture",
      "name": "Documentation, product promise, and architecture notes",
      "depends_on": ["phase-6-backend-abstraction"]
    },
    {
      "id": "phase-8-review-regression",
      "name": "Final regression, review artifact, and PR preparation",
      "depends_on": ["phase-7-docs-architecture"]
    }
  ]
}
```

## Phases

### Phase 1: Strace parser hygiene and recovery contract (`phase-1-strace-parser-reliability`)

- **Objective**: Improve the existing strace parser and live recovery behavior without introducing snapshot yet.
- **Files**:
  - `src/ai_observe/trace_parser.py` — add parser coverage for `copy_file_range`, `sendfile`, xattr metadata operations, safe `O_CREAT|O_EXCL`/`creat` create events, and truncated/unfinished-line tolerance during rebuild; preserve safe false negatives for ambiguous inputs.
  - `src/ai_observe/observe.py` — implement the concrete artifact contract for recovery paths, extend `LogPaths` with `<session>.jsonl.rebuilt` and `<session>.meta.json`, write/read `<session>.meta.json` warnings, rebuild `<session>.jsonl.rebuilt` on live timeout, protect all new artifact writes with the same containment/symlink/permission checks used for trace/jsonl/partial artifacts, and inject `AI_OBSERVE_NESTED=1` into traced child environments while direct-execing inner shims that see it.
  - `tests/test_trace_parser.py` and `tests/test_live_trace.py` — add parser fixtures/tests and recovery/nested-regression tests.
- **Dependencies**: None.
- **Success Criteria**:
  - Successful `copy_file_range` and `sendfile` syscalls emit `modify` events when destination fd/path is known.
  - Xattr set/remove operations emit `metadata` events when target path/fd is known.
  - `creat` and `open`/`openat`/`openat2` with `O_CREAT|O_EXCL` emit direct create events when target path is known.
  - Non-`O_EXCL` `O_CREAT` does not produce false direct create events from strace alone.
  - Live parser timeout produces discoverable `<session>.jsonl.rebuilt` and `<session>.meta.json` state instead of silently leaving only partial canonical output.
  - Recovery precedence is explicit in `<session>.meta.json`: normal and successful non-timeout rebuilds use `<session>.jsonl` as the authoritative complete stream; live-timeout rebuilds use `<session>.jsonl.rebuilt` as the authoritative complete stream while `<session>.jsonl` remains the non-authoritative partial live stream; parser-failure sessions use `<session>.jsonl.partial` as the partial direct-event artifact and `<session>.jsonl` only for any safe inferred snapshot events or an empty placeholder.
  - Parser failure still writes `<session>.jsonl.partial` according to existing strict/non-strict semantics.
  - Full-trace rebuild tolerates truncated final lines and unfinished syscalls safely.
  - Outer observed sessions set `AI_OBSERVE_NESTED=1`; inner shims seeing it direct-exec the resolved real command rather than launching nested strace.
- **Tests**:
  - Add trace fixture unit tests for `copy_file_range`, `sendfile`, xattrs, `creat`, `O_CREAT|O_EXCL`, and non-`O_EXCL` `O_CREAT`.
  - Add live/recovery tests for timeout rebuild, partial artifact naming, authoritative-file precedence in the meta sidecar, safe-write/permission behavior for `.rebuilt` and `.meta.json`, symlink attack rejection for new artifacts, and truncated trace rebuild tolerance.
  - Add observer tests for `AI_OBSERVE_NESTED=1` injection and inner direct-exec behavior.
  - Run `python3 -m unittest tests.test_trace_parser tests.test_live_trace tests.test_observe_cli tests.test_observe_env`.

### Phase 2: Schema-v2 provenance emission and compatibility normalization (`phase-2-schema-v2-provenance-core`)

- **Objective**: Introduce event provenance in raw JSONL and normalize v1/v2 streams in non-UI consumers before snapshot events exist.
- **Files**:
  - `src/ai_observe/trace_parser.py` — bump emitted events to `schema_version: 2` and add `source: "strace"`, `confidence: "direct"`, plus optional object identity when safely available from fd/path annotations or stat context.
  - `src/ai_observe/viewer/tailer.py` — accept schema-v1 and schema-v2 events, explicitly stop rejecting `schema_version: 2`, normalize missing provenance as `strace/direct`, and sanitize `schema_version`, `source`, and `confidence` to the browser while continuing to exclude raw syscall/command/pid/process fields.
  - `tests/test_viewer_tailer.py` and `tests/test_trace_parser.py` — add v2 emission and v1/v2 tailer normalization coverage.
- **Dependencies**: Phase 1.
- **Success Criteria**:
  - New strace-derived raw JSONL events are schema-v2 with `source: "strace"` and `confidence: "direct"`.
  - Existing schema-v1 fixture files remain accepted by the viewer tailer and are normalized as `strace/direct` in sanitized output.
  - Schema-v2 events are accepted by the current tailer/server path and no longer skipped for version mismatch.
  - Browser-sanitized events include provenance fields but still exclude sensitive fields. Because `sanitize_event` is a whitelist, Phase 2 explicitly updates that whitelist and runs aggregation/oracle tests to ensure additional safe provenance fields are ignored or propagated intentionally.
  - Unknown future schema versions are warned/skipped only when they cannot be safely normalized.
- **Tests**:
  - Assert parser-emitted events contain `schema_version: 2`, `source`, and `confidence`.
  - Add tailer tests for missing `schema_version`, explicit `schema_version: 1`, explicit `schema_version: 2`, and unsupported future versions.
  - Run `python3 -m unittest tests.test_trace_parser tests.test_viewer_tailer tests.test_viewer_server tests.test_viewer_aggregator` to catch early sanitizer/oracle effects from new safe provenance fields.

### Phase 3: Snapshot manifest capture, diff, excludes, and diagnostics (`phase-3-snapshot-manifest-diff`)

- **Objective**: Build the snapshot engine as a pure, deterministic module before integrating it into command execution.
- **Files**:
  - `src/ai_observe/snapshot.py` — create manifest entry models/helpers, root parsing and overlap de-duplication, built-in/user exclude matching, synchronous tree walk without following symlink subtrees, optional streaming hashes, max-files cap handling, manifest diff, conservative rename detection, and schema-v2 snapshot event synthesis.
  - `tests/test_snapshot.py` — create unit tests for root parsing, excludes, manifest fields, diff operations, rename/delete-create behavior, hashing, max-files diagnostics, and warnings.
  - `src/ai_observe/observe.py` — add only shared meta-sidecar helper functions if needed by `snapshot.py` tests; avoid wiring snapshot into the wrapper until Phase 4.
- **Dependencies**: Phase 2.
- **Success Criteria**:
  - `AI_OBSERVE_ROOTS` parsing defaults to launch cwd when unset/empty and uses Linux colon-separated roots.
  - Missing roots, overlapping roots, unreadable paths, hash errors, and cap exceedance produce structured diagnostics suitable for `<session>.meta.json`.
  - Overlapping roots keep the ancestor and skip descendants with a warning.
  - Built-in excludes match `.git`, `node_modules`, `__pycache__`, `.codev/observe/**`, `**/*.pyc`, `**/*.pyo`, swap/backup patterns, `.DS_Store`, and `.nfs*`.
  - Built-in excludes do not suppress project lockfiles such as `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `Cargo.lock`, `Pipfile.lock`, or generic `*.lock` by default.
  - User excludes support documented newline- or colon-separated glob/segment syntax.
  - Manifest entries include path, type, size, mtime/ctime nanosecond fields where available, mode, optional `dev`/`ino`, optional hash, and symlink target where applicable.
  - Diff produces schema-v2 `snapshot/inferred` create, modify, delete, metadata, and conservative rename or delete/create events as specified.
  - `ctime` alone does not emit a snapshot event.
- **Tests**:
  - Unit-test manifest diff with synthetic manifests for create, modify, delete, metadata-only changes, rename with matching `dev`/`ino`, and ambiguous delete/create.
  - Unit-test real temporary-directory walking for symlinks, excludes, hash opt-in, and max-files cap.
  - Run `python3 -m unittest tests.test_snapshot`.

### Phase 4: Observe wrapper integration and strace/snapshot deduplication (`phase-4-snapshot-observe-integration`)

- **Objective**: Wire synchronous snapshot reconciliation into the observer lifecycle and merge inferred events with direct strace events conservatively.
- **Files**:
  - `src/ai_observe/observe.py` — parse snapshot env vars, capture start manifest synchronously before child launch, capture end manifest after child exit, call snapshot diff, append/safely merge snapshot events into the authoritative event artifact identified by the recovery contract, write `<session>.meta.json` with the final artifact roles, and preserve exit-code/strict-parse behavior.
  - `src/ai_observe/snapshot.py` — add wrapper-facing helpers for deduplication/correlation against direct events and final event ordering/timestamping if not completed in Phase 3.
  - `tests/test_observe_cli.py` and `tests/test_snapshot.py` — add integration tests for default cwd roots, explicit roots, external/out-of-tree helper writes simulated through manifest diffs or deterministic subprocess orchestration, snapshot-only inferred events, and deduplication.
- **Dependencies**: Phase 3.
- **Success Criteria**:
  - With default configuration, watched roots default to launch cwd and observer artifacts are excluded.
  - `AI_OBSERVE_ROOTS`, `AI_OBSERVE_SNAPSHOT_HASH`, `AI_OBSERVE_SNAPSHOT_EXCLUDE`, and `AI_OBSERVE_SNAPSHOT_MAX_FILES` are honored.
  - Start snapshot completes before the child command starts; end snapshot runs after child exit.
  - External or untraced writes under watched roots appear as `source: "snapshot"`, `confidence: "inferred"` events.
  - Changes outside configured roots do not appear.
  - Deduplication uses deterministic operation/path rules from the spec: direct strace events win only for matching operations/paths, and snapshot deletes are not hidden by direct modifies/creates.
  - Snapshot diagnostics appear in `<session>.meta.json` and stderr warnings where appropriate without changing the observed command exit code unless existing strict parse semantics require it. Snapshot events are merged into `<session>.jsonl` for normal/recovered sessions, into `<session>.jsonl.rebuilt` for live-timeout rebuilt sessions, and into `<session>.jsonl` as inferred-only data for parser-failure sessions while partial direct events remain in `<session>.jsonl.partial`.
  - No false completeness claim is made when roots are skipped or max-files cap is exceeded.
- **Tests**:
  - Add fake-strace integration tests that run a command mutating a temp watched root and verify merged JSONL includes direct and inferred provenance as expected.
  - Prefer deterministic unit tests for external-writer scenarios through manifest diff/event synthesis; add live subprocess orchestration only where stable.
  - Add tests for explicit roots, default cwd, excludes, cap warnings, missing roots, no remaining roots, artifact exclusion, and snapshot merge target selection for normal, timeout/rebuilt, and parser-failure modes.
  - Run `python3 -m unittest tests.test_observe_cli tests.test_snapshot tests.test_live_trace tests.test_observe_env`.

### Phase 5: Viewer provenance rendering, source filters, and artifact banners (`phase-5-viewer-provenance-ux`)

- **Objective**: Make mixed direct/inferred streams understandable in the local browser viewer while preserving existing aggregation and privacy behavior.
- **Files**:
  - `src/ai_observe/viewer/static/aggregator.js` and `tests/_aggregator_oracle.py` — propagate source/confidence through aggregation, implement source filtering semantics alongside existing path filters, and track source composition per node where useful.
  - `src/ai_observe/viewer/static/index.html`, `src/ai_observe/viewer/static/index.js`, `src/ai_observe/viewer/static/table.js`, `src/ai_observe/viewer/static/treemap.js`, and `src/ai_observe/viewer/static/style.css` — render provenance badges/tooltip text, add source visibility controls for `strace` and `snapshot`, and show non-sensitive artifact/meta banners.
  - `src/ai_observe/viewer/server.py`, `src/ai_observe/viewer/tailer.py`, and tests under `tests/test_viewer_*.py` — expose sanitized meta/artifact state to the browser and add mixed v1/v2/source-filter coverage.
- **Dependencies**: Phase 4.
- **Success Criteria**:
  - Viewer accepts mixed schema-v1 and schema-v2 streams and aggregates them correctly.
  - `strace/direct` and `snapshot/inferred` are visibly distinguishable in rows, tooltips, badges, or equivalent UI.
  - Source filters can hide/show `strace` and `snapshot` events without breaking existing path filters, factory filters, metric controls, SSE backlog delivery, or live batching.
  - Sanitized SSE/browser payloads include only safe provenance and artifact status fields, not raw syscall, command argv, PID, process tree, raw attribution, or full unsanitized manifests.
  - Sibling `<session>.jsonl.partial`, `<session>.jsonl.rebuilt`, and `<session>.meta.json` are detected and displayed through a non-sensitive banner/control. Viewer precedence follows `<session>.meta.json`: if it marks `.rebuilt` as authoritative, the viewer clearly offers/switches to the rebuilt stream; if parser failure produced `.partial`, the viewer labels it as partial direct evidence and does not silently merge it with inferred canonical data.
  - Existing viewer treemap/table/filter tests remain passing.
- **Tests**:
  - Add fixtures for mixed v1/v2 strace/snapshot JSONL streams and golden aggregation output.
  - Add JavaScript/unit tests for source filter state, badges/tooltips, and table/treemap compatibility.
  - Add server/tailer tests for meta sidecar detection, authoritative artifact precedence, rebuilt/partial banner behavior, and sanitized artifact banner data.
  - Run `python3 -m unittest tests.test_viewer_tailer tests.test_viewer_server tests.test_viewer_aggregator tests.test_viewer_index_js tests.test_viewer_table_js tests.test_viewer_treemap tests.test_viewer_smoke_e2e`.

### Phase 6: Concrete backend protocol and backend selection (`phase-6-backend-abstraction`)

- **Objective**: Refactor around a backend interface only after strace and snapshot behavior make the seam concrete.
- **Files**:
  - `src/ai_observe/backends/__init__.py` — create the backend protocol/session context/capability definitions and helper types.
  - `src/ai_observe/backends/strace.py` and `src/ai_observe/backends/snapshot.py` — move strace execution/parsing lifecycle and snapshot lifecycle behind concrete backend implementations while preserving behavior.
  - `src/ai_observe/observe.py` and `tests/test_backends.py` — implement backend orchestration/default selection through `AI_OBSERVE_BACKENDS`, strace-only and snapshot-only troubleshooting modes, and protocol tests.
- **Dependencies**: Phase 5.
- **Success Criteria**:
  - Default backend selection is `strace,snapshot` and remains no-root.
  - `AI_OBSERVE_BACKENDS=strace`, `AI_OBSERVE_BACKENDS=snapshot`, and `AI_OBSERVE_BACKENDS=strace,snapshot` are accepted and tested.
  - Existing command execution behavior, artifact paths, parser recovery, snapshot reconciliation, and viewer outputs do not change except for documented backend-selection behavior.
  - Invalid backend names produce actionable errors before launching the child command.
  - The interface has lifecycle/capability seams sufficient for future fanotify/inotify/eBPF without adding those backends now.
  - The refactor keeps privacy boundaries and schema-v2 event pipeline intact.
- **Tests**:
  - Add unit tests for backend selection parsing, invalid backend names, protocol conformance, and default behavior.
  - Add integration tests for strace-only and snapshot-only modes using fake strace and temp roots.
  - Run `python3 -m unittest tests.test_backends tests.test_observe_cli tests.test_snapshot tests.test_live_trace`.

### Phase 7: Documentation, product promise, and architecture notes (`phase-7-docs-architecture`)

- **Objective**: Update public and internal docs to accurately describe the layered guarantee, provenance model, configuration, and limitations.
- **Files**:
  - `docs/observe.md` — document schema-v2, provenance, watched roots, snapshot env vars, backend env var, meta/rebuilt/partial artifacts, revised product promise, sensitive-data warnings, and limitations.
  - `docs/viewer.md` — document provenance badges/filters, mixed v1/v2 compatibility, source filtering, and partial/rebuilt/meta banners.
  - `codev/resources/arch.md` — add layered observer and backend abstraction invariants.
- **Dependencies**: Phase 6.
- **Success Criteria**:
  - Docs state the defensible product promise: every net create/modify/delete visible under configured roots by combining live strace and session-boundary snapshot reconciliation, with provenance.
  - Docs explicitly state limitations: configured roots only, remote/hosted agents out of scope, snapshot is post-hoc/net-change, ephemeral create-delete can be missed when live tracing misses it, and snapshot events do not imply process attribution.
  - Docs list all new variables: `AI_OBSERVE_ROOTS`, `AI_OBSERVE_SNAPSHOT_HASH`, `AI_OBSERVE_SNAPSHOT_EXCLUDE`, `AI_OBSERVE_SNAPSHOT_MAX_FILES`, `AI_OBSERVE_NESTED`, and `AI_OBSERVE_BACKENDS` if exposed.
  - Docs describe schema-v2 fields and v1 compatibility rules.
  - Sensitive-data warnings cover `.trace`, `.jsonl`, `.jsonl.partial`, `.jsonl.rebuilt`, `<session>.meta.json`, and manifest-derived metadata.
  - Architecture notes describe strace default, snapshot completeness backstop, provenance, and deferred kernel backends.
- **Tests**:
  - Run documentation-adjacent smoke checks if available; otherwise verify examples manually against implemented CLI behavior with fake tools.
  - Run `python3 -m unittest discover -s tests` after docs changes to catch accidental fixture/schema drift.

### Phase 8: Final regression, review artifact, and PR preparation (`phase-8-review-regression`)

- **Objective**: Perform full validation, record lessons learned, and prepare the single PR after all implementation phases are committed.
- **Files**:
  - `codev/reviews/15-layered-observer-with-snapshot.md` — create review notes with implementation summary, phase commits, test results, deferred work, and flaky-test documentation if any tests are skipped.
  - No production files unless final regression reveals small fixes; any fixes should be committed explicitly with the relevant files.
- **Dependencies**: Phase 7.
- **Success Criteria**:
  - Full unit test suite passes or any pre-existing flaky tests are skipped with clear annotations and documented under `## Flaky Tests` in the review.
  - Review artifact records the final product promise, implemented phases, important design decisions, test commands/results, and deferred kernel backends.
  - Git history contains phase-sized commits and no untracked debug files.
  - A single PR is opened only after final implementation/review unless the architect explicitly asks for an earlier PR.
  - Architect is notified via `afx send architect "PR #N ready for review (project 15)"` once the PR exists.
- **Tests**:
  - Run `python3 -m unittest discover -s tests`.
  - Run targeted smoke scenarios with fake tools for default `strace,snapshot`, `AI_OBSERVE_BACKENDS=strace`, and `AI_OBSERVE_BACKENDS=snapshot`.
  - Run a viewer smoke test against a mixed v1/v2 JSONL fixture.
  - Verify `git status --short` contains only expected tracked changes before PR creation.

## Cross-phase implementation notes

- Do not edit `codev/projects/15-layered-observer-with-snapshot/status.yaml` directly; porch owns project state.
- Do not use `git add .` or `git add -A`; stage files explicitly.
- Do not open per-phase PRs. Phase work should be committed as git commits within the single final PR unless the architect explicitly requests otherwise.
- Keep `strace` as the default live attribution backend throughout.
- Kernel backends (`fanotify`, `inotify`, `eBPF`, auditd) are deferred; do not prototype them in this project.
- Preserve the existing local-only viewer and browser sanitization posture.
- Avoid false-positive direct events; prefer safe false negatives that snapshot can backstop.
- Treat snapshot events as inferred net session changes, not process attribution.
- Keep snapshot baseline synchronous before child launch in this release.
- Store session diagnostics in `<session>.meta.json`, not fake filesystem mutation events. The sidecar must include artifact roles/precedence so normal, timeout-rebuilt, and parser-failure sessions are interpreted consistently by both CLI diagnostics and the viewer.
- Keep hashing opt-in and streaming.
- Ensure built-in excludes suppress observer artifacts and high-cost caches but not project lockfiles.
- Use pure helper functions for parser classification, snapshot diffing, exclude matching, deduplication, and backend selection so most behavior is unit-testable without real `strace` or third-party AI CLIs.
- Existing internal test knobs may remain undocumented, but parser-failure and live-timeout tests need deterministic hooks. All new artifacts (`.jsonl.rebuilt`, `.meta.json`, and any future manifest-derived files) must use explicit safe-write helpers with containment checks, no-follow/symlink protection, restrictive permissions, and tests matching the existing trace/jsonl safety posture.

## Risk Assessment

- **False create events from `O_CREAT`**: Mitigate by emitting direct creates only for `creat`, `O_CREAT|O_EXCL`, or a reliable pre-open existence signal; otherwise let snapshot infer absent→present.
- **Snapshot races before baseline completion**: Mitigate by making the start manifest synchronous before child launch.
- **Large-root performance or memory blowups**: Mitigate with concrete built-in excludes, opt-in hashing, streaming walks/hashes, and `AI_OBSERVE_SNAPSHOT_MAX_FILES` diagnostics.
- **Over-suppression during deduplication**: Mitigate with deterministic conservative rules that keep inferred evidence unless a direct same-operation/path event covers it.
- **Viewer privacy regression**: Mitigate by only sanitizing provenance and non-sensitive artifact status to the browser; never send raw syscall, command argv, PID/process, raw attribution, or full manifests.
- **Schema-v2 breaks existing viewer fixtures**: Mitigate with explicit v1 normalization, mixed v1/v2 tests, and updates to `tests/_aggregator_oracle.py` in lock-step with `aggregator.js`.
- **Recovery artifact confusion**: Mitigate with a documented naming contract, explicit authoritative-file precedence for normal/timeout/parser-failure modes, and `<session>.meta.json` sidecar consumed by the viewer.
- **Security regression in new artifacts**: Mitigate by extending existing safe path/write helpers to `.jsonl.rebuilt` and `.meta.json`, enforcing containment and symlink protections, keeping restrictive file modes, and adding regression tests for unsafe paths/symlinks.
- **Backend refactor churn**: Mitigate by delaying abstraction until after snapshot integration and by adding behavior-preserving backend tests before moving code.
- **Flaky live integration tests**: Prefer parser fixtures, manifest-diff unit tests, fake strace, and deterministic temp-dir tests; if a pre-existing flaky test blocks progress, skip it with a clear annotation and document it in the review as required.
- **Scope creep into kernel backends or cross-platform ports**: Keep those as explicit non-goals and future backend opportunities only.
