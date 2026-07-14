# Review: 21 — CI + test reliability + docs/release checklist

## Summary

Made `ai-observe` continuously validated and documented, without touching core
observation semantics:

- **Phase 1 (test reliability)**: consolidated the suite's two ad-hoc polling
  helpers into `tests/_util.py` (`poll_until`), converted timing-sensitive
  `time.sleep()` synchronization to polling across `test_live_trace`,
  `test_viewer_tailer`, `test_viewer_server`, and `test_viewer_smoke_e2e`,
  documented every remaining fixed sleep as an intentional negative-check /
  unqueryable-state wait, and removed the one umask-dependent mode assertion
  (test-created dir `0o755`) while keeping all product-set `0o600` assertions.
  Suite verified green under both `umask 022` and `umask 077`.
- **Phase 2 (CI)**: added `.github/workflows/ci.yml` — push + PR triggers,
  `ubuntu-latest` × Python 3.10/3.12/3.13 (`fail-fast: false`), Node 20,
  `strace` via apt, guarded `kernel.yama.ptrace_scope=0`, explicit
  `build` + `setuptools>=77` provisioning, main unittest suite (excluding the
  packaging-smoke module) with a fail-loud-on-skip gate, `python -m build`,
  and the SPIR-A installed-artifact smoke tests as a separate fail-loud step.
  Container `SYS_PTRACE`/seccomp caveats documented in the workflow header.
- **Phase 3 (docs + release)**: new root `README.md` (security-forward, with
  the severe sensitive-data warning prominent near the top), `docs/observe.md`
  and `docs/viewer.md` aligned to packaged-first usage, and `RELEASING.md`
  with the 8-step local (non-PyPI) release checklist.

## Spec Compliance

- [x] CI on standard `ubuntu-latest` VM runners, Python 3.10/3.12/3.13.
- [x] CI installs `strace` and Node.js 20; loopback HTTP works (VM runners,
      ephemeral ports).
- [x] CI builds wheel + sdist; smoke harness installs from built artifacts in
      a clean venv and exercises the installed artifact (SPIR-A harness,
      unchanged).
- [x] Unittest suite + packaging smoke pass across the matrix; skips fail the
      job loudly (anchored on unittest's `... skipped` / `skipped=N` markers).
- [x] Timing-sensitive sleeps replaced with `poll_until` where practical;
      remaining fixed sleeps documented as intentional (negative checks,
      unqueryable states, fake-strace long-runner).
- [x] Umask-sensitive assertions removed; only product-set `0o600` modes are
      asserted (umask-independent).
- [x] README covers: severe sensitive-data warning (all five artifact types +
      contents: absolute paths, argv/prompts, raw syscall text, file metadata,
      snapshot diagnostics/sidecar); install; quick start with packaged CLI;
      artifact locations; checkout-only opt-in named-shim workflow (not
      installed by default); Linux/strace/ptrace/container caveats;
      loopback-only viewer; watched-roots + snapshot limitations; the #18
      limitation; keep-`.codev/observe/`-out-of-commits guidance;
      off-Linux-install / Linux-only-runtime note.
- [x] `docs/observe.md` / `docs/viewer.md` aligned with packaged usage
      (installed CLI first; checkout paths preserved as secondary).
- [x] `RELEASING.md` covers, in order: version check/bump, full test run, CI
      status, wheel + sdist build, content inspection, clean-venv install from
      built artifacts, one end-to-end observed command, viewer static-asset
      serving smoke test.
- [x] No change to core observation semantics, packaging metadata, the shim
      refactor, or the smoke harness (SPIR A); #18 unimplemented (separate).

## Deviations from Plan

- **Phase 2**: the plan's "mechanism (a)" (grep verbose unittest output for
  skips) needed hardening in two ways discovered during review iterations:
  (1) fail-loud-on-skip was extended from the smoke step to the **main suite
  step** too (Codex: strace tests self-skip on ptrace denial and would
  otherwise pass a leg silently); (2) the grep is anchored to unittest's own
  markers (`\.\.\. skipped|skipped=[0-9]`) because two test **names** in
  `test_viewer_tailer.py` contain the word "skipped" and the naive bare-word
  grep failed every leg with zero real skips.
