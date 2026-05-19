# Phase 2 Iteration 1 Rebuttal

## Summary

Gemini approved. Codex and Claude requested changes. I addressed Codex's substantive forward-compatibility concern with a code/test change. Claude's review appears to have inspected a stale/pre-change view of the worktree; the cited line numbers and claims do not match the current implementation.

## Codex: future schema versions skipped unconditionally

**Feedback:** `viewer/tailer.py` accepted only schema versions `{1, 2}` and skipped all future schema versions, while the spec/plan says higher schema versions should be warned/skipped only when a consumer cannot safely normalize them.

**Resolution:** Changed the tailer to allow forward-compatible normalization for future integer schema versions when the event still has the known viewer-safe fields needed by the current UI/aggregator (`operation` and at least one of `path`, `old_path`, or `new_path`). Sanitization remains whitelist-only, so unknown future fields are not exposed to the browser. Events with invalid schema versions, non-positive versions, or future shapes that lack the fields needed for safe current rendering are still warned/skipped.

Added `test_future_schema_with_known_safe_fields_is_normalized` and updated the unsupported-future test to cover a future event that cannot safely normalize.

## Claude: phase 2 allegedly not implemented

**Feedback:** Claude reported that Phase 2 had not been implemented: `trace_parser.py` still had `SCHEMA_VERSION = 1`, the tailer still rejected v2, and tests still asserted v1/v2-rejection behavior.

**Response:** This appears to be a stale inspection result. The current worktree has:

- `src/ai_observe/trace_parser.py`: `SCHEMA_VERSION = 2` and `_make_event` emits `source: "strace"`, `confidence: "direct"`.
- `src/ai_observe/viewer/tailer.py`: accepts missing/v1/v2 events, normalizes missing provenance as `strace/direct`, and now also forward-normalizes compatible future events.
- `tests/test_trace_parser.py`: asserts parser-emitted schema-v2 provenance.
- `tests/test_viewer_tailer.py`: covers missing schema, explicit v1, explicit v2, compatible future schema, and unsupported future schema.
- `tests/test_viewer_server.py` and `tests/test_observe_cli.py`: updated for the browser-visible provenance whitelist / emitted v2 events.

No additional implementation change was needed for the stale findings beyond the Codex fix above.

## Verification

- `python3 -m unittest tests.test_trace_parser tests.test_viewer_tailer tests.test_viewer_server tests.test_viewer_aggregator tests.test_observe_cli` — passed (87 tests)
- `python3 -m unittest discover -s tests` — passed (170 tests)
