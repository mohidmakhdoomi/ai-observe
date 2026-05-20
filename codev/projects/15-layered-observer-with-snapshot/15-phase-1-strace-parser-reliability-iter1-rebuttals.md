# Phase 1 Implementation Review Rebuttals — Iteration 1

## Review Summary

- Gemini: APPROVE.
- Codex: REQUEST_CHANGES, focused on recovery-contract edge cases.
- Claude: APPROVE.

## Codex REQUEST_CHANGES

### Live-timeout rebuild should not fail strict parse after successful recovery

**Feedback:** A live parser join timeout set `parse_failed = True` and kept it true even after a successful full-trace rebuild to `<session>.jsonl.rebuilt`, causing `AI_OBSERVE_STRICT_PARSE=1` sessions to exit 1 despite having an authoritative rebuilt artifact.

**Change made:** After a successful timeout rebuild, the implementation now sets `parse_failed = False` while keeping `parser_status = "live_timeout_rebuilt"` and marking `<session>.jsonl.rebuilt` as the authoritative complete stream in `<session>.meta.json`. The timeout remains discoverable through metadata and stderr, but strict parse no longer treats a successfully recovered timeout as a parser failure.

**Tests:** Updated `test_join_timeout_warns_and_preserves_partial_jsonl` so strict and non-strict successful timeout rebuilds both preserve the observed command exit code while verifying `.jsonl.rebuilt` and meta precedence.

### Meta sidecar mislabeled live partial JSONL when no authoritative rebuild exists

**Feedback:** `build_session_meta()` treated all `authoritative_path is None` cases as `jsonl: "inferred_or_empty_placeholder"`, which is incorrect when a live timeout or live-error path leaves `<session>.jsonl` as a partial live stream.

**Change made:** `build_session_meta()` now uses the parser status when no authoritative event path exists. For `live_timeout*` and `live_error*` statuses, `<session>.jsonl` is labeled `partial_live`; for parser-failure-only paths it remains `inferred_or_empty_placeholder` because the wrapper truncates/empties canonical JSONL and writes direct partial evidence to `<session>.jsonl.partial`.

**Tests:** Added `test_join_timeout_rebuild_failure_labels_live_jsonl_as_partial`, which forces a live timeout and rebuild failure, verifies strict parse still exits 1, and checks `<session>.meta.json` has no authoritative event path while labeling `<session>.jsonl` as `partial_live`.

### Missing test coverage for rebuild-failure branches

**Feedback:** The previous tests covered the happy timeout-rebuild path but did not guard the failed-rebuild metadata contract.

**Change made:** Added the rebuild-failure test described above. It covers the previously unguarded branch and asserts the recovery contract explicitly.

## Validation

- `python3 -m unittest tests.test_trace_parser tests.test_live_trace tests.test_observe_cli tests.test_observe_env` — PASS (58 tests).
- `python3 -m unittest discover -s tests` — PASS (167 tests).