- **Phase 2**: `setuptools>=77` is provisioned explicitly alongside `build`
  (pyproject requires it for PEP 639, and the smoke harness invokes the PEP
  517 backend against the host interpreter without isolation).
- **Review phase (product code, architect-directed)**: the plan scoped
  reliability fixes to test-side changes, but the CI matrix revealed the
  flakiness in the viewer *shutdown* path (a CPython `join()` race — see
  **Flaky Tests**). Fixing it required a minimal, targeted product change
  (`_join_thread_safely` in `viewer/server.py`); viewer shutdown is not
  observation semantics, so this stays within the spec's "no change to core
  observation semantics" constraint. No broader refactor was undertaken.
- No other deviations; `pyproject.toml`'s `readme` was left pointing at
  `docs/observe.md` per the plan's default.

## Lessons Learned

### What Went Well

- The plan's decision to keep flakiness fixes in the same PR as CI proved
  right: the phase-1 `poll_until` consolidation made every later phase's local
  dry-run stable and fast (234 tests in ~27s).
- The two-step CI design (main suite vs. smoke) kept the capability-gated
  smoke harness's skips impossible to confuse with success, exactly as the
  spec demanded.
- Local dry-runs of the exact CI step commands (module-list expansion, grep
  gate, `python -m build`, smoke module) caught every workflow bug before any
  push — including one that reviews missed (see below).

### Challenges Encountered

- **Grep false positive**: the naive fail-on-skip grep matched test *names*
  (`test_malformed_line_skipped_with_warning`). Resolved by running the real
  suite output through the gate locally and anchoring the regex to unittest's
  emitted markers, then verifying both per-test and `setUpModule` skip forms
  against generated fixtures.
- **Untracked new file**: `tests/_util.py` was created but not staged in
  phase 1 iteration 1; two of three reviewers caught that the canonical diff
  would break imports. Resolved by staging; porch's re-iter commit swept it in.
- **Session interruption**: the builder session was interrupted mid-cycle
  (after fixing phase_2 feedback, before the rebuttal). The thread log plus
  file mtimes made resuming unambiguous.

### What Would Be Done Differently

- Run new CI gate logic against *real* captured output as part of writing it,
  not after: the grep bug was invisible to all three reviewers reading the
  diff and only surfaced by executing the gate against the actual suite log.
- Stage new files the moment they are created (the `_util.py` miss cost an
  iteration).

### Methodology Improvements

- porch's phase-advance/build-complete commits only sweep **staged** files
  (and sometimes only `status.yaml`) — a builder habit of `git add`-ing
  deliverables immediately after creation is what makes the sweep reliable.
  Recorded in the thread log and in lessons-learned.md.

## Technical Debt

- CI matrix validation is local-dry-run-verified but not yet observed green on
  GitHub (no push has occurred from this worktree); the first PR push is the
  real integration test, and the verify phase should confirm all three legs.
- The `... skipped` grep gate depends on unittest's verbose output format;
  a future unittest format change would need the regex revisited (low risk,
  stdlib format is stable).

## Consultation Feedback

### Specify Phase (Round 1)

#### Gemini
- No concerns raised (APPROVE).

#### Codex (COMMENT)
- **Concern**: ambiguity over whether CI must validate the workflow-built
  artifacts or whether the smoke harness (which builds its own) is the
  validator; risk of a capability-gated skip being mistaken for success under
  plain `discover`.
  - **Addressed**: spec/plan encode the smoke harness as the installed-
    artifact validator, run as a distinct CI step with a fail-loud-on-skip
    gate; the main suite excludes the smoke module to avoid double-building.

