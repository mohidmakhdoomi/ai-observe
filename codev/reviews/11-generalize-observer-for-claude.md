# Review: generalize-observer-for-claude

## Implementation summary

Spec 11 generalized the observer from a Codex-specific wrapper into a command-oriented filesystem observer while preserving Codex compatibility.

Implemented work:

- Extracted the tracing backend into `src/ai_observe/observe.py`.
- Kept `ai_observe.codex_observe` as a compatibility alias so existing imports and monkeypatch-based tests continue to work.
- Added preferred `AI_OBSERVE_*` environment variable handling with `CODEV_OBSERVE_*` aliases where required.
- Added named shims:
  - `bin/codex`
  - `bin/claude`
  - `bin/gemini`
  - `bin/opencode`
- Added generic command wrapper:
  - `bin/ai-observe [--session SESSION] -- command [args...]`
- Implemented named-program resolver priority and generic command resolver behavior, including `AI_OBSERVE_REAL_COMMAND` argv replacement and observer-shim recursion avoidance.
- Added resolver, env precedence, and wrapper integration tests using fake executables and fake `strace`.
- Rewrote `docs/observe.md` as generic command observer documentation and updated viewer wording.

## Schema compatibility

The JSONL schema remains version 1. The existing `command` field continues to record the resolved real executable path plus argv passed under `strace`. No new JSONL metadata fields were added.

Viewer sanitization remains unchanged: sensitive fields such as `command`, `raw_syscall`, process metadata, and PIDs are not sent to the browser page.

## Consultation notes

- Spec and plan phases completed with Codex/Claude consultation and requested changes addressed.
- Phase 1 and Phase 2 implementation consultations completed; requested Codex changes were addressed.
- Phase 3 Codex consultation requested expanded compatibility-matrix coverage. Added tests for preferred/legacy strict parse, include log writes, symlink-dir allowance, signal grace, and actual `bin/codex` execution with `AI_OBSERVE_REAL_CODEX`.
- Phase 3 Claude consultation repeatedly failed with Claude API 500/internal server errors. Per architect instruction on 2026-05-16T18:07:06.073Z, proceeded under degraded consultation and documented the failures in the phase-3 artifacts.

## Tests run

Focused commands run during implementation included:

```bash
python3 -m unittest tests.test_codex_observe tests.test_live_trace
python3 -m unittest tests.test_observe_resolver
python3 -m unittest tests.test_observe_env tests.test_observe_cli
python3 -m unittest tests.test_codex_observe tests.test_live_trace tests.test_observe_resolver tests.test_observe_cli
```

Final regression command:

```bash
python3 -m unittest discover -s tests
```

Final result after Phase 4 documentation updates: full suite passed.

## Deferred work

- Non-Linux backends remain out of scope.
- Redaction/safe telemetry export remains out of scope.
- Deep transcript/session parsing for individual AI tools remains out of scope.
- A future schema version may add explicit wrapper metadata such as `observed_program`, but this iteration deliberately preserved schema version 1 without additional fields.
- Exact removal timing for `CODEV_OBSERVE_*` aliases remains unspecified beyond the compatibility window.

## Flaky Tests

None skipped.
