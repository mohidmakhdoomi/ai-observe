# Implementation Plan: generalize-observer-for-claude

## Overview

Refactor the current Codex-specific observer into a generic command observer without changing the strace parser schema or viewer behavior. The implementation will first extract a reusable core with environment alias support, then add named and generic command entry points with resolver tests, then update end-to-end coverage and docs. Codex compatibility remains a first-class regression target throughout.

## Phase machine-readable block

```json
{
  "phases": [
    {
      "id": "phase-1-generic-core",
      "name": "Generic observer core and compatibility adapter",
      "depends_on": []
    },
    {
      "id": "phase-2-entrypoints-resolvers",
      "name": "Named shims, generic CLI, and resolver coverage",
      "depends_on": ["phase-1-generic-core"]
    },
    {
      "id": "phase-3-integration-compatibility-tests",
      "name": "Generic execution and compatibility regression tests",
      "depends_on": ["phase-2-entrypoints-resolvers"]
    },
    {
      "id": "phase-4-docs-review-regression",
      "name": "Generic observer documentation and final regression",
      "depends_on": ["phase-3-integration-compatibility-tests"]
    }
  ]
}
```

## Phases

### Phase 1: Generic observer core and compatibility adapter (`phase-1-generic-core`)

- **Objective**: Move Codex-specific wrapper logic into a generic observer module while preserving the existing `ai_observe.codex_observe` import path and current Codex behavior.
- **Files**:
  - `src/ai_observe/observe.py` — create the generic implementation extracted from `codex_observe.py`: shared `run` path, strace invocation, log preparation, live parser handling, signal forwarding, safe file helpers, generic env lookup helpers, and parameterized real-command resolution hooks.
  - `src/ai_observe/codex_observe.py` — convert to a thin compatibility adapter that re-exports existing public helpers where practical and calls the generic core for the Codex shim.
  - `src/ai_observe/__init__.py` — update package description text from Codex-specific to generic observer wording.
- **Dependencies**: None.
- **Success Criteria**:
  - Existing tests importing `from ai_observe import codex_observe` still pass or require only mechanical expectation changes caused by the generic module split.
  - `codex_observe.main(argv, env)` and `codex_observe.run(argv, env)` continue to work for the Codex shim.
  - Existing helper functions used by tests, including log preparation, safe JSONL/trace helpers, session-id sanitization, exit-code normalization, and real Codex resolution, remain available from `ai_observe.codex_observe` or are deliberately re-exported there.
  - The traced command recorded in JSONL remains the resolved real executable path plus argv.
  - `schema_version` and trace parsing behavior remain unchanged.
  - Error handling, parser-failure partial output, live parse fallback, signal forwarding, exit code preservation, and strict parse behavior match existing Codex behavior.
- **Tests**:
  - Run `python3 -m unittest tests.test_codex_observe` after the refactor.
  - Add or adapt focused unit tests for generic environment lookup so `AI_OBSERVE_*` values override `CODEV_OBSERVE_*` aliases for shared variables.

### Phase 2: Named shims, generic CLI, and resolver coverage (`phase-2-entrypoints-resolvers`)

- **Objective**: Add concrete user entry points and implement resolver semantics for Codex, Claude, Gemini, OpenCode, and arbitrary command mode.
- **Files**:
  - `bin/codex` — update to call the generic shim entry point for `codex` while preserving existing behavior.
  - `bin/claude` — create executable shim for Claude Code.
  - `bin/gemini` — create executable shim for Gemini CLI.
  - `bin/opencode` — create executable shim for OpenCode.
  - `bin/ai-observe` — create executable generic CLI that requires `--` and a command, supports `--session`, and invokes the generic observer.
  - `src/ai_observe/observe.py` — implement named-shim resolver priority, generic command parsing/resolution, `AI_OBSERVE_REAL_COMMAND` argv[0] replacement semantics, recursion avoidance, and usage/help failure for missing `--`/command.
  - `tests/test_observe_resolver.py` — add pure resolver and CLI parsing tests for all named programs and generic mode.