#### Claude
- No concerns raised (APPROVE).

### Plan Phase (Round 1)

#### Gemini
- No concerns raised (APPROVE).

#### Codex (COMMENT)
- **Concern**: `tests/test_viewer_smoke_e2e.py` missing from phase-1 audit
  scope; phase 2 lacked the concrete exclusion/fail-loud mechanism.
  - **Addressed**: both folded into the plan ("Plan Adjustments" section) —
    smoke_e2e added to phase 1, and phase 2 got the concrete two-step run +
    grep mechanism.

#### Claude
- **Comment**: note `tests/_util.py` import mechanics (no `tests/__init__.py`;
  resolves under `discover`) and verify under the CI invocation.
  - **Addressed**: plan note added; CI runs from `tests/` cwd so sibling
    imports resolve identically to `discover`.

### Implement — phase_1 (Round 1)

#### Gemini (REQUEST_CHANGES) / Codex (REQUEST_CHANGES)
- **Concern** (both): `tests/_util.py` untracked — the committed diff would
  break the four test modules importing it.
  - **Addressed**: staged the file; rebutted with the staged state; porch's
    re-iter commit included it.

#### Claude
- No concerns raised (APPROVE).

### Implement — phase_1 (Round 2)

Unanimous APPROVE (Gemini HIGH, Codex MEDIUM, Claude HIGH).

### Implement — phase_2 (Round 1)

#### Codex (REQUEST_CHANGES)
- **Concern 1**: CI installed `build` but not `setuptools>=77`, which the
  pyproject backend requires and the smoke harness invokes against the host
  interpreter.
  - **Addressed**: "Provision build tooling" step installs
    `pip build "setuptools>=77"` with an explanatory comment.
- **Concern 2**: the main suite had no fail-loud check, so strace tests
  self-skipping on ptrace denial could silently pass a leg.
  - **Addressed**: main-suite step got the same tee + grep + `::error::` gate
    as the smoke step.
- **Builder-found follow-on**: the bare-word grep false-positived on test
  names containing "skipped"; anchored to `\.\.\. skipped|skipped=[0-9]` and
  verified against real and fixture outputs (documented in the rebuttal).

#### Gemini / Claude
- No concerns raised (APPROVE).

### Implement — phase_2 (Round 2)

Unanimous APPROVE (all HIGH).

### Implement — phase_3 (Round 1)

#### Codex (REQUEST_CHANGES)
- **Concern 1**: `docs/observe.md` lacked the spec-required "keep
  `.codev/observe/` out of commits, uploads, and public logs until reviewed"
  recommendation (README had it; the security doc did not).
  - **Addressed**: added to the "Severe sensitive-data risk" section — doubly
    important since that file is the pyproject readme.
- **Concern 2**: `RELEASING.md` demanded a zero-skip test run in step 2 but
  only provisioned `build`/`setuptools>=77` in step 4.
  - **Addressed**: provisioning moved to the intro, before step 1; step 4 now
    only runs `python -m build`.

#### Gemini / Claude
- No concerns raised (APPROVE).

### Implement — phase_3 (Round 2)

Unanimous APPROVE (all HIGH).

## Architecture Updates

Routed per the hot/cold two-tier model:

- **HOT (`codev/resources/arch-critical.md`)**: added one fact — CI fails
  loudly on *any* unittest skip, so capability-gated skips are a local-dev
  affordance only; a new test whose capability CI doesn't provision will turn
  the matrix red. This is behavior-changing and cross-cutting: it changes how
  every future test with an environment gate must be written.
- **COLD (`codev/resources/arch.md`)**: added a "Continuous integration"
  top-level section documenting the workflow shape (matrix, provisioning,
  two-step main-suite/smoke split, anchored skip-grep mechanism, ptrace/
  container notes) and updated the hot file's cold-doc map accordingly.

## Lessons Learned Updates

