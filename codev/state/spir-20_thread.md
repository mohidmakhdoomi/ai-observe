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
