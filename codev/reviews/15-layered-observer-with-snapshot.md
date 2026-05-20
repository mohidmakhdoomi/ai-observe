# Review: layered-observer-with-snapshot

## Summary

This project turned `ai-observe` from a single-source `strace` observer into a layered observer that combines:

- `strace` as the default live, process-tree-scoped attribution backend;
- watched-root snapshot reconciliation as the completeness backstop;
- schema-v2 provenance so direct and inferred evidence are explicit;
- viewer support for mixed source streams, source filtering, and artifact-state banners; and
- a concrete backend abstraction for `strace` / `snapshot` composition and troubleshooting modes.

The implementation keeps the no-root default and preserves the viewer privacy boundary while making the product promise more defensible.

## Final Product Promise

`ai-observe` reports every **net** file create, modify, or delete visible under configured watched roots during a session by combining a live event stream from the wrapped Linux process tree with session-boundary snapshot reconciliation. Events carry provenance so users can distinguish directly observed changes from inferred changes. Activity outside watched roots and changes by remote or hosted agents are not observed.

## Spec Compliance

- [x] Keep `strace` as the default live backend with direct provenance (`source: "strace"`, `confidence: "direct"`).
- [x] Improve parser/recovery reliability, including `copy_file_range`, `sendfile`, xattr metadata operations, timeout rebuilds, and nested-shim recursion handling.
- [x] Emit schema-v2 events and preserve schema-v1 ingestion compatibility in viewer/server/tailer flows.
- [x] Add watched-root snapshot reconciliation with explicit roots, excludes, optional hashing, max-files safety limits, diagnostics, and conservative deduplication.
- [x] Surface provenance in the viewer through source-aware aggregation, visible provenance UI, source toggles, and artifact/meta banners.
- [x] Introduce a concrete backend abstraction with `AI_OBSERVE_BACKENDS=strace,snapshot` (default), `strace`, and `snapshot`.
- [x] Update public/internal documentation (`docs/observe.md`, `docs/viewer.md`, `codev/resources/arch.md`) to match the layered guarantee and limitations.
- [x] Add/maintain regression coverage proving mixed direct/inferred event handling, recovery precedence, and backend-selection behavior.

## Implemented Work by Phase

### Phase 1 — strace parser hygiene and recovery
- Added parser coverage for `copy_file_range`, `sendfile`, xattr metadata operations, and safe direct create detection for `creat` / `O_CREAT|O_EXCL`.
- Hardened timeout/failure recovery around `.jsonl`, `.jsonl.partial`, `.jsonl.rebuilt`, and `<session>.meta.json`.
- Added the internal `AI_OBSERVE_NESTED=1` recursion guard so nested shims direct-exec instead of launching nested `strace`.

### Phase 2 — schema-v2 provenance
- Upgraded raw events to `schema_version: 2` with `source` and `confidence`.
- Kept viewer/tailer compatibility with schema-v1 by normalizing legacy events to `strace/direct`.

### Phase 3 — snapshot manifest/diff
- Added watched-root parsing, overlap handling, built-in/user excludes, optional hashing, max-files limits, manifest diagnostics, and conservative diffing/rename detection.
- Synthesized `snapshot/inferred` events without inventing process attribution.

### Phase 4 — observe wrapper integration
- Captured the baseline snapshot synchronously before child launch and a second snapshot after child exit.
- Merged direct and inferred events conservatively and recorded authoritative artifact roles in `<session>.meta.json`.

### Phase 5 — viewer provenance UX
- Added source/confidence propagation through aggregation plus viewer source filters, provenance badges/details, and partial/rebuilt/meta artifact banners.
- Preserved local-only serving and browser sanitization boundaries.

### Phase 6 — backend abstraction
- Added concrete backend protocol/types and split `strace` / `snapshot` behavior behind backend modules.
- Exposed `AI_OBSERVE_BACKENDS` with supported values `strace,snapshot` (default), `strace`, and `snapshot`.

### Phase 7 — docs and architecture
- Rewrote `docs/observe.md`, `docs/viewer.md`, and `codev/resources/arch.md` to document the layered guarantee, provenance, backend selection, snapshot configuration, artifact precedence, and explicit limitations.

