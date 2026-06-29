# spir-20 thread — Packaging: make ai-observe installable

Builder: spir-20 | Protocol: SPIR (strict) | Issue #20

## Log

### Specify phase — start
- Project 20, strict mode. Spec file did not exist; writing it from issue #20 (which is
  highly detailed and carries a "License" + "Baked Decisions"-style decisions block).
- Codebase recon:
  - `src/` layout, package `ai_observe`, version in `src/ai_observe/__init__.py` = `0.1.0`.
  - Entry points: `ai_observe.observe:main_generic`, `ai_observe.viewer.__main__:main`.
  - `bin/*` shims (ai-observe, claude, codex, gemini, opencode) all do
    `sys.path.insert(0, ROOT/"src")` then import — checkout-only today.
  - Viewer serves static via `_STATIC_DIR = Path(__file__).resolve().parent / "static"`
    + `path.read_bytes()`. Filesystem-based read → works for wheel installs **iff**
    static files are declared as package data and installed unpacked. This is the
    classic src-layout footgun the hard acceptance criterion guards.
  - Static files: index.html, index.js, aggregator.js, table.js, treemap.js, style.css.
  - Linux/strace gating already exists in `backends/strace.py` (`requires_linux`,
    raises "Linux required for strace backend" / "strace not found").
  - Sensitive-data warning printed unless `AI_OBSERVE_QUIET` env flag set.
- No existing pyproject/setup/LICENSE/NOTICE/MANIFEST.
- Next: draft spec, commit, run `porch done 20` to trigger 3-way review.

### Specify phase — 3-way review done → GATE
- Verdicts: Gemini APPROVE (HIGH), Claude APPROVE (HIGH), Codex COMMENT (HIGH).
- Incorporated all actionable feedback (clarifying/hardening only, no scope change):
  - `project.name = "ai-observe"` stated explicitly; `packages.find where=["src"]` added.
  - Platform-failure test = simulated unit test (monkeypatch sys.platform/shutil.which),
    native non-Linux is a manual check (CI is Linux-only).
  - Two-path shim test matrix (installed + checkout-fallback) added as smoke-test MUST.
  - Offline smoke-test handling: host-built artifacts + `pip install --no-build-isolation
    --no-deps`.
  - Editable-install dev caveat documented (stale non-editable install footgun).
  - Consultation Log populated.
- Committed "[Spec 20] Specification with multi-agent review".
- **STATE: spec-approval gate pending.** Notified architect via afx send. STOPPED, waiting
  for human `porch approve 20 spec-approval`.

### Plan phase — start
- spec-approval APPROVED by human. Advanced to plan phase.
- Phase decomposition (3 phases, each an independent git commit within one PR):
  1. Packaging metadata + license: `pyproject.toml` (PEP 621/639, dynamic version,
     packages.find where=["src"], package-data for viewer/static, console scripts,
     setuptools>=77), `LICENSE` (full Apache-2.0), `NOTICE`, `MANIFEST.in` if needed.
     Verified by building wheel+sdist and inspecting contents.
  2. Shim resilience: `bin/*` prefer installed import, fall back to checkout src. Unit
     tests for both modes.
  3. Packaging smoke tests: build → install into clean venv OUTSIDE checkout → exercise
     entry points, viewer static serving (hard criterion), python -m, simulated
     platform-failure, shim two-path matrix, wheel excludes tests.
- Static-asset hardening (importlib.resources) is contingency only — Approach A keeps
  filesystem reads; the hard smoke test is the arbiter.
- Architect confirmed: prefer install-from-sdist; hard static-asset test is the arbiter.

### Plan phase — 3-way review done → GATE
- Verdicts: Gemini APPROVE, Claude APPROVE, Codex REQUEST_CHANGES (HIGH).
- Codex's two concrete points addressed:
  1. No root README — `readme` now points at existing `docs/observe.md` (SPIR B owns docs
     rewrite; minimal root README is the documented fallback).
  2. sdist offline install — concrete recipe: wheel via `pip install --no-deps <wheel>`
     (no network); sdist via pre-provision `setuptools>=77 wheel` then
     `--no-build-isolation --no-deps`, guarded-skip + build-path-validation fallback.