- **Dependencies**: Phase 1.
- **Success Criteria**:
  - `bin/claude`, `bin/gemini`, `bin/opencode`, and `bin/ai-observe` exist, are executable, and import from `src` like the existing `bin/codex` checkout workflow.
  - Named shim lookup priority is implemented: `AI_OBSERVE_REAL_<PROGRAM>`, then Codex-only `CODEV_OBSERVE_REAL_CODEX`, then non-recursive `PATH` match, then adjacent `<program>.real` / `<program>.bin`, else exit `127` with actionable error.
  - `AI_OBSERVE_REAL_CODEX` takes precedence over `CODEV_OBSERVE_REAL_CODEX` when both are set.
  - Generic mode requires `--` and at least one command token; missing separator or command prints usage/help and exits nonzero before running `strace` or a child.
  - Generic mode resolves normal commands through `PATH` or explicit paths while avoiding `bin/ai-observe` and observer-provided named shims as recursive targets.
  - `AI_OBSERVE_REAL_COMMAND=/real/tool ai-observe -- tool arg` executes and records `[/real/tool, "arg"]`, replacing only the command token and preserving remaining args.
  - All command execution continues to use argv arrays, not shell interpolation.
- **Tests**:
  - Resolver tests for `codex`, `claude`, `gemini`, and `opencode` covering explicit env var, PATH skip-self behavior, adjacent `.real`/`.bin`, and self-recursion rejection.
  - Codex-specific resolver tests for legacy `CODEV_OBSERVE_REAL_CODEX` and preferred-over-legacy precedence.
  - Generic parsing/resolution tests for missing `--`, missing command, direct explicit path, PATH lookup, recursive target rejection, and `AI_OBSERVE_REAL_COMMAND` argv replacement.

### Phase 3: Generic execution and compatibility regression tests (`phase-3-integration-compatibility-tests`)

- **Objective**: Extend fake-strace and subprocess coverage so actual wrapper execution is verified for Codex compatibility, at least one non-Codex named shim, and arbitrary generic command mode.
- **Files**:
  - `tests/test_codex_observe.py` — keep existing Codex end-to-end coverage passing; update imports/assertions only as needed for generic stderr prefix or module split.
  - `tests/test_observe_cli.py` — add subprocess/fake-strace tests for generic `bin/ai-observe` and non-Codex shims.
  - `src/ai_observe/observe.py` — make any small behavior fixes surfaced by integration tests, especially env alias precedence and disable/bypass semantics.
- **Dependencies**: Phase 2.
- **Success Criteria**:
  - Existing Codex workflows continue to pass with both `AI_OBSERVE_REAL_CODEX` and legacy `CODEV_OBSERVE_REAL_CODEX`.
  - `AI_OBSERVE_DISABLE=1` bypasses tracing and execs the resolved real command for named and generic entry points.
  - Legacy `CODEV_OBSERVE_DISABLE=1` still bypasses for Codex compatibility.
  - Preferred shared variables override legacy aliases for at least observe dir, session id, quiet mode, disable, live parse, live poll/join settings, strict parse, include log writes, symlink-dir allowance, and signal grace where applicable.
  - Fake-strace tests verify generic command mode writes `.trace`/`.jsonl`, preserves child exit code, records the real command argv, and produces schema-compatible events.
  - At least one non-Codex shim, preferably `claude` because its usage mirrors Codex-style prompts, is exercised end-to-end with a fake real executable and fake strace.
  - Missing `strace` returns `127` before running the child command for generic and named paths.
  - Ptrace-denied empty trace warning remains actionable with generic wording; tests tolerate compatibility wording for `bin/codex` if deliberately retained.
  - Viewer tests continue to pass, confirming JSONL schema and sanitization remain compatible.
- **Tests**:
  - Run `python3 -m unittest tests.test_codex_observe`.
  - Run `python3 -m unittest tests.test_observe_resolver tests.test_observe_cli`.
  - Run `python3 -m unittest discover -s tests` before completing the phase.
  - Keep the existing real-`strace` process-tree test skip behavior when `strace` is unavailable or ptrace is denied.

### Phase 4: Generic observer documentation and final regression (`phase-4-docs-review-regression`)

- **Objective**: Reframe public documentation from Codex-specific to generic command observer, document all supported entry points and limitations, and record implementation review notes.
- **Files**:
  - `docs/observe.md` — rewrite/update title, setup, runtime requirements, env var table, resolver order, named shim examples, generic command examples, JSONL schema notes, security warnings, limitations, and troubleshooting.
  - `docs/viewer.md` — update any wording that assumes only Codex-produced JSONL if present.
  - `codev/reviews/11-generalize-observer-for-claude.md` — create review notes during Review phase with implementation summary, test commands/results, schema compatibility notes, and flaky-test skips if any occur.
