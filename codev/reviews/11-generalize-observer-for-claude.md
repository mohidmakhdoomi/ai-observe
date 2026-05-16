# Review: generalize-observer-for-claude

## Summary

Spec 11 generalized `ai-observe` from a Codex-specific wrapper into a generic Linux command filesystem observer. The implementation keeps Codex compatibility while adding named shims for Claude Code, Gemini CLI, and OpenCode plus a generic `bin/ai-observe -- command [args...]` entry point.

## Spec Compliance

- [x] Generic observer module and command path exist in `src/ai_observe/observe.py` and `bin/ai-observe`.
- [x] `bin/codex` continues to work through the compatibility facade.
- [x] `bin/claude`, `bin/gemini`, and `bin/opencode` are executable named shims.
- [x] Named shim resolution implements preferred `AI_OBSERVE_REAL_<PROGRAM>`, Codex legacy real-binary alias, PATH recursion avoidance, adjacent `.real` / `.bin`, and actionable `127` errors.
- [x] Generic command mode requires `--` and a command token, supports PATH and explicit-path resolution, rejects observer-wrapper recursion, and supports `AI_OBSERVE_REAL_COMMAND` argv[0] replacement.
- [x] Preferred `AI_OBSERVE_*` variables are supported with `CODEV_OBSERVE_*` aliases where specified; preferred values win when both are set.
- [x] JSONL schema compatibility is preserved: `schema_version` remains `1`, and `command` records the resolved real executable argv passed under `strace`.
- [x] Existing Codex, live-trace, viewer, resolver, env, and wrapper tests pass.
- [x] Documentation now presents the product as a generic command observer and includes security warnings, process-tree scope, ptrace/sandbox requirements, named tool examples, arbitrary-command examples, and troubleshooting.

## Deviations from Plan

- **Phase 3 Claude consultation**: Claude review repeatedly failed with API 500/internal server errors. Per architect instruction on 2026-05-16T18:07:06.073Z, the phase proceeded with degraded consultation and a COMMENT artifact documenting the failure.
- **Schema metadata**: Optional wrapper metadata such as `observed_program` was not added. This was intentional to preserve schema version 1 compatibility.

## Implementation Summary

Implemented work:

- Extracted the tracing backend into `src/ai_observe/observe.py`.
- Kept `ai_observe.codex_observe` as a compatibility alias so existing imports and monkeypatch-based tests continue to work.
- Added preferred `AI_OBSERVE_*` environment variable handling with `CODEV_OBSERVE_*` aliases where required.
- Added named shims: `bin/codex`, `bin/claude`, `bin/gemini`, and `bin/opencode`.
- Added generic command wrapper: `bin/ai-observe [--session SESSION] -- command [args...]`.
- Implemented named-program resolver priority and generic command resolver behavior, including `AI_OBSERVE_REAL_COMMAND` argv replacement and observer-shim recursion avoidance.
- Added resolver, env precedence, and wrapper integration tests using fake executables and fake `strace`.
- Rewrote `docs/observe.md` as generic command observer documentation and updated viewer wording.

## Schema Compatibility

The JSONL schema remains version 1. The existing `command` field continues to record the resolved real executable path plus argv passed under `strace`. No new JSONL metadata fields were added.

Viewer sanitization remains unchanged: sensitive fields such as `command`, `raw_syscall`, process metadata, and PIDs are not sent to the browser page.

## Architecture Updates

Updated `codev/resources/arch.md` with a new **Generic command observer** section documenting the shared observer core, generic and named entry points, Codex compatibility facade, `AI_OBSERVE_*` / `CODEV_OBSERVE_*` precedence invariant, recursion-avoidance requirements, and JSONL schema-version invariant.

## Lessons Learned Updates

Updated `codev/resources/lessons-learned.md` with three generalizable lessons:

- Preserve compatibility facades during generic refactors.
- Make recursion-avoidance tests cover cross-installation PATH shims, not only same-directory self-skips.
- Turn broad compatibility promises into explicit matrix tests.

## Lessons Learned

### What Went Well

