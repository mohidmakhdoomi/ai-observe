# Plan: CI + test-reliability + docs/release checklist for ai-observe

## Metadata
- **ID**: plan-2026-06-30-ci-test-reliability-docs-relea
- **Status**: draft
- **Specification**: [codev/specs/21-ci-test-reliability-docs-relea.md](../specs/21-ci-test-reliability-docs-relea.md)
- **Created**: 2026-06-30
- **Issue**: #21 · **Branch**: `builder/spir-21`

## Executive Summary

Implements SPIR B of 2 for `ai-observe` in three dependency-ordered phases, all shipping
as git commits within a **single PR**:

1. **Test reliability** — consolidate the two existing poll helpers into one shared
   `tests/_util.py`, convert timing-sensitive `time.sleep()` synchronizations to bounded
   poll-until-condition waits, and make permission/mode tests deterministic under any
   umask. This comes **first** because a deterministic suite is a precondition for a green,
   trustworthy CI gate.
2. **CI** — add a single GitHub Actions workflow (`.github/workflows/ci.yml`) on
   `ubuntu-latest` across Python `3.10`/`3.12`/`3.13`, installing `strace` + Node.js 20,
   relaxing ptrace, running the unittest suite, and building + clean-venv-installing +
   running the SPIR-A installed-artifact smoke tests so they execute (not skip). Any
   additional flakiness the matrix reveals is fixed here, reusing Phase 1's helper (per the
   issue's rationale for keeping reliability fixes in the CI-greening PR).
3. **Documentation + release checklist** — a prominent, security-forward root `README.md`;
   align `docs/observe.md` and `docs/viewer.md` with packaged usage; and a local
   (non-PyPI) `RELEASING.md`. Last, so the README can reference the now-known CI workflow.

The chosen approaches match the spec's selected approaches (single CI workflow + matrix;
shared poll helper + targeted umask determinism; README front-door + aligned docs +
`RELEASING.md`). No change to core observation semantics, packaging metadata, or the
smoke-test harness itself.

## Success Metrics

Copied from the spec's acceptance criteria (the binding list):

- [ ] CI runs on `ubuntu-latest` with Python `3.10`, `3.12`, `3.13` (matrix).
- [ ] CI installs `strace` and Node.js 20; loopback HTTP works.
- [ ] CI builds wheel + sdist, installs from artifacts into a clean venv, and **runs** (not
      skips) the SPIR-A installed-artifact smoke tests across the matrix.
- [ ] CI passes the unittest suite **and** the packaging smoke tests across the matrix.
- [ ] ptrace handled so `strace` tests run; container/`SYS_PTRACE` caveat documented.
- [ ] Timing-sensitive sleeps replaced with polling where practical; remaining fixed
      sleeps documented as justified.
- [ ] Umask-sensitive permission tests deterministic; product-set `0o600` assertions
      preserved.
- [ ] Root `README.md` with prominent sensitive-data warning + full artifact-contents
      warning + install/use/requirements/limitations + checkout-only shim workflow + #18
      caveat + loopback-only viewer + watched-roots/snapshot limits.
- [ ] `docs/observe.md` and `docs/viewer.md` aligned with packaged usage.
- [ ] Local release checklist exists covering all listed steps.
- [ ] All existing tests continue to pass (suite passes identically under `umask 077` and
      `umask 022`); #18 product promise unchanged.

> Note: the template's generic ">90% coverage / performance benchmark / load test" metrics
> are **N/A** for this change — it adds CI + docs + test-harness determinism, not product
> code. The binding metrics are the spec's acceptance criteria above.

## Phases (Machine Readable)

```json
{
  "phases": [
    {"id": "phase_1", "title": "Test reliability: shared poll helper + umask determinism"},
    {"id": "phase_2", "title": "CI: GitHub Actions matrix workflow (suite + build + installed-artifact smoke)"},
    {"id": "phase_3", "title": "Documentation: README + aligned docs + release checklist"}
  ]
}
```

## Phase Breakdown

### Phase 1: Test reliability — shared poll helper + umask determinism
**Dependencies**: None

#### Objectives
- Make the suite deterministic regardless of host timing and ambient umask, so CI (Phase 2)
  has a trustworthy green baseline.
- Remove guess-based fixed-sleep synchronization where a queryable condition exists;
  preserve and document intentional sleeps.

#### Deliverables
- [ ] New `tests/_util.py` exposing a single bounded poll helper (e.g.
      `poll_until(predicate, timeout=..., interval=...) -> bool`/raises on timeout with a
      clear message), consolidating the two existing helpers.
