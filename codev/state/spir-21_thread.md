# spir-21 thread — CI + test-reliability + docs/release checklist

## Context
SPIR B of 2. SPIR A (#20, packaging) merged via PR #22 (commit 181c242). The smoke
tests (`tests/test_packaging_smoke.py`) and pyproject.toml exist. My job: GitHub Actions
CI across a Python matrix, deterministic tests, user-facing docs + release checklist.
No change to core observation semantics.

## Phase log

### Specify (in progress)
- Confirmed: no spec existed for 21 yet; writing it from the issue body.
- Codebase recon done:
  - No `.github/` yet → CI workflow is greenfield.
  - No root `README.md` yet → docs workstream creates it. `docs/observe.md` +
    `docs/viewer.md` exist and are good.
  - JS parity tests gate on `shutil.which("node")` and skip when absent
    (test_viewer_table_js/index_js/treemap/aggregator). CI needs Node 20 so they run.
  - Viewer tests already bind `port=0` (ephemeral) — good; smoke e2e to confirm.
  - Flaky candidates:
    - `tests/test_codex_observe.py:67` asserts `obs.stat().st_mode == 0o755` on a dir
      created by the test's own `obs.mkdir()` → umask-dependent. Prime umask fix target.
    - Many `time.sleep()` synchronization points in viewer/tailer/live-trace tests.
    - `tests/test_codex_observe.py:116` `time.sleep(30)` is a fake-strace long-runner
      for signal-forwarding tests — intentional, terminated by signal; document, don't poll.
  - strace tests need ptrace; ubuntu-latest may need `kernel.yama.ptrace_scope=0`.
- Spec drafted + committed. 3-way consult: Gemini APPROVE, Claude APPROVE, Codex COMMENT
  (all HIGH, no blockers). Folded in: CI artifact-validation clarity (smoke harness is the
  validator; CI must make it run not skip), import-shadowing pitfall, zero-deps fact,
  consolidate the two existing poll helpers (`_wait_until`/`_wait_for`), 5th node-gated
  file (breadcrumb). Committed "Specification with multi-agent review".
- **GATE: spec-approval reached — STOPPED, waiting for human/architect approval.**
- spec-approval APPROVED by architect.

### Plan (in progress)
- Plan drafted: 3 dependency-ordered phases in ONE PR —
  1. Test reliability (shared `tests/_util.py` poll helper, sleep→poll conversions, umask
     determinism in test_codex_observe.py).
  2. CI (`.github/workflows/ci.yml`: ubuntu-latest × py3.10/3.12/3.13, strace + Node 20,
     ptrace sysctl, suite + build + clean-venv install + installed-artifact smoke).
  3. Docs (root README, align docs/observe.md + docs/viewer.md, RELEASING.md).
- 3-way consult: Codex COMMENT (HIGH), Claude APPROVE (HIGH), Gemini skipped (agy timeout).
  Folded in: added test_viewer_smoke_e2e.py to Phase 1 scope; concrete CI run-placement +
  fail-loud-on-skip mechanism; _util.py import-mechanics note; line-240 stays fixed sleep.
  Committed "Plan with multi-agent review".
- **GATE: plan-approval reached — STOPPED, waiting for human/architect approval.**
- Architect asked to retry the Gemini plan consult for 3-way parity. Re-ran
  `consult -m gemini --protocol spir --type plan` — **agy timed out again** (no response;
  consult exited 0, artifact = skip message). No substantive Gemini feedback exists to fold
  in. Plan review remains effectively 2-way (Codex COMMENT, Claude APPROVE), both folded.
  Reported back to architect; staying parked at the gate.
- Session resumed (2026-07-13). Porch confirms still parked at plan-approval gate;
  architect already notified. No action taken — waiting for approval.
- Architect asked to re-run the Gemini plan consult (3rd attempt). Ran
  `consult -m gemini --protocol spir --type plan --issue 21` (first try without `--issue`
  exited 1: multi-project auto-detect ambiguity). This time agy responded in 63.3s:
  **VERDICT: APPROVE, CONFIDENCE: HIGH, KEY_ISSUES: None** — no substantive feedback to
  fold in. Artifact written to
  codev/projects/21-ci-test-reliability-docs-relea/21-plan-iter1-gemini.txt.
  Plan review is now a full 3-way: Codex COMMENT (folded), Claude APPROVE, Gemini APPROVE.
  Still parked at plan-approval gate.
- Architect requested delete + verbatim re-run: removed the artifact, then ran
  `consult -m gemini --protocol spir --type plan --project-id 21 --output "codev/projects/21-ci-test-reliability-docs-relea/21-plan-iter1-gemini.txt"`.
  Exit 0, agy completed in 52.3s. VERDICT: APPROVE, CONFIDENCE: HIGH, KEY_ISSUES: None.
  Artifact rewritten in place (1578 bytes) and recommitted. Still parked at plan-approval.

### Implement — phase_1: test reliability (build complete)
- plan-approval gate APPROVED by human in-session; porch advanced to implement/phase_1.
- Added `tests/_util.py` with `poll_until(predicate, timeout=3.0, interval=0.02)`
  (consolidates `_wait_until`/`_wait_for`; final re-check at deadline; imported via the
  `_aggregator_oracle` precedent: `sys.path.insert(0, ROOT/"tests")`).
- Converted sleep→poll: viewer_server 153/187/221 (tailer catch-up via
  `broadcaster_for(...).snapshot_len()`), 313 (both rebuilt+partial artifact tailers),
  smoke_e2e setUp (first event tailed), tailer truncation test (poll the "truncated"
  warning), tailer shutdown-fragment test (poll `tailer._buf`).
- Documented intentional fixed sleeps: viewer_server 240 (two-SSE-subscribers not
  queryable), 331 + 344 (initial empty-file scan, negative check); tailer negative checks
  (initial empty, incomplete line); live_trace negative checks (3 sites); codex_observe
  fake-strace `time.sleep(30)` long-runner + 0.3s signal-test startup grace.
- Umask: dropped the test-created-dir `0o755` assertion in test_codex_observe (product
  only chmods dirs it creates, 0o700); kept all product-set `0o600` assertions.
  Audit: remaining chmod(0o755) uses are explicit sets on fake exes (umask-independent);
  only mode assertions left are product-set 0o600 (codex_observe 2 sites, live_trace
  rebuilt/meta) — none ambient-umask-dependent.
- Verified: full suite 234/234 OK under umask 022 AND umask 077; 3x stability loop on
  viewer_server OK; direct-file invocation of all touched test modules OK.

### Implement — phase_1 approved; phase_2 CI (build complete)
- phase_1 iter2 review: unanimous APPROVE (Gemini HIGH, Codex MEDIUM, Claude HIGH). Iter1
  had flagged only the untracked tests/_util.py — staged + rebutted; porch swept it into
  its re-iter commit. NOTE: porch's phase-advance commits only status.yaml — the builder
  must commit implementation files itself; phase_1 test changes committed as 71392c6.
- phase_2: added .github/workflows/ci.yml — push+PR triggers; ubuntu-latest ×
  py 3.10/3.12/3.13 (fail-fast off); checkout@v4 / setup-python@v5 / setup-node@v4
  (Node 20, no npm install — parity tests are Node-stdlib-only); apt strace; guarded
  `sysctl kernel.yama.ptrace_scope=0`; `pip install build`; main suite from tests/ cwd via
  explicit module list excluding test_packaging_smoke (keeps _util.py resolvable, no
  PYTHONPATH leak into smoke's clean venv); `python -m build`; smoke as separate -v step
  with fail-loud-on-skip (grep "skipped" → exit 1, plan mechanism (a), zero product-code).
  Container/SYS_PTRACE+seccomp caveat documented in the header comment.
- Local dry-runs: YAML parses; main-suite invocation 213/213 OK; smoke module 21/21 OK
  with zero skips; `python -m build` in a scratch venv builds wheel+sdist (stray egg-info
  removed; *.egg-info/ already gitignored). Real matrix validation happens on push.

### Implement — phase_2 iter1 rebuttal (resumed session)
- iter1 verdicts: Gemini APPROVE, Claude APPROVE, Codex REQUEST_CHANGES (2 issues).
- Codex fix 1: "Provision build tooling" now installs `pip build "setuptools>=77"` —
  pyproject requires setuptools>=77 (PEP 639) and the smoke harness builds via the HOST
  interpreter's setuptools.build_meta with no isolation, so `build` alone was insufficient.
- Codex fix 2: main-suite step now has the same fail-loud-on-skip check as smoke (tee to
  $RUNNER_TEMP, grep, ::error:: + exit 1) — strace tests self-skip on ptrace denial and
  would otherwise silently drop coverage on a green leg.
- Bug found while verifying: bare `grep -E "skipped"` false-positives on test NAMES
  (test_malformed_line_skipped_with_warning, test_unsupported_future_schema_version_skipped
  in test_viewer_tailer) — every CI leg would have failed with zero real skips. Both greps
  now anchor on unittest's own markers: `\.\.\. skipped|skipped=[0-9]` (per-test/module
  "... skipped 'reason'" lines + "OK (skipped=N)" summary).
- Verified locally: YAML parses; main suite as-CI-runs-it 213/213 OK, anchored grep clean;
  smoke as-CI-runs-it 21/21 OK, zero skips; anchored grep positively matches both per-test
  and setUpModule skip fixtures. Rebuttal written; porch done.

### Implement — phase_2 approved; phase_3 docs (starting)
- phase_2 iter2: unanimous APPROVE (all HIGH). Codex confirmed both iter1 gaps covered.
- ci.yml landed in porch's re-iter commit 258ac02 (same sweep pattern as phase_1).
- phase_3 scope: README.md (security-forward front door), docs/observe.md +
  docs/viewer.md aligned with packaged usage, RELEASING.md local checklist.

### Implement — phase_3 docs (build complete)
- README.md (new): CI badge; severe sensitive-data warning as prominent blockquote near
  top (all 5 artifact extensions + full contents warning: absolute paths, argv/prompts,
  raw syscall text, file metadata, snapshot diagnostics/sidecar); what-it-does with
  provenance framing; requirements (Linux+strace runtime vs install-anywhere); pip
  install .; quick start with packaged CLI (ai-observe / ai-observe-viewer /
  python -m ai_observe.viewer); artifact locations; checkout-only opt-in named-shim
  workflow (symlink into user dir + PATH prepend, explicitly NOT installed by default);
  ptrace/Yama/container (SYS_PTRACE+seccomp) caveats; loopback-only viewer; watched-roots
  + snapshot limitations incl. #18; links into docs/ instead of duplicating.
- RELEASING.md (new): 8 ordered steps — version check/bump (single-source __init__),
  full test run (zero-skip), CI green on release commit, python -m build, wheel/sdist
  content inspection, clean-venv --no-index install outside checkout (+ smoke module for
  sdist path), e2e observed command from temp dir, viewer static-asset serving smoke.
- docs/viewer.md: invocation + practical workflow now packaged-first (ai-observe-viewer /
  python -m), checkout PYTHONPATH=src form preserved as secondary.
- docs/observe.md: quick start restructured — installed CLI first (ai-observe examples,
  was bin/ai-observe), named shims reframed as checkout-only opt-in with symlink/copy
  guidance + absolute-path recursion warning. pyproject readme NOT repointed (per plan).
- Verified: link/anchor/CLI-name consistency script passes; e2e walkthrough (bin/ai-observe
  observed command → demo.{jsonl,meta.json,trace}, generated.txt in events); viewer
  --no-browser on 127.0.0.1:7878 serves index + static assets (200s).

### Implement — phase_3 iter1 rebuttal
- iter1 verdicts: Gemini APPROVE, Claude APPROVE, Codex REQUEST_CHANGES (2 doc issues,
  both legit). Fix 1: docs/observe.md severe-risk section now carries the spec-required
  "keep .codev/observe/ out of commits/uploads/public logs until reviewed" (doubly
  important: that file is the pyproject readme). Fix 2: RELEASING.md provisions
  build+setuptools>=77 in the intro (step 2's zero-skip smoke run needs the PEP 517
  backend), step 4 now just runs python -m build. Rebuttal written; porch done.

### Review phase — phase_3 approved; review doc + governance updates + PR
- phase_3 iter2: unanimous APPROVE (all HIGH). All plan phases complete → review phase.
- Review doc written (codev/reviews/21-...) with full consultation feedback across
  specify/plan/3 implement phases, deviations (grep anchoring, setuptools provisioning),
  arch + lessons routing.
- Governance: arch-critical.md gets its first real HOT fact (CI fails loud on any skip)
  + populated arch.md map (9 topics); arch.md gains "Continuous integration" section;
  lessons-learned.md gains 2 cold lessons (anchored grep gates; stage-new-files/commit
  sweep). lessons-critical map left starter — flat 15-heading cold doc would bust the
  12-topic cap; flagged for MAINTAIN.
- Consultation artifacts committed (precedent: projects 1/11/15/20 tracked on main).
- Next: push branch, open PR (Closes #21), porch done --pr.
