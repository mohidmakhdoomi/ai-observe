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

### Phase 2 review iter1 — Codex REQUEST_CHANGES (both valid, fixed)
- Gemini APPROVE, Claude APPROVE (Claude consult first hit a usage limit; retried OK), Codex
  REQUEST_CHANGES.
- Codex 1: `except ImportError` too broad → could mask a broken installed package. Fixed:
  narrowed to `except ModuleNotFoundError as exc: if exc.name != "ai_observe": raise` in all
  5 shims (absent top-level pkg → fallback; deeper failure → surface).
- Codex 2: fallback tests not hermetic. Fixed: in-process fallback uses a sys.meta_path
  blocker to force the branch regardless of installed env (skip removed); subprocess fallback
  runs under `python -S` (no site-packages) so only the checkout fallback can satisfy import;
  added test_broken_installed_package_is_not_masked_by_fallback for Codex-1 behavior.
- 213 tests pass (208 + 5). Rebuttal written. Committing fixes, then porch done → iter2 re-review.
- iter2: unanimous APPROVE (Gemini, Codex, Claude). porch advanced to phase_3.

### Implement Phase 3 — packaging smoke tests
- New: tests/test_packaging_smoke.py (19 tests). Module-scoped fixture builds wheel+sdist
  ONCE (from copied source inputs = stand-in fresh clone) and installs the wheel into a
  fresh venv with `--no-index --no-deps` (offline; zero deps). Tests exercise the INSTALLED
  package, not checkout src.
- Coverage: both artifacts built; wheel ships all 6 static assets; wheel excludes tests/;
  wheel entry_points = only ai-observe + ai-observe-viewer (no shadow shims); wheel+sdist
  carry LICENSE+NOTICE; License-Expression Apache-2.0, no legacy classifier; sdist ships
  static+license; sdist build-path valid (unpack+rebuild); install-from-sdist best-effort
  (system-site venv + --no-build-isolation; RAN here, not skipped); console scripts present;
  usage path works; import outside checkout w/o PYTHONPATH resolves to venv not src; all 6
  static files exposed by installed pkg; viewer serves via `python -m` and via console
  script; **HARD CRITERION**: clean-venv wheel install OUTSIDE checkout serves /,
  /static/index.html, /static/index.js, /static/style.css (200 + bytes); unsupported
  platform simulated unit test (monkeypatch sys.platform → "Linux required"); installed
  backend-unavailable (PATH="") fails clearly; Linux+strace live-observe runs + warns by
  default; AI_OBSERVE_QUIET suppresses warning. Live tests skip cleanly w/o Linux/strace or
  on ptrace-denied.
- IMPORTANT FIX during dev: build_sdist + build_wheel in the SAME interpreter leaves the
  2nd artifact unwritten (setuptools in-process state). Fixed by building each kind in its
  OWN subprocess.
- Static serving confirmed via Approach A (no viewer/server.py change needed — hard test
  green). importlib.resources NOT required.
- Full suite: 232 tests pass (213 + 19). Committing before porch done (Phase 1 lesson).

### Phase 3 review iter1 — Codex REQUEST_CHANGES (+Gemini COMMENT), all fixed
- Claude APPROVE, Gemini COMMENT, Codex REQUEST_CHANGES.
- Codex 1: phase_3 shim matrix must run against REAL install (not PYTHONPATH=src). Fixed:
  added InstalledShimMatrixTests running bin/* with the venv interpreter (installed pkg)
  and `venv/bin/python -S` (fallback to checkout src), all 5 shims, DISABLE+REAL+marker.
- Codex 2: sdist install used --system-site-packages (masks reqs). Fixed: clean venv +
  pre-provision `setuptools>=77 wheel` + `--no-build-isolation --no-index --no-deps`; skip
  if offline; assert resolves to venv not src. RUNS here (network available).
- Gemini 1: sys.executable may lack setuptools → setUpModule now SkipTest if absent.
- Gemini 2: HTTPError not closed → added exc.close() in finally.
- 234 tests pass (213 + 21). Rebuttal written. Commit, porch done → iter2 re-review.
- iter2: unanimous APPROVE. porch advanced to REVIEW phase.

### Review phase
- Wrote codev/reviews/20-...md with required `## Architecture Updates` + `## Lessons
  Learned Updates` sections (porch checks grep for these).
- Updated COLD docs: arch.md (+"Packaging and distribution" section), lessons-learned.md
  (+3 lessons: wheel-static footgun, build-each-kind-own-interpreter, narrow import
  fallback). HOT placeholder files left for a dedicated governance pass (documented in
  review) — not half-populated by this packaging spec.
- Opened **PR #22** (base main) with all 3 phase-commits. Pushed builder/spir-20.
- Notifying architect PR is up for integration review.
- Builder 3-way PR review: Codex APPROVE, Claude APPROVE, Gemini lane timed out (agy env,
  non-blocking). Architect ran independent full CMAP (all APPROVE) + approved + instructed
  merge.
- **PR #22 MERGED** (squash, base main) at 2026-06-30T00:07Z. Recorded via
  `porch done 20 --merged 22`.
- porch now at `pr` gate (human approval) → notified architect for
  `porch approve 20 pr`. STOPPED, waiting. Next: verify phase.

### Verify phase
- pr gate approved. PR #22 already merged (squash). Fetched origin/main — contains the
  packaging files, identical to worktree (no diff).
- END-TO-END VERIFY (built wheel from merged source → fresh venv → OUTSIDE checkout, no
  PYTHONPATH):
  - import ai_observe → 0.1.0, resolves to venv site-packages (not src). ✓
  - `ai-observe` console script: usage path (rc 2) ✓; real strace observation captured a
    schema-v2 `modify` event for the written file, sensitive-data warning on by default. ✓
  - `ai-observe-viewer` console script: GET / + /static/{index.html,index.js,style.css}
    all 200 with bytes. ✓
  - `python -m ai_observe.viewer`: same, all 200. ✓
  - HARD CRITERION confirmed live: static served from installed wheel outside checkout. ✓
- Temp verify dir cleaned; worktree clean.
- Signaling verify ready via `porch done 20` → architect approves verify-approval gate.
  NOT running afx cleanup (architect's step).
- verify-approval APPROVED. `porch next` → **status: complete, phase: verified**.
- PROJECT 20 DONE. PR #22 merged to main; SPIR run fully verified. Awaiting architect to
  close issue #20 + run afx cleanup. Worktree clean.
