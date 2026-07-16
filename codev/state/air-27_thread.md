# air-27 thread — Add agy (Antigravity CLI) observer shim (issue #27)

## Phase: implement

## BLOCKER surfaced to architect (issue contradicts a hard invariant)

The issue scope says: *"pyproject.toml — add the `agy` named entry point so
installed workflows get the shim."* This directly contradicts the codebase's
established, test-enforced design:

- `pyproject.toml:38-40` — named shims (claude/codex/gemini/opencode) are
  **intentionally NOT** entry points because they'd shadow the real tools;
  they stay checkout-only `bin/*` shims.
- `tests/test_packaging_smoke.py:243-244` — `test_wheel_declares_only_expected_console_scripts`
  asserts each named shim is NOT a console script.
- Installed workflows use the generic `ai-observe -- <cmd>` entry point.

Adding `agy` as an entry point would install a command shadowing the real
Antigravity CLI (`agy`) and require weakening the smoke-test invariant.

**My recommendation:** follow the established pattern —
1. `bin/agy` shim (mirror `bin/opencode`, `main_shim("agy")`)
2. register `agy` in `OBSERVER_SHIM_NAMES` (observe.py:35)
3. extend resolver + resilience tests to cover `agy`
4. extend the smoke test by ADDING `agy` to the shadow list (assert NOT an
   entry point), i.e. the opposite of adding an entry point.
5. do **NOT** add a pyproject entry point.

Sent to architect via `afx send`. Waiting for resolution before touching
pyproject / smoke-test entry-point assertions.

## RESOLVED (architect confirmed 2026-07-16)

Architect confirmed the interpretation: issue scope wording was wrong. NO
pyproject entry point for `agy`. Corrected issue #27 comment posted.

## Implementation complete

- `bin/agy` — new shim, mirrors `bin/opencode`, `main_shim("agy")`, +x.
- `observe.py:35` — `agy` added to `OBSERVER_SHIM_NAMES` (feeds the recursion
  guard `is_observer_shim`; env var `AI_OBSERVE_REAL_AGY` derives generically).
- `test_observe_resolver.py` — `agy` added to both named-program loops.
- `test_shim_resilience.py` — `agy` added to `SHIMS` (covers branch-detection +
  subprocess matrix).
- `test_packaging_smoke.py` — `agy` added to the shadow list (asserts NOT a
  console script) AND to the InstalledShimMatrix (two-path dispatch vs real wheel).
- NO pyproject change.

Full suite: 236 passed. Direct dispatch of `bin/agy` to a marker target verified.
Net diff well under 300 LOC.