- Claude notes: named shim test file `tests/test_shim_resilience.py`; MANIFEST.in fallback
  already anticipated.
- Committed "[Spec 20] Plan with multi-agent review".
- **STATE: plan-approval gate pending.** Will notify architect, STOP, wait for human
  `porch approve 20 plan-approval`.

### Implement Phase 1 — packaging metadata + license
- plan-approval APPROVED. Architect reminders: one branch/one PR; don't touch
  viewer/server.py unless the hard static-asset test forces it; explicit git add; signal
  when PR up.
- Created: `pyproject.toml`, `LICENSE` (full Apache-2.0 + copyright), `NOTICE`.
- Env: Python 3.14.4, setuptools 78.1.1 (>=77 ✓). `build` frontend not installed
  (PEP 668 externally-managed env), so built via `setuptools.build_meta` directly
  (== `python -m build --no-isolation`), offline. Phase 3 smoke tests will formalize this.
- Build verification (wheel + sdist):
  - Wheel: all 6 static assets present, `tests/` EXCLUDED, entry points = exactly
    ai-observe + ai-observe-viewer, `License-Expression: Apache-2.0`, License-File LICENSE
    + NOTICE, NO legacy Apache classifier, long-desc from docs/observe.md, version 0.1.0
    (dynamic attr resolved), Requires-Python >=3.10.
  - Sdist: includes LICENSE, NOTICE, pyproject, docs/observe.md, all 6 static assets,
    PKG-INFO License-Expression Apache-2.0. (sdist also ships tests/ — fine; spec only
    requires WHEEL to exclude tests.)
  - Benign warning: setuptools sdist `check` warns "no README" because there's no root
    README — cosmetic only; long_description IS populated from docs/observe.md (verified
    in METADATA/PKG-INFO). Matches the plan's readme→docs/observe.md decision; NOT adding a
    stub README (SPIR B owns docs). Documented fallback remains a root README if ever needed.
  - No MANIFEST.in needed — license-files + package-data already ship everything.
- 208 existing tests still pass. Build artifacts cleaned from worktree.
- Signaling PHASE_COMPLETE via `porch done 20`; porch runs 3-way impl review then commits.

### Phase 1 review — LESSON
- iter1 impl review: Claude APPROVE (read working tree), but Gemini + Codex REQUEST_CHANGES
  because the deliverables were UNTRACKED → outside the git-diff review scope.
- **Lesson: the impl consult reviews the COMMITTED branch diff, not the working tree.**
  Must `git add` + commit phase deliverables BEFORE signaling, then rebuttal + re-verify.
- Fixed by committing pyproject/LICENSE/NOTICE (0477f54) + rebuttal. iter2: unanimous
  APPROVE. porch advanced to phase_2.

### Implement Phase 2 — resilient shims
- Rewrote all 5 bin/* shims (ai-observe, claude, codex, gemini, opencode) to the
  try-installed-import / except-fallback-to-checkout-src pattern. Preserved each entry call
  (main_generic; main_shim("claude"/"gemini"/"opencode"); codex keeps
  error_prefix="codex-observe").
- New test: tests/test_shim_resilience.py (4 tests):
  - In-process branch detection: sentinel ai_observe module proves the INSTALLED branch is
    taken without prepending src; removing src proves the FALLBACK branch splices src and
    resolves the real entry. (Skips fallback test if ai_observe is installed in the env.)
  - Subprocess matrix: runs `python bin/<shim>` in installed (PYTHONPATH=src) and
    bare-checkout (no PYTHONPATH, cwd outside repo) modes; uses AI_OBSERVE_DISABLE +
    AI_OBSERVE_REAL_<X> + marker (existing house idiom) to prove dispatch reaches a target.
- 212 tests pass (208 + 4). Committing deliverables BEFORE porch done (Phase 1 lesson).
