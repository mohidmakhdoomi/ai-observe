# Phase 6 Backend Abstraction — Iteration 1 Rebuttals

## Review Summary

- Gemini: **APPROVE**
- Codex: **REQUEST_CHANGES**
- Claude: **APPROVE**

## Addressed REQUEST_CHANGES feedback

### 1. Explicit `AI_OBSERVE_BACKENDS=strace,snapshot` coverage was missing

**Feedback:** Phase 6 requires the explicit combined backend setting to be accepted and tested, not just the default/no-env path.

**Change made:** Added `test_explicit_strace_snapshot_backend_setting_matches_default_layered_mode` to `tests/test_observe_cli.py`.

**What it verifies:**
- `AI_OBSERVE_BACKENDS=strace,snapshot` is accepted explicitly.
- The command runs in layered mode.
- Direct `strace` events and inferred `snapshot` events are both emitted as expected.

### 2. Snapshot-only launch failures still reported strace-specific error text

**Feedback:** In snapshot-only mode, the launch error path in `observe.py` still said `failed to start strace` / `failed to run strace`, even though no strace backend was selected.

**Change made:** Updated `src/ai_observe/observe.py` to derive a backend-aware launch subject:
- `strace` when the strace backend is selected
- `observed command` when running without strace (for example `AI_OBSERVE_BACKENDS=snapshot`)

**Result:** Snapshot-only troubleshooting no longer produces misleading strace-specific launch errors.

## Validation

Re-ran the Phase 6 regression set after the fixes:

```bash
python3 -m unittest tests.test_backends tests.test_observe_cli tests.test_snapshot tests.test_live_trace
```

Result: **54 tests passed**.