### Phase 8 — final regression and review
- Fixed latent flakiness in three older observe-cli shim/compat tests by pinning them to `AI_OBSERVE_BACKENDS=strace`.
- Re-ran the full suite and targeted layered-backend/viewer smoke coverage.
- Recorded the implementation summary, consultation history, architecture updates, lessons-learned updates, and deferred work in this review.

## Deviations from Plan

- **Phase 5 initial review pass**: all three reviewers correctly noted that provenance UX, source filters, and artifact banners were still missing. The missing work was implemented in the same phase before advancement, so the delivered functionality matches the approved plan.
- **Phase 8 review artifact wording**: the first draft read too much like a PR-finalized declaration while the strict-mode porch review cycle was still open. The artifact was corrected to reflect the actual state during the consultation/rebuttal loop.
- **Phase 8 regression hardening**: final review found latent flakiness in three pre-snapshot observe-cli tests. Those tests were updated to pin `AI_OBSERVE_BACKENDS=strace`, which is a Phase 8 regression/stability fix rather than a product-scope deviation.

## Important Design Decisions

- Keep `strace` as the default live backend for no-root, process-tree-scoped direct evidence.
- Use snapshot reconciliation as the completeness backstop for net watched-root changes that live tracing can miss.
- Make provenance explicit before mixing sources so users can distinguish `strace/direct` from `snapshot/inferred` evidence.
- Stay conservative in deduplication: suppress inferred evidence only when a matching direct operation/path already exists.
- Preserve privacy in the viewer: provenance reaches the browser, but raw syscall text, argv, PID/process details, session ids, and full manifests do not.
- Defer kernel backends; the new backend seam is future-facing but the release promise remains grounded in `strace + snapshot`.

## Architecture Updates

Updated `codev/resources/arch.md` with the layered observer architecture:

- documented the `strace` default plus snapshot completeness backstop;
- captured schema-v2 provenance and artifact-precedence invariants;
- documented the backend abstraction boundary and `AI_OBSERVE_BACKENDS` selection rules; and
- explicitly recorded deferred kernel backends as future extensions rather than release scope.

## Lessons Learned Updates

Updated `codev/resources/lessons-learned.md` with two new reusable lessons:

1. **Pin legacy tests when a new default backend changes observation scope** — old single-backend tests can become latently flaky once a second default source is added.
2. **Make artifact authority explicit when recovery can yield multiple valid outputs** — use a machine-readable sidecar instead of filename inference when canonical/partial/rebuilt artifacts may coexist.

## Lessons Learned

### What Went Well

- The phased plan split the work cleanly: parser/recovery, schema/provenance, snapshot engine, wrapper integration, viewer UX, backend abstraction, docs, then final regression.
- The combination of parser fixtures, temp-dir snapshot tests, fake `strace`, and viewer fixture/oracle tests kept the implementation highly testable without privileged dependencies.
- Making provenance and artifact state explicit prevented later viewer/backend work from devolving into source-specific heuristics.

### Challenges Encountered

- **Recovery artifact semantics**: timeout rebuild vs parser failure vs normal JSONL output needed an explicit artifact-authority contract. This was resolved by formalizing `.jsonl`, `.jsonl.partial`, `.jsonl.rebuilt`, and `<session>.meta.json` roles.
- **Viewer Phase 5 scope gap**: the first implementation pass still lacked provenance UX and artifact banners. Review feedback caught it quickly, and the missing work was completed with mixed-source fixtures and server/UI coverage.
- **Regression drift after layered mode became default**: a few older shim tests still assumed strace-only behavior. Phase 8 fixed this by pinning those tests to `AI_OBSERVE_BACKENDS=strace`.

### What Would Be Done Differently

- Add explicit backend/source isolation to older wrapper tests as soon as layered mode becomes the default, rather than waiting for final regression to expose latent flakiness.
- Write the artifact-authority sidecar contract even earlier in the design so recovery-mode behavior stays uniform across parser, wrapper, and viewer changes.
- Land the viewer provenance UX earlier in the implementation cycle with a single mixed-source fixture from the start.

### Methodology Improvements

- SPIR review prompts for multi-source observer work should explicitly ask reviewers to check legacy tests for hidden single-backend assumptions once defaults broaden.
- Plans that introduce new artifacts should always specify safe-write requirements and authoritative-file precedence at the plan stage, not leave them implicit for implementation review.

