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
