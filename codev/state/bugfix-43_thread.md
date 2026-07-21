# bugfix-43 thread — Flaky test_viewer_server tstate-lock race

Issue #43: `tests/test_viewer_server.py` harness `.join()` calls are raw/unwrapped,
so they can hit the CPython thread-teardown race (gh-89322 / bpo-45274) —
`Thread.join()` raising `AssertionError` from `_wait_for_tstate_lock`. Product code
is already hardened (`ViewerServer.stop()` → `_join_thread_safely`); the test harness
is not.

## Investigate (phase 1) — findings

- **Root cause confirmed.** Four test-harness joins use raw `.join()`:
  - `tests/test_viewer_server.py:247-248` — `t1`/`t2` concurrent SSE consumers
  - `tests/test_viewer_server.py:408` — `stopper`
  - `tests/test_viewer_server.py:561` — one-shot CLI thread
  - `tests/test_viewer_server.py:593` — one-shot CLI thread (no-browser)
- Production mitigation already exists: `src/ai_observe/viewer/server.py:_join_thread_safely`
  (helper ~L465, used at L449). Regression test for the helper already present:
  `test_join_thread_safely_tolerates_tstate_lock_race`.
- Test module already imports the helper: `from ai_observe.viewer import server as viewer_server` (L18).
- **Fix direction:** route the four harness joins through `viewer_server._join_thread_safely`.
- **Baseline:** full `test_viewer_server.py` suite green locally (22 tests OK, py3.14),
  consistent with an intermittent flake. Race is non-deterministic; mechanism (raw join
  propagates AssertionError; `_join_thread_safely` swallows it) is well understood and
  already unit-tested for the helper.
- **CI constraint (arch-critical):** CI fails loud on ANY `... skipped` / `skipped=N`.
  Regression test MUST NOT skip.
- **Scope:** test-only change, one file, ~4 join edits + 1 regression test. Well under
  300 LOC. BUGFIX-appropriate. No product code touched.

Next: signal PHASE_COMPLETE → fix phase.

## Fix (phase 2) — done

- Routed all 4 harness join sites (5 join calls) through
  `viewer_server._join_thread_safely(...)`: SSE consumers `t1`/`t2` (L247-248),
  `stopper` (L408), one-shot CLI threads (L561, L593). Minimal, test-only.
- Added deterministic regression guard
  `test_harness_thread_joins_route_through_race_tolerant_helper` (CLITests): static
  source scan asserts no raw thread joins (`.join(` with a timeout/positional arg)
  remain. Fails-without-fix (offenders at exactly [247,248,408,561,593] in pre-fix
  source), passes-with-fix, zero flakiness. Chosen over a probabilistic race repro
  because the phase demands a deterministic test and the helper's behavioral
  tolerance is already covered by `test_join_thread_safely_tolerates_tstate_lock_race`.
- Verified: full main suite 245 OK, no skips; porch's `unittest discover -s tests`
  266 OK. build check is skipped in this project's porch config.
- Net diff ~40 lines, one file. Well within BUGFIX scope.

Next: PHASE_COMPLETE → pr phase (push, open PR, CMAP 3-way, `pr` gate).

## PR (phase 3) — in progress

- PR **#44** opened: https://github.com/mohidmakhdoomi/ai-observe/pull/44 (Fixes #43).
- CMAP 3-way (gemini/codex/claude, `--type pr`) running. Note: consult needs
  `--project-id bugfix-43 --issue 43` here — bare auto-detect lists all
  `codev/projects/*` dirs and no-ops (many merged projects on main).
- CMAP verdicts (unanimous): **gemini=APPROVE, codex=APPROVE, claude=APPROVE**, all
  HIGH confidence, zero KEY_ISSUES. No REQUEST_CHANGES → no code changes needed.
- Architect notified. Requesting `pr` gate via `porch done`. **STOPPING for human
  approval** — do NOT self-merge; merge only after `porch approve bugfix-43 pr`.