## Technical Debt

- Optional kernel backends such as fanotify, inotify, and eBPF remain deferred.
- Remote/hosted-agent filesystem observation remains out of scope.
- Byte-level attribution for `mmap` writes remains out of scope.
- Full macOS/Windows live backend support remains out of scope.
- Ephemeral create-then-delete files can still be missed if neither live tracing nor final snapshots observe them.

## Consultation Feedback

### Specify Phase (Round 1)

#### Gemini
- **Concern**: Clarify symlink traversal, synchronous start snapshots, and truncated-trace recovery expectations.
  - **Addressed**: The spec now requires synchronous baseline capture, canonical root handling without following arbitrary symlink subtrees, and tolerant rebuild behavior for truncated final lines / unfinished syscalls.

#### Codex
- **Concern**: Warning/artifact semantics, metadata-only snapshot semantics, built-in excludes, deduplication rules, and rebuilt/partial artifact naming were too ambiguous for deterministic implementation.
  - **Addressed**: The spec now defines the concrete `.jsonl` / `.jsonl.partial` / `.jsonl.rebuilt` / `.meta.json` contract, metadata-vs-content event rules, required built-in excludes, deterministic deduplication, and artifact discovery/precedence behavior.

#### Claude
- **Concern**: Tighten `O_CREAT` semantics, define `AI_OBSERVE_NESTED=1`, ensure schema-v2 tailer acceptance, and resolve overlapping-root / backend-selection open questions.
  - **Addressed**: Those semantics were written directly into the approved specification.

### Plan Phase (Round 1)

#### Gemini
- No concerns raised — APPROVE.

#### Codex
- **Concern**: The plan under-specified safe-write/security handling for new artifacts and did not define authoritative artifact precedence across timeout/rebuild flows.
  - **Addressed**: The plan now requires safe-write/symlink/permission protections for all new artifacts and explicitly defines authoritative event-file precedence plus snapshot-merge targets across recovery modes.

#### Claude
- **Concern**: Call out early sanitizer/oracle interactions and preserve the same safe-write posture for new artifact types.
  - **Addressed**: Phase 2 test coverage and cross-phase safe-write requirements were strengthened accordingly.

### Implement Phase 1 — Strace Parser Reliability (Round 1)

#### Gemini
- No concerns raised — APPROVE.

#### Codex
- **Concern**: Successful timeout rebuilds still tripped strict-parse failure state; meta-sidecar labeling for partial-live JSONL was incorrect; rebuild-failure branches lacked coverage.
  - **Addressed**: Timeout rebuilds now clear `parse_failed` after successful recovery, metadata distinguishes `partial_live` correctly, and rebuild-failure coverage was added.

#### Claude
- No concerns raised — APPROVE.

### Implement Phase 2 — Schema-v2 Provenance (Round 1)

#### Gemini
- No concerns raised — APPROVE.

#### Codex
- **Concern**: The tailer still rejected all future schema versions unconditionally instead of normalizing compatible future events safely.
  - **Addressed**: Future integer schema versions are now normalized when they still provide the current viewer-safe fields.

#### Claude
- **Concern**: Reported that Phase 2 appeared unimplemented.
  - **Rebutted**: This inspection was stale; the current worktree already emitted schema-v2 events and accepted v1/v2 normalization paths. Only the Codex forward-compatibility fix required code changes.

### Implement Phase 3 — Snapshot Manifest/Diff (Round 1)

#### Gemini
- No concerns raised — APPROVE.

#### Codex
- **Concern**: Hash-error handling could create false-positive modifies, rename detection could cross watched-root boundaries, and diagnostics coverage was incomplete.
  - **Addressed**: Hash-only diffs now require both hashes, rename detection is root-scoped, and hash-error/unreadable-path tests were added.

#### Claude
- No concerns raised — APPROVE.

### Implement Phase 4 — Snapshot Observe Integration (Round 1)

#### Gemini
- No concerns raised — APPROVE.

#### Codex
- **Concern**: The wrapper wrongly continued when all configured roots were invalid, and the test suite encoded that wrong contract.
  - **Addressed**: The wrapper now fails before launch when no usable roots remain, writes `snapshot_root_error` diagnostics to meta, and the integration tests were corrected.

#### Claude
- No concerns raised — APPROVE.