- The SPIR phase split worked well: core extraction, resolver entry points, integration compatibility, and docs/regression each had focused deliverables.
- Keeping `ai_observe.codex_observe` as an alias/facade avoided divergent compatibility code paths and preserved monkeypatch-heavy live tracing tests.
- Fake executable and fake `strace` integration tests made tool support testable without requiring real Claude, Gemini, OpenCode, or Codex installations.

### Challenges Encountered

- **Legacy naming leakage**: Early generic-core errors still mentioned only `CODEV_OBSERVE_*`. This was caught by consultation and fixed to prefer `AI_OBSERVE_*` while mentioning legacy aliases where useful.
- **Recursive shim detection**: Same-directory self-skips were insufficient for generic command mode because observer shims may appear elsewhere in PATH. Tests and resolver logic were expanded to cover cross-directory observer wrappers.
- **Compatibility matrix completeness**: The initial env-alias tests did not cover every promised shared variable class. Additional tests were added for strict parse, include-log-writes, symlink-dir allowance, signal grace, and preferred `AI_OBSERVE_REAL_CODEX` wrapper execution.
- **Consultation availability**: Claude was unavailable during Phase 3 due to repeated API 500 errors; the architect approved degraded consultation for that phase.

### What Would Be Done Differently

- Start the compatibility matrix from the spec table and require one test or explicit non-test rationale for each env variable before implementation review.
- Include cross-installation recursion fixtures in the first resolver test pass instead of only testing the currently invoked wrapper directory.
- Update architecture and lessons-learned notes as soon as the generic-core design stabilizes, rather than waiting until the final review phase.

### Methodology Improvements

- Protocol review prompts should remind builders to update `codev/resources/arch.md` and `codev/resources/lessons-learned.md` incrementally when a phase introduces durable architecture or process lessons.
- Consultation prompts for compatibility refactors should explicitly ask reviewers to compare the full public/test-facing compatibility surface, not only user-facing CLI behavior.

## Technical Debt

- Non-Linux backends remain out of scope.
- Redaction/safe telemetry export remains out of scope.
- Deep transcript/session parsing for individual AI tools remains out of scope.
- A future schema version may add explicit wrapper metadata such as `observed_program`, but this iteration deliberately preserved schema version 1 without additional fields.
- Exact removal timing for `CODEV_OBSERVE_*` aliases remains unspecified beyond the compatibility window.

## Consultation Feedback

### Specify Phase (Round 1)

#### Codex
- **Concern**: `AI_OBSERVE_REAL_COMMAND` generic-mode semantics were ambiguous.
  - **Addressed**: The spec now states the command token after `--` is required and that `AI_OBSERVE_REAL_COMMAND` replaces only `argv[0]` for execution and JSONL `command`, preserving remaining args.
- **Concern**: Generic recursion avoidance was underspecified.
  - **Addressed**: The spec now requires generic mode to avoid resolving to `bin/ai-observe` itself or observer-provided named shims.
- **Concern**: Generic entry-point delivery expectations were too loose.
  - **Addressed**: The spec now requires a concrete checkout entry point at `bin/ai-observe`; package console scripts remain optional.
- **Concern**: Backward-compatible alias precedence needed more testable requirements.
  - **Addressed**: The spec now explicitly requires preferred-vs-legacy precedence tests for shared variables including disable, observe directory, session id, and quiet mode.

#### Claude
- **Concern**: Clarify invocation without `--` and concrete `bin/ai-observe` delivery.
  - **Addressed**: Added a MUST requirement that missing `--` or missing command prints usage/help, exits nonzero, and does not run `strace` or a child command.

### Plan Phase (Round 1)

#### Codex
- **Concern**: The plan underrepresented `tests/test_live_trace.py` and its direct dependency on `ai_observe.codex_observe` internals.
  - **Addressed**: Phase 1 success criteria now require preserving/re-exporting the live-trace compatibility surface, and Phase 1/3 test commands explicitly include `python3 -m unittest tests.test_live_trace`.
- **Concern**: Live-parse-related aliases should be called out specifically.
  - **Addressed**: Phase 3 success criteria now explicitly cover live parse, live poll/join settings, strict parse, include log writes, symlink-dir allowance, and signal grace.

