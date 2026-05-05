# Review 1: Monitor filesystem modifications from wrapped Codex processes

## Summary

Implemented Linux-first Codex filesystem observer using Python stdlib wrapper plus `strace` parser.

## What changed

- Added `bin/codex` drop-in shim.
- Added `src/ai_observe/codex_observe.py` wrapper.
- Added `src/ai_observe/trace_parser.py` strace-to-JSONL parser.
- Added unittest coverage for wrapper, parser, fake strace, failure paths.
- Added `docs/observe.md` setup, schema, limitations, troubleshooting.

## Validation

```bash
python3 -m unittest discover -s tests -v
```

Result: passing, 27 tests total, 1 skipped.

Live real-`strace` process-tree test is present and skips because `strace` is not installed in this environment. Tests use fake `strace` plus parser fixtures; missing-real-strace behavior covered.

## Notes

- Parser intentionally favors false negatives over false positives for malformed strace fragments.
- `CODEV_OBSERVE_TEST_FAIL_AFTER` exists only as internal test hook for deterministic parser-failure wrapper coverage.
- Logs contain sensitive raw argv/syscall/path data; docs and stderr warning call this out.

## Flaky Tests

None.
