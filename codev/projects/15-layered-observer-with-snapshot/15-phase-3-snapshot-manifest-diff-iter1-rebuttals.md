# Phase 3 Iteration 1 Rebuttal

## Summary

Gemini approved and Claude approved. Codex requested changes on two concrete conservative-diffing issues plus missing regression coverage. I agreed with those points, fixed the implementation, and added the requested tests.

## Codex: hash-error path could cause a false-positive modify

**Feedback:** If hashing fails on only one side, `_changed_operation()` treated `old.hash != new.hash` as evidence of content change, which could emit a false-positive `modify`.

**Resolution:** Updated `src/ai_observe/snapshot.py` so hash differences only drive `modify` when **both** hashes are present and different. Hash absence on either side is now treated conservatively/no-event unless size or `mtime_ns` changed independently.

**Tests added:**
- `test_hash_error_records_diagnostic_without_hash_only_modify_signal`

## Codex: rename detection was not root-scoped

**Feedback:** `_detect_renames()` paired delete/create records by `(dev, ino)` even across different watched roots, but the spec requires conservative rename detection within the same root.

**Resolution:** Added internal root tracking on manifest entries captured from a watched root, excluded that field from public snapshot payloads, and changed rename matching to require the same `(root, dev, ino)` identity. Cross-root matches now remain `delete` + `create`.

**Tests added:**
- `test_rename_detection_does_not_cross_root_boundaries`

## Codex: missing unreadable/hash-error diagnostic coverage

**Feedback:** Phase 3 needed explicit tests for hash-error and unreadable-path diagnostics.

**Resolution:** Added deterministic unit coverage for both cases using mocks.

**Tests added:**
- `test_hash_error_records_diagnostic_without_hash_only_modify_signal`
- `test_unreadable_path_records_diagnostic`

## Verification

- `python3 -m unittest tests.test_snapshot` — passed (12 tests)
- `python3 -m unittest tests.test_trace_parser tests.test_live_trace tests.test_observe_cli tests.test_observe_env tests.test_viewer_tailer tests.test_viewer_server tests.test_viewer_aggregator tests.test_snapshot` — passed (127 tests)