#### Claude
- **Concern**: Preserve default `.codev/observe` ancestor-search semantics.
  - **Addressed**: Phase 1 success criteria now state default observe-dir discovery remains unchanged and tool-agnostic.
- **Concern**: Clarify parameterization strategy.
  - **N/A**: The plan kept implementation strategy flexible while strengthening facade and resolver criteria enough to enforce behavior.

### Implement Phase 1: Generic Core (Round 1)

#### Codex
- **Concern**: Missing-`strace` error still preferred `CODEV_OBSERVE_DISABLE`.
  - **Addressed**: Updated the error to prefer `AI_OBSERVE_DISABLE=1` and mention legacy `CODEV_OBSERVE_DISABLE=1` second.
- **Concern**: Invalid session-id error still referred only to `CODEV_OBSERVE_SESSION_ID`.
  - **Addressed**: Updated the error to mention `AI_OBSERVE_SESSION_ID/CODEV_OBSERVE_SESSION_ID`.

#### Claude
- No blocking concerns raised — APPROVE.

### Implement Phase 2: Entry Points and Resolvers (Round 1)

#### Codex
- **Concern**: Generic recursion avoidance only recognized observer shims beside the invoked wrapper.
  - **Addressed**: Resolver logic now detects observer shim launchers in other PATH directories and skips/rejects them as recursive targets.
- **Concern**: Resolver tests missed cross-directory recursive targets.
  - **Addressed**: Added tests for skipping an observer-provided shim in a different directory and rejecting `ai-observe` wrapper recursion when no real target follows.

#### Claude
- No blocking concerns raised — APPROVE.

### Implement Phase 3: Integration Compatibility Tests (Round 1)

#### Codex
- **Concern**: Preferred-vs-legacy compatibility coverage was incomplete for strict parse, include-log-writes, symlink-dir allowance, and signal grace.
  - **Addressed**: Expanded `tests/test_observe_env.py` and `tests/test_observe_cli.py` to cover those aliases and strict-parse integration precedence.
- **Concern**: Actual `bin/codex` wrapper execution with preferred `AI_OBSERVE_REAL_CODEX` was missing.
  - **Addressed**: Added end-to-end wrapper coverage for `bin/codex` with `AI_OBSERVE_REAL_CODEX`, fake `strace`, and preferred observe/session/quiet variables.

#### Claude
- **Concern**: Consultation failed repeatedly with Claude API 500/internal server errors.
  - **N/A**: Architect approved proceeding under degraded consultation; the failure was documented in the phase artifact.

### Implement Phase 4: Documentation and Regression (Round 1)

#### Codex
- No concerns raised — APPROVE.

#### Claude
- No concerns raised — APPROVE.

#### Gemini
- No concerns raised — APPROVE.

## Tests Run

Focused commands run during implementation included:

```bash
python3 -m unittest tests.test_codex_observe tests.test_live_trace
python3 -m unittest tests.test_observe_resolver
python3 -m unittest tests.test_observe_env tests.test_observe_cli
python3 -m unittest tests.test_codex_observe tests.test_live_trace tests.test_observe_resolver tests.test_observe_cli
```

Final regression command during Phase 4:

```bash
python3 -m unittest discover -s tests
```

Final result after Phase 4 documentation updates: full suite passed (`157 tests OK`).

Review-phase verification before PR:

```bash
python3 -m unittest discover -s tests
# Ran 157 tests in 15.261s — OK

# Smoke-tested fake-tool execution through:
# - bin/codex with legacy CODEV_OBSERVE_REAL_CODEX
# - bin/claude with AI_OBSERVE_REAL_CLAUDE
# - bin/ai-observe -- <fake-tool> args

ls -l bin/ai-observe bin/codex bin/claude bin/gemini bin/opencode
# all five files are executable

npm run test:e2e 2>&1 || echo 'e2e tests skipped (not configured)'
# skipped: no package.json / e2e suite configured
```

## Flaky Tests

No flaky tests encountered; no tests were skipped as flaky.

## Follow-up Items

- Decide a removal timeline for legacy `CODEV_OBSERVE_*` aliases in a future release.
- Consider schema-versioned wrapper metadata such as `observed_program` if consumers need it.
- Consider packaging console scripts if/when project packaging metadata is introduced.
