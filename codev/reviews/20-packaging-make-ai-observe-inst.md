# Review: Packaging — make ai-observe installable (pyproject + shims + smoke tests)

- **Spec**: [codev/specs/20-packaging-make-ai-observe-inst.md](../specs/20-packaging-make-ai-observe-inst.md)
- **Plan**: [codev/plans/20-packaging-make-ai-observe-inst.md](../plans/20-packaging-make-ai-observe-inst.md)
- **Protocol**: SPIR (strict) · **Issue**: #20 · **Branch**: `builder/spir-20`

## Summary

Made `ai-observe` installable and proved the **installed artifact** works **outside the
source checkout**, without changing core observation semantics. Delivered as three
git-committed phases on one branch / one PR:

1. **Packaging metadata + Apache-2.0 license** — `pyproject.toml` (PEP 621/639, dynamic
   version, `packages.find where=["src"]`, viewer static as `package-data`, two console
   scripts), full Apache-2.0 `LICENSE`, one-line `NOTICE`.
2. **Resilient `bin/*` shims** — prefer the installed package, fall back to the checkout
   `src/` only when the package itself is absent.
3. **Packaging smoke tests** — build real wheel + sdist, install into a clean venv, and
   exercise the installed package (console scripts, viewer static serving incl. the hard
   criterion, `python -m`, shim two-path matrix against the real install, simulated
   platform failure, `tests/` exclusion).

All ~208 pre-existing tests still pass; the work adds **26 tests** (5 shim-resilience + 21
packaging smoke) for **234 total**, all green.

## What was built (by acceptance criterion)

Every spec acceptance criterion is met and, where feasible, machine-checked:

- `pyproject.toml`: PEP 621, `setuptools`, `src/` layout, `requires-python >=3.10`,
  classifiers, project URLs. ✅
- License: `License-Expression: Apache-2.0` + `license-files = ["LICENSE","NOTICE"]`,
  `setuptools>=77` pinned, **no** legacy Apache classifier. `LICENSE` + `NOTICE` ship in
  both wheel and sdist. ✅ (`ArtifactContentTests`)
- Zero runtime dependencies. ✅
- Default console scripts are exactly `ai-observe` + `ai-observe-viewer`; the named tool
  shims are **not** installed. ✅ (`test_wheel_declares_only_expected_console_scripts`)
- `bin/*` shims prefer installed imports, fall back to checkout `src/` only for an absent
  package (`exc.name == "ai_observe"`), surfacing broken installs. ✅
  (`test_shim_resilience.py`, `InstalledShimMatrixTests`)
- Fresh clone builds wheel + sdist; fresh venv installs and runs the documented entry
  points; smoke tests run **outside** the checkout with no `PYTHONPATH=src`. ✅
- `python -m ai_observe.viewer` works after install. ✅
- **Hard criterion**: clean-venv wheel install, outside the checkout, viewer serves `/`,
  `/static/index.html`, `/static/index.js`, `/static/style.css`. ✅
  (`test_installed_viewer_serves_static_outside_checkout`)
- Wheel + sdist contain static assets; wheel excludes `tests/`. ✅
- Unsupported platform/backend paths fail clearly. ✅ (simulated unit test + installed
  backend-unavailable test)
- Sensitive-data warning on by default; `AI_OBSERVE_QUIET` suppresses it. ✅
  (`LiveObservationSmokeTests`, Linux+strace-gated)
- Existing tests still pass. ✅

## Deviations from the plan

- **Build mechanism**: the `build` frontend is not installed in this environment (PEP 668
  externally-managed), so builds use the PEP 517 backend directly
  (`setuptools.build_meta`, i.e. `python -m build --no-isolation` semantics) — both manually
  during Phase 1 and in the smoke fixture. No behavioral difference to the artifacts.
- **Shim fallback narrowed beyond the plan's sketch**: per Phase-2 review, the fallback
  catches `ModuleNotFoundError` and re-raises when the missing module isn't the top-level
  package, so a broken install is not masked. (Plan said "on ImportError"; this is stricter
  and better.)
