# Rebuttal: phase-2-entrypoints-resolvers iteration 1

## Review summary

- Codex requested changes because generic recursion avoidance only recognized observer shims beside the currently invoked wrapper, and tests only covered same-directory shim rejection.
- Claude approved the phase and confirmed the entry points, resolver order, CLI parsing, and full test suite behavior.

## Responses to REQUEST_CHANGES

### 1. Generic recursion avoidance was too narrow

Accepted. I updated `src/ai_observe/observe.py` so generic command recursion detection is no longer limited to the current wrapper directory.

The generic resolver now:

- still rejects the currently invoked wrapper and same-directory observer shims;
- additionally inspects PATH/explicit candidates named like observer shims (`ai-observe`, `codex`, `claude`, `gemini`, `opencode`);
- treats a candidate as an observer shim if its launcher text imports `ai_observe.observe` and calls `main_shim`/`main_generic`, or if it imports the legacy `ai_observe.codex_observe` shim module.

This lets `ai-observe -- codex ...` skip another ai-observe-provided `codex` shim in a different PATH directory and continue to a later real `codex`, while not rejecting arbitrary real binaries merely because their basename is `codex`.

### 2. Resolver tests missed cross-directory recursive targets

Accepted. I added tests covering:

- PATH lookup skips an observer-provided `codex` shim in a different directory and finds a later real `codex`.
- PATH lookup rejects an `ai-observe` command that resolves to another observer wrapper in a different directory when no real target follows.

## Validation after changes

Re-ran:

```bash
python3 -m unittest tests.test_observe_resolver
python3 -m unittest discover -s tests
```

Both passed. Full discovery result: 147 tests OK.

## Result

All requested changes have been addressed. No disagreements.