- [ ] `tests/test_live_trace.py` and `tests/test_viewer_tailer.py` updated to import the
      shared helper; their local `_wait_until` / `_wait_for` removed (or made thin
      aliases) — no third variant left behind.
- [ ] `tests/test_viewer_server.py` fixed-sleep synchronization sites (~lines 153, 187,
      221, 240, 313, 331, 344) converted to poll-until-condition where the awaited state is
      queryable (SSE/tailer catch-up); any genuinely un-pollable wait left as a documented
      fixed sleep. Note: the ~line-240 `time.sleep(0.3)` ("once both are likely subscribed")
      waits for two SSE clients to subscribe — there is no easy queryable predicate for
      "both subscribed", so this one is expected to **stay a documented fixed sleep**.
- [ ] `tests/test_viewer_smoke_e2e.py` `time.sleep(0.1)` (~line 59) included in the audit:
      convert to poll-until if the awaited state (server up / first event tailed) is
      queryable, else classify and document as an intentional fixed wait.
- [ ] `tests/test_codex_observe.py` directory-mode assertion made umask-independent: drop
      the exact `0o755` assertion on the **test-created** `obs` dir (or wrap that `mkdir`
      in an explicit `umask`), while **keeping** the product-set `0o600` assertions on
      `.trace`/`.jsonl`.
- [ ] Audit of all `umask`/`st_mode`/`chmod` assertions in `tests/` confirming none of the
      kept ones depend on ambient umask (record findings in the review).
- [ ] Intentional sleeps documented inline as intentional (fake-strace `time.sleep(30)`
      long-runner; product poll loops are not test code and are unchanged).

#### Implementation Details
- **Import mechanics:** there is no `tests/__init__.py`, so `tests/` is not a package;
  under `python -m unittest discover -s tests` the discovered modules import siblings as
  top-level modules (`from _util import poll_until`). Verify this import resolves under the
  exact invocation CI uses (Phase 2) before relying on it; if an editable install changes
  `sys.path` ordering, confirm `_util` still resolves (it should, since discover adds the
  start dir to `sys.path`).
- Shared helper lives in `tests/_util.py`; signature unifies the two existing helpers
  (`_wait_until` timeout≈2.0/interval≈0.02 in `test_live_trace.py`; `_wait_for`
  timeout≈3.0/interval≈0.02 in `test_viewer_tailer.py`). Use the **more generous** timeout
  as the default so slow CI doesn't flake; return as soon as the predicate holds.
- Conversion pattern: replace `time.sleep(X); assert cond` with
  `assert poll_until(lambda: cond, timeout=...)` so the test returns immediately once the
  condition holds and fails with a clear timeout otherwise.
- Umask fix bias (from spec): for **test-created** directories, stop asserting the exact
  umask-dependent mode; only assert modes the **product** sets. Use an explicit `umask`
  guard only where a test genuinely validates a product-set directory mode.
- Do **not** touch `tests/test_packaging_smoke.py` (already uses bounded retry + deadline
  reads) or product timing in `src/`.

#### Acceptance Criteria
- [ ] Full suite passes under `umask 022` **and** `umask 077` (run both locally).
- [ ] No new third poll-helper variant; the two old ones are consolidated.
- [ ] Converted tests pass and no longer depend on a fixed guess interval for correctness.
- [ ] Product-set `0o600` assertions still present and passing.

#### Test Plan
- **Unit/Integration**: the existing viewer/tailer/live-trace/codex tests are themselves
  the tests; verify they pass after conversion.
- **Manual**: `umask 077 && python -m unittest discover -s tests` then `umask 022 && ...`
  → identical pass result. Optionally run the converted tests in a loop to confirm
  stability.

#### Rollback Strategy
- Single atomic commit; `git revert` restores the prior test files. No product code
  touched, so revert is risk-free.

#### Risks
- **Risk**: a "sleep" that looks like sync is actually load-bearing (e.g. allowing a
  debounce window). **Mitigation**: convert conservatively; where the awaited state isn't
  queryable, keep a documented fixed sleep rather than forcing a poll.
- **Risk**: consolidating helpers changes a timeout that a test relied on. **Mitigation**:
  default to the larger existing timeout; keep per-call overrides.

---

### Phase 2: CI — GitHub Actions matrix workflow
**Dependencies**: Phase 1 (deterministic suite → trustworthy green CI)

#### Objectives
- Continuously validate the package, the suite, and the **installed artifact** across the
  Python matrix on every push and PR.