### Implement Phase 5 — Viewer Provenance UX (Round 1)

#### Gemini
- **Concern**: Frontend UI work, artifact discovery, and source-aware aggregation were still missing.
  - **Addressed**: Implemented provenance UI, source toggles, artifact-state banners, session/artifact endpoints, and mixed-source fixtures/tests.

#### Codex
- **Concern**: Viewer provenance UX, source filtering, and artifact/meta banner support were largely unimplemented.
  - **Addressed**: Completed the missing viewer/server/runtime work and added the corresponding fixture/oracle/UI/server coverage.

#### Claude
- **Concern**: Provenance badges, source filters, artifact banners, and mixed-source handling were entirely absent in the pre-review worktree.
  - **Addressed**: Implemented the full missing Phase 5 feature set before advancement.

### Implement Phase 6 — Backend Abstraction (Round 1)

#### Gemini
- No concerns raised — APPROVE.

#### Codex
- **Concern**: Missing explicit `AI_OBSERVE_BACKENDS=strace,snapshot` coverage and misleading strace-specific launch errors in snapshot-only mode.
  - **Addressed**: Added explicit layered-mode coverage and made launch errors backend-aware when `strace` is not selected.

#### Claude
- No concerns raised — APPROVE.

### Implement Phase 7 — Docs and Architecture (Round 1)

#### Gemini
- No concerns raised — APPROVE.

#### Codex
- **Concern**: Snapshot exclude-pattern semantics needed to be more explicit/visible, and the sensitive-data warning should mention snapshot-derived metadata directly.
  - **Addressed**: `docs/observe.md` now explicitly references the exclude matching rules and calls out `snapshot/manifest-derived metadata` in the risk warning.

#### Claude
- No concerns raised — APPROVE.

### Implement Phase 8 — Review and Regression (Round 1)

#### Gemini
- No concerns raised — APPROVE.

#### Codex
- **Concern**: The first review artifact overstated finalization while the strict-mode porch review cycle was still active.
  - **Addressed**: The review artifact wording was corrected, and the later implementation/review phases were committed so the branch now reflects the actual project state.

#### Claude
- **Concern**: Flagged latent flakiness in three older observe-cli shim/compat tests once layered mode became the default, and noted the then-uncommitted later-phase state.
  - **Addressed**: The three tests now pin `AI_OBSERVE_BACKENDS=strace`, the full suite passes afterward, and later-phase implementation artifacts are now committed.

## Validation

### Full regression

```bash
python3 -m unittest discover -s tests
```

Result: **204 tests passed**.

### Targeted backend / viewer smoke tests

```bash
python3 -m unittest \
  tests.test_backends \
  tests.test_observe_cli.ObserveCliIntegrationTests.test_explicit_strace_snapshot_backend_setting_matches_default_layered_mode \
  tests.test_observe_cli.ObserveCliIntegrationTests.test_strace_only_mode_disables_snapshot_reconciliation \
  tests.test_observe_cli.ObserveCliIntegrationTests.test_snapshot_only_mode_runs_without_strace_and_emits_inferred_events
```

Result: **7 tests passed**.

```bash
python3 -m unittest \
  tests.test_viewer_aggregator.CustomFilterTests.test_mixed_v1_v2_sources_roll_up_provenance \
  tests.test_viewer_aggregator.CustomFilterTests.test_source_visibility_replays_only_selected_sources \
  tests.test_viewer_smoke_e2e
```

Result: **5 tests passed**.

### Review-phase e2e check

```bash
npm run test:e2e 2>&1 || echo 'e2e tests skipped (not configured)'
```

Result: **e2e tests skipped (not configured)**.

## Flaky Tests

No tests were skipped as flaky.

Phase 8 did uncover latent flakiness in three older shim/compat tests after layered mode became the default, but those tests were fixed by pinning `AI_OBSERVE_BACKENDS=strace` and now pass reliably in the suite.

## Follow-up Items

- Consider whether snapshot-only mode should eventually gain its own short troubleshooting section in user-facing docs with concrete examples.
- If kernel backends are ever added, preserve the same provenance/privacy contract rather than widening browser-visible detail.
- If future recovery modes produce more artifact variants, keep extending `<session>.meta.json` rather than encoding state in filenames or UI heuristics.