- **COLD (`codev/resources/lessons-learned.md`)**: two additions —
  1. *Anchor log-grep gates to tool-emitted markers, not bare words*, and run
     the gate against real captured output before shipping it (the "skipped"
     test-name false positive).
  2. *Stage new files immediately and know your orchestrator's commit sweep*:
     porch commits only staged/known files; an untracked deliverable produces
     a broken canonical diff even when local runs pass.
- **HOT (`codev/resources/lessons-critical.md`)**: no additions — both lessons
  are narrower than the always-on bar; the existing seeded lessons already
  cover the closest cross-cutting habit ("tests pass" ≠ "it works"). Its
  cold-doc map is left as starter: `lessons-learned.md` is a flat list of
  15 per-lesson headings, so an accurate top-level map would bust the
  ≤12-topic cap — flagged for MAINTAIN to restructure the cold doc into
  topical groups first.

## Flaky Tests

### Matrix-revealed: `test_default_port_collision_falls_back_to_ephemeral` (CPython join race)

The first real CI runs surfaced exactly the class of flakiness the plan
predicted the matrix would expose — but in **product** shutdown code, not a
test-side sleep/umask assertion.

- **Failed run**: GitHub Actions run `29309258243`, `test (3.12)` leg, job
  `87009317453`. A sibling run on the *same* PR-opening commit passed all
  legs — intermittent, ~1 failure in 12 legs observed.
- **Test**: `tests/test_viewer_server.py::CLITests.test_default_port_collision_falls_back_to_ephemeral`
  (one-shot CLI: start viewer, immediately stop).
- **Traceback (summary)**:
  `cli.main()` → `viewer/__main__.py:84 server.stop()` →
  `viewer/server.py:433 self._serve_thread.join(timeout=5.0)` →
  CPython `threading._wait_for_tstate_lock` → `AssertionError: assert self._is_stopped`.
- **Root cause**: a known CPython thread-teardown race (gh-89322 / bpo-45274).
  When a thread is joined at the exact moment it clears its C-level
  `_tstate_lock`, `_wait_for_tstate_lock` can observe `lock is None` while
  `_is_stopped` is not yet visibly `True` and trips its internal assertion.
  The one-shot CLI path (start → immediate `stop()` → `join()`) hits this
  window because the serve thread is torn down microseconds after creation.
  It is **not** an observation-semantics defect — the thread has, in fact,
  finished when the assertion fires.
- **Fix (product code, minimal/targeted)**: added
  `viewer/server._join_thread_safely()` and call it from `ViewerServer.stop()`
  in place of the bare `join()`. The helper tolerates the benign
  `AssertionError` from `join()`/`is_alive()`, lets the thread's Python-side
  state converge with brief bounded retries, and returns once the (daemon)
  thread is no longer alive or the timeout elapses. No change to shutdown
  ordering, HTTP handling, or observation behavior.
- **Regression coverage**: two deterministic tests in `test_viewer_server.py`
  (`test_join_thread_safely_tolerates_tstate_lock_race` forces `join()` to
  raise the assertion and asserts it is swallowed;
  `test_join_thread_safely_joins_live_thread_cleanly` covers the normal path).
- **Confidence**: the affected test passed 150/150 in separate processes and
  200/200 in a single interpreter (in-process, the configuration that most
  readily reproduces an in-interpreter teardown race); full suite green.

Beyond this one race, no flaky tests were encountered. No tests were skipped;
the phase-1 work removed the known timing/umask flakiness sources, and the
suite ran green on every check during all three phases, under both `umask 022`
and `077`.

## Follow-up Items

- Confirm all three CI matrix legs green on the PR after the `join()`-race
  fix, across several runs (the race was ~1-in-12 legs, so a single green
  sweep is necessary but not fully sufficient) — part of the verify phase.
- Follow `RELEASING.md` end-to-end once on the merged integration branch
  (verify phase, per the plan's post-implementation tasks).
- #18 (periodic snapshot reconciliation) remains open and out of scope.