- **Dependencies**: Phase 3.
- **Success Criteria**:
  - `docs/observe.md` presents ai-observe as a generic Linux command filesystem observer, not a Codex integration layer.
  - Documentation includes separate sections/examples for Codex, Claude Code, Gemini CLI, OpenCode, and arbitrary commands.
  - Preferred `AI_OBSERVE_*` variables are documented first, with `CODEV_OBSERVE_*` clearly marked as backwards-compatible aliases for the compatibility window.
  - Security warning remains prominent near setup and JSONL schema sections and explicitly mentions secrets in paths, argv, and raw syscall text.
  - Limitations explicitly cover Linux/strace/ptrace requirements, process-tree scope, remote services, editor/IDE edits outside the traced tree, sandbox/privilege issues, background daemons, and known parser fidelity limits.
  - Troubleshooting covers recursion/wrong binary, missing strace, ptrace denial, unwritable observe dir, symlink observe dir, parser partial output, and generic CLI usage errors.
  - Review notes record final test results and any deferred work such as future schema metadata or removal timeline for legacy aliases.
- **Tests**:
  - Run `python3 -m unittest discover -s tests`.
  - Run quick smoke commands with fake tools where practical:
    - `bin/codex` with legacy real env var.
    - `bin/claude` with `AI_OBSERVE_REAL_CLAUDE`.
    - `bin/ai-observe -- <fake-tool> args`.
  - Verify executable bits on new `bin/*` files.

## Cross-phase implementation notes

- Do not edit `codev/projects/11-generalize-observer-for-claude/status.yaml` directly; porch owns project state.
- Do not use `git add .` or `git add -A`; stage files explicitly.
- Keep commits atomic by phase where possible.
- Preserve schema version 1 unless a deliberate schema migration is introduced, which is not planned for this feature.
- Do not add transcript/session parsing for target AI tools; the observer should remain command/process-tree based.
- Prefer small pure helpers for environment lookup and resolver behavior so precedence and recursion rules can be tested without subprocesses.
- Keep `ai_observe.codex_observe` as a compatibility facade; avoid forcing downstream callers to migrate immediately.
- When both preferred and legacy env vars exist, preferred `AI_OBSERVE_*` values should win. Codex real-binary lookup must also support legacy `CODEV_OBSERVE_REAL_CODEX`.
- Existing internal test knobs can remain undocumented, but tests need deterministic parser-failure coverage after refactor.
- Avoid broad chmod or shell eval patterns in shims; keep launchers minimal and argv-preserving.
- If stderr prefixes are changed to `ai-observe:`, update tests deliberately. It is acceptable for `bin/codex` compatibility paths to retain or tolerate `codex-observe:` where needed.

## Risk Assessment

- **Compatibility regression in Codex shim**: Mitigate by keeping a thin `codex_observe` adapter and running all existing `tests.test_codex_observe` after each relevant phase.
- **Environment alias precedence bugs**: Mitigate with pure env lookup tests and integration tests for disable, dir, session id, quiet mode, and Codex real-binary precedence.
- **Recursive shim execution**: Mitigate with resolver tests that put observer shims first in `PATH`, explicit self-path rejection tests, and docs telling users to resolve real binaries before prepending `bin`.
- **Ambiguous generic command semantics**: Mitigate with parser tests and docs for missing `--`, required command token, direct command lookup, and `AI_OBSERVE_REAL_COMMAND` argv replacement.
- **JSONL schema drift**: Mitigate by leaving `trace_parser.py` schema unchanged, asserting emitted `command` arrays in integration tests, and running viewer tests to confirm sanitization still ignores sensitive fields.
- **Signal/exit-code behavior changes during refactor**: Mitigate by preserving existing process-management code and keeping current signal escalation and exit-code tests.
- **Security posture weakening**: Mitigate by preserving safe log path helpers, symlink checks, restrictive permissions, argv-array execution, and prominent documentation warnings.
- **Test suite requires real AI CLIs**: Avoid this entirely by using fake executables and fake strace in all new resolver/integration tests.