- **`readme`** points at the existing `docs/observe.md` (no stub README; SPIR B owns the
  docs rewrite). No `MANIFEST.in` was needed — `license-files` + `package-data` ship
  everything.
- **No `viewer/server.py` change**: Approach A held — the hard static-asset smoke test
  passes with filesystem reads, so no `importlib.resources` change was introduced (per the
  architect's "hard test is the arbiter" directive).

## Systematic issues observed

- **Review scope is the committed git diff, not the working tree.** Phase 1's first review
  flagged the deliverables as "missing" because they were authored but uncommitted. Fixed by
  committing phase deliverables *before* signalling; this became a standing discipline for
  Phases 2–3 and is worth encoding as a strict-mode habit.
- **Packaging seams are easy to assert weakly.** Both Codex review rounds caught
  "looks-installed-but-isn't" gaps (simulated `PYTHONPATH=src` standing in for a real
  install; `--system-site-packages` standing in for a clean install). The fix each time was
  to test against the *real* artifact. Captured as lessons below.

## Architecture Updates

Applied to the COLD reference doc `codev/resources/arch.md` (reference-detail tier; the
HOT `arch-critical.md` remains a project-wide STARTER placeholder and is intentionally left
for a dedicated governance pass rather than half-populated by this packaging spec):

- Added a **"Packaging and distribution"** section recording the durable system shape:
  installable package (PEP 621/639, `src/` layout, dynamic version), Apache-2.0 via PEP 639
  with `setuptools>=77`, the two-and-only-two console scripts (named shims deliberately not
  entry points), the narrow installed-vs-checkout shim fallback, viewer static shipped as
  `package-data` and served from disk (validated by the outside-checkout wheel smoke test),
  and the off-Linux-install / Linux-only-runtime boundary with `tests/` excluded from the
  wheel.

No existing arch facts were invalidated; the observation/backend/viewer architecture is
unchanged by this work.

## Lessons Learned Updates

Added to the COLD `codev/resources/lessons-learned.md` (three new lessons):

- **Prove src-layout package data from an installed wheel, not the checkout** — the
  classic `package_data` footgun; the arbiter is a clean-venv, outside-checkout smoke test
  that GETs the real static routes.
- **Build each distribution kind in its own interpreter** — `build_sdist` + `build_wheel`
  in one process drops the second artifact; and pre-provision the backend (or skip) for
  install-from-sdist because modern venvs lack `setuptools`.
- **Scope import fallbacks to "package absent", not any ImportError** — catch
  `ModuleNotFoundError` with an `exc.name` guard so broken installs surface; test
  installed / absent→fallback / present-but-broken→surface, forcing the fallback
  hermetically (`sys.meta_path` blocker or `python -S`).

## What went well

- The spec/plan baked the right decisions (Approach A + the hard smoke test as arbiter), so
  implementation had no architectural surprises; static assets worked first try once
  `package-data` was declared.
- Reusing the existing test idioms (`AI_OBSERVE_DISABLE` + `AI_OBSERVE_REAL_*` + marker,
  fake-strace) kept new tests in-house-style and avoided overmocking — the smoke suite
  exercises real artifacts and real subprocesses.

## What was challenging

- Getting the packaging smoke tests genuinely hermetic and offline-robust (clean venv,
  `--no-index --no-deps`, subprocess-per-build, capability gating) took the most iteration,
  and was exactly where the external reviews added the most value.

## Flaky Tests

None. No pre-existing flaky tests were encountered; no tests were skipped to bypass
failures. Capability-gated skips (non-Linux, no `strace`, ptrace-denied, offline backend
provisioning) are deliberate and carry clear reasons.

## Follow-ups (out of scope here — SPIR B)

GitHub Actions CI (which installs this wheel), test-reliability fixes, README/docs rewrite,
the release checklist, and PyPI publishing are deferred to the follow-on issue.
