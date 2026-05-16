# Rebuttal: PR review iteration 1

## Review summary

- Gemini approved PR #12 after the latest branch update.
- Claude approved PR #12 and verified the test suite.
- Codex requested changes for one recursion-avoidance gap in named-shim resolution.

## Responses to REQUEST_CHANGES

### 1. Named-shim resolution could select observer shims from other installs

Codex noted that `resolve_real_program()` only rejected the currently invoked shim path. This meant a named shim such as `claude` could resolve another ai-observe-provided `claude` shim from a different PATH directory, or an explicit `AI_OBSERVE_REAL_<PROGRAM>` could point at another observer shim, causing recursive wrapper chaining.

**Addressed.** I updated `src/ai_observe/observe.py` so named-shim validation uses the same content-aware observer-shim detection as generic command mode:

- `validate_real_candidate()` now calls `is_observer_shim(path, wrapper_real)` instead of comparing only to the current wrapper path.
- PATH lookup catches observer-shim recursion errors, skips those recursive candidates, and continues to later PATH entries so a real executable can still be found.
- Explicit `AI_OBSERVE_REAL_<PROGRAM>` / Codex legacy real env values that point at observer shims are rejected with the existing actionable recursion error.

### 2. Named-shim resolver tests missed cross-directory recursion cases

Codex also noted that existing cross-directory recursion tests covered generic mode but not named shims.

**Addressed.** I added two resolver tests in `tests/test_observe_resolver.py`:

- `test_path_lookup_skips_observer_shim_in_other_directory_for_named_program`
- `test_explicit_real_rejects_observer_shim_in_other_directory_for_named_program`

These cover both PATH skip-and-continue behavior and explicit-env rejection for named shims.

## Validation after changes

Ran:

```bash
python3 -m unittest tests.test_observe_resolver
python3 -m unittest discover -s tests
```

Results:

- Resolver suite passed: `16 tests OK`.
- Full test discovery passed: `159 tests OK`.

The PR body was updated to reflect the 159-test result, and the branch was pushed to PR #12.

## Result

All Codex-requested changes have been addressed. No disagreements.