- Ensure the SPIR-A installed-artifact smoke tests actually **run** in CI (not skip).

#### Deliverables
- [ ] `.github/workflows/ci.yml`:
  - Triggers: `push` and `pull_request`.
  - Matrix: `python-version: ["3.10", "3.12", "3.13"]` on `ubuntu-latest`.
  - Steps: `actions/checkout`; `actions/setup-python` (matrixed);
    `actions/setup-node@v4` with `node-version: 20`;
    `sudo apt-get update && sudo apt-get install -y strace`;
    relax ptrace (`sudo sysctl kernel.yama.ptrace_scope=0`, guarded so it doesn't fail if
    the key is absent); provision build/test tooling (`python -m pip install --upgrade pip
    build`); make `ai_observe` importable for the suite (editable install `pip install -e .`
    or `PYTHONPATH=src` — pick the one matching local runs); run the unittest suite; build
    wheel + sdist; ensure the packaging smoke tests run and pass.
  - A clear, separate, named step (or explicit assertion) so a **silent smoke skip is not
    mistaken for success** (surface skip counts; bias toward failing on an unexpected smoke
    skip).
- [ ] A short comment block in the workflow documenting the container/`SYS_PTRACE` +
      seccomp caveat for any future containerized runners (per spec/issue).
- [ ] Any **additional** flakiness the matrix reveals fixed here using Phase 1's helper
      (kept in this PR per the issue's rationale).

#### Implementation Details
- **Run placement decision (from spec) — concrete mechanism:** use **(ii)**, two steps:
  - **Main suite step**, excluding the smoke module so it isn't double-built. Concrete
    options (pick one, document in-workflow): run discover but exclude smoke via
    `python -m unittest discover -s tests -p 'test_*.py'` combined with either (a) a
    dedicated pattern that omits `test_packaging_smoke.py` (rename-free approach:
    enumerate modules, or run `discover` then a second explicit run), or (b) the simplest
    robust form — `python -m unittest discover -s tests` minus smoke by passing an explicit
    module list, or setting an env var the smoke module reads to self-skip in the main run.
    Bias: keep it boring — run discover for everything **except** smoke by giving the main
    step an explicit exclusion, and run smoke alone in the next step.
  - **Smoke step**, explicit and visible: `python -m unittest tests.test_packaging_smoke`
    (or `python -m unittest -v tests/test_packaging_smoke.py` per the repo's import style).
  - **Fail-loud-on-skip mechanism (concrete):** capture the smoke step's result and fail
    the job if it reports `OK (skipped=N)` with N>0 for the *core* installed-artifact cases.
    Implementation options: (a) run with `-v` and `grep` the output for `... skipped` /
    `OK (skipped=` and `exit 1` if matched on the smoke step; or (b) set an env flag (e.g.
    `AI_OBSERVE_CI=1`) that the smoke harness's capability gate treats as "must run" so a
    would-be skip becomes a failure. Bias: (a) is zero-product-code; (b) is cleaner but
    edges toward changing the harness (SPIR-A territory) — default to (a) unless a tiny,
    clearly-SPIR-B-scoped env check is acceptable. Document the chosen mechanism.
- **Import shadowing guard:** the smoke harness already isolates itself in a clean venv
  outside the checkout; the workflow must **not** export `PYTHONPATH=src` into the smoke
  step's subprocesses. If the main suite uses `PYTHONPATH=src`, scope it to the main-suite
  step only; an editable install avoids the issue entirely.
- **Zero deps:** clean-venv `--no-index --no-deps` installs are safe (SPIR A confirmed zero
  runtime deps). Node parity tests use Node stdlib only (no root `package.json`) — confirm;
  no `npm install` step unless that turns out false.
- Pin action major versions; guard the sysctl call so a missing key logs rather than fails.

#### Acceptance Criteria
- [ ] Workflow present and triggers on push + PR.
- [ ] All three matrix legs green: unittest suite passes; Node-gated parity tests and
      `strace`-backed tests **execute** (visible in logs, not skipped).
- [ ] Build → clean-venv install → installed-artifact smoke tests run and pass on the
      matrix; no silent smoke skip counted as success.

#### Test Plan
- **Integration (the workflow itself)**: push the branch / open the PR and confirm all
  legs green; inspect logs to confirm parity + strace tests ran (not skipped) and the
  smoke step built/installed/ran.
- **Manual pre-push**: locally dry-run the key commands (apt/strace availability aside) —
  the unittest invocation, `python -m build`, and the smoke module — to catch syntax/path
  errors before relying on the runner.

#### Rollback Strategy
- Single atomic commit adding `.github/workflows/ci.yml`; deleting the file removes CI with
  zero impact on product or tests.

#### Risks
- **Risk**: `strace` can't attach on the runner image even after sysctl. **Mitigation**:
  guarded sysctl; if a specific test can't run, gate it with a clear capability skip rather
  than failing the whole leg — but never mask a real failure as a skip.
- **Risk**: smoke tests skip in CI (build tooling/provisioning). **Mitigation**: install
  `build`; surface skip counts; bias to fail on unexpected smoke skip.
- **Risk**: matrix reveals new flakiness late. **Mitigation**: fix in this phase with the
  Phase 1 helper; that's the intended design, not scope creep.

---

### Phase 3: Documentation + release checklist
**Dependencies**: Phase 2 (so README can reference the CI workflow/badge accurately)

#### Objectives
- Give a new user a security-forward front door (`README.md`) and keep existing docs
  aligned with packaged usage; provide a repeatable local release procedure.

#### Deliverables
- [ ] Root `README.md`:
  - **Very prominent sensitive-data warning near the top** (severe warning for `.trace`,
    `.jsonl`, `.jsonl.partial`, `.jsonl.rebuilt`, `.meta.json`).
  - What the tool does; install (`pip install .`); quick start with **packaged CLI**
    (`ai-observe --session demo -- <cmd>`, `ai-observe-viewer <jsonl>`,
    `python -m ai_observe.viewer <jsonl>`); artifact locations.
  - **Checkout-only opt-in named-shim workflow** (symlink/copy `bin/*` into a user dir +
    prepend `PATH`), explicitly noting shims are **not** installed by default.
  - Linux/`strace`/ptrace/container caveats; loopback-only viewer behavior; watched-roots +
    snapshot limitations; the **#18 limitation** (snapshot reconciliation inferred/post-hoc,
    doesn't perfectly capture files created/deleted between snapshots).
  - Full **security/privacy artifact-contents warning** (absolute paths; command argv &
    prompts on the command line; raw syscall text; file metadata; snapshot diagnostics &
    sidecar metadata) + "keep `.codev/observe/` out of commits, uploads, public logs until
    reviewed" + the off-Linux-install / Linux-only-runtime note.
  - Optional CI status badge if trivial/accurate.
- [ ] `docs/observe.md` and `docs/viewer.md` reconciled with packaged usage (e.g.
      `docs/viewer.md` currently shows `PYTHONPATH=src python3 -m ...`; show the installed
      `ai-observe-viewer` / `python -m ai_observe.viewer` path while preserving the
      checkout-shim instructions where they're the intended path).
- [ ] `RELEASING.md` — local (non-PyPI) checklist: version check/bump; full test run; CI
      status; wheel + sdist build; wheel/sdist content inspection; clean-venv install from
      built artifacts; one end-to-end observed command; viewer static-asset serving smoke
      test.

#### Implementation Details
- Reuse accurate content from `docs/observe.md` (already strong on sensitive-data + limits)
  rather than duplicating verbatim; the README summarizes and links into `docs/`.
- **Do not** repoint `pyproject.toml`'s `readme` (still `docs/observe.md`) — flag to the
  architect if it seems clearly right; default is leave it (stays in SPIR-B scope).
- Release checklist commands mirror the smoke harness / CI so the local procedure matches
  what CI does.

#### Acceptance Criteria
- [ ] README contains every required element from the spec's documentation constraint
      (checklist above), with the sensitive-data warning visually prominent near the top.
- [ ] `docs/observe.md` / `docs/viewer.md` show packaged usage; no stale checkout-only-only
      phrasing that contradicts the installed CLI.
- [ ] `RELEASING.md` covers all listed steps in order.

#### Test Plan
- **Manual**: follow the README from scratch in a clean checkout (install, run an observed
  command, open the viewer) and confirm the steps work and the warnings appear before any
  artifact is produced. Follow `RELEASING.md` end-to-end once (it overlaps the smoke path).
- **Link/consistency check**: verify intra-repo doc links resolve and CLI examples match
  actual entry points (`ai-observe`, `ai-observe-viewer`).

#### Rollback Strategy
- Docs-only commit; revert restores prior docs with zero functional impact.

#### Risks
- **Risk**: README drifts from `docs/` over time. **Mitigation**: README links into
  `docs/` rather than duplicating the canonical reference.
- **Risk**: under-emphasized sensitive-data warning. **Mitigation**: place it near the top,
  visually distinct; mirror `docs/observe.md`'s "Severe sensitive-data risk" framing.

## Dependency Map
```
Phase 1 (test reliability) ──→ Phase 2 (CI) ──→ Phase 3 (docs + release)
```
All three land as separate commits on `builder/spir-21` in one PR.

## Resource Requirements
- **Environment**: GitHub Actions `ubuntu-latest` runners; local Linux + `strace` + Node
  for pre-push verification. No new services, databases, or infra.

## Integration Points
- **GitHub Actions** — CI runs against the repo on push/PR. Fallback: none needed; absence
  of CI just means no gate (the prior state).
- **SPIR-A smoke harness** (`tests/test_packaging_smoke.py`) — Phase 2 ensures it runs in
  CI; no code change to the harness.

## Risk Analysis
### Technical Risks
| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| `strace` can't attach on runner even after sysctl | M | M | Guarded sysctl; clear capability skip per-test, never mask real failures |
| Smoke tests silently skip in CI | M | H | Install `build`; surface skip counts; fail on unexpected smoke skip |
| Import shadowing (`src/` vs install) in CI | L | M | Keep smoke harness clean-venv isolation; scope any `PYTHONPATH=src` to main-suite step / use editable install |
| Over-aggressive sleep→poll conversion breaks a load-bearing wait | L | M | Convert conservatively; keep documented fixed sleep where state isn't queryable |
| Matrix reveals new flakiness late | M | M | Fix in Phase 2 with Phase 1 helper (intended design) |

### Schedule Risks
- N/A — no time estimates (AI-driven); progress measured by completed phases.

## Validation Checkpoints
1. **After Phase 1**: suite passes under both `umask 022` and `umask 077`; helpers
   consolidated.
2. **After Phase 2**: all matrix legs green; parity + strace tests run (not skipped); smoke
   runs against the built artifact.
3. **After Phase 3**: README/docs/RELEASING complete and accurate; new-user walkthrough
   works; warnings prominent.

## Monitoring and Observability
- N/A for product runtime. The relevant "monitoring" is the CI status itself (green/red on
  push/PR) and visible skip counts in CI logs.

## Documentation Updates Required
- [ ] Root `README.md` (new).
- [ ] `docs/observe.md`, `docs/viewer.md` (align with packaged usage).
- [ ] `RELEASING.md` (new).
- [ ] Architecture/lessons docs updated in the Review phase (per protocol), not here.

## Post-Implementation Tasks
- [ ] Confirm CI green on the PR across all three matrix legs.
- [ ] (Verify phase) follow `RELEASING.md` once end-to-end on the merged integration
      branch.

## Expert Review
**Date**: 2026-06-30
**Models**: Gemini, Codex, Claude (porch-run 3-way; see Consultation Log in the spec)
**Key Feedback (carried from spec review into this plan)**:
- Make CI artifact validation unambiguous (smoke harness is the validator; CI must make it
  run, not skip) → encoded in Phase 2 deliverables + run-placement decision.
- Avoid import shadowing → Phase 2 implementation detail.
- Consolidate the two existing poll helpers, not invent a third → Phase 1 deliverable.

**Plan Adjustments** (after this plan's 3-way review — Codex COMMENT/HIGH, Claude
APPROVE/HIGH, Gemini lane skipped/agy timeout):
- **Codex #1** — added `tests/test_viewer_smoke_e2e.py` (`time.sleep(0.1)`) to Phase 1's
  audit/conversion scope, and flagged the `test_viewer_server.py` ~line-240 two-SSE-client
  sleep as an expected documented fixed sleep.
- **Codex #2** — Phase 2 now specifies the concrete two-step run mechanism (main suite
  excluding smoke + a distinct smoke step) and a concrete fail-loud-on-skip mechanism
  (grep the smoke step output for skips and `exit 1`, vs. an env flag), with a bias toward
  the zero-product-code grep approach.
- **Claude** — added `tests/_util.py` import-mechanics note (no `tests/__init__.py`;
  resolves under `discover`; verify under the CI invocation).

## Approval
- [ ] Expert AI Consultation Complete (porch-run)
- [ ] Human plan-approval gate

## Change Log
| Date | Change | Reason | Author |
|------|--------|--------|--------|
| 2026-06-30 | Initial implementation plan | Plan phase | builder spir-21 |

## Notes
- Phases ship as git commits within **one PR** (opened during/after Phase 3 unless the
  architect requests earlier). No `git add -A`/`git add .` — stage files explicitly.
- No change to core observation semantics, packaging metadata, the shim refactor, or the
  smoke-test harness (those are SPIR A). #18 remains separate.
