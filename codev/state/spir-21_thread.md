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
