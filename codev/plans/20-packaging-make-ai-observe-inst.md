# Plan: Packaging — make ai-observe installable (pyproject + shims + smoke tests)

## Metadata
- **ID**: plan-2026-06-29-packaging-make-ai-observe-inst
- **Status**: draft
- **Specification**: [codev/specs/20-packaging-make-ai-observe-inst.md](../specs/20-packaging-make-ai-observe-inst.md)
- **Created**: 2026-06-29

## Executive Summary

The spec selected **Approach A** for the highest-risk surface (viewer static assets): keep
the existing filesystem-based static serving (`Path(__file__).parent / "static"` +
`read_bytes()`) and make it correct by declaring the assets as **package data**, with the
**hard static-asset smoke test as the sole arbiter** of whether any `importlib.resources`
hardening is needed. The architect confirmed this, and confirmed **install-from-sdist** as
the preferred sdist verification (with build-path validation as the fallback only if
install-from-sdist proves too slow/flaky/offline-hostile).

The implementation is a thin, additive packaging layer over an already-coherent codebase —
no observation semantics change. It decomposes into **three independently-testable,
independently-committable phases**, shipped as **git commits on a single branch / single
PR** (per the PR Strategy: phases are commits, not separate PRs):

1. **Packaging metadata + license** — author `pyproject.toml` (PEP 621/639), `LICENSE`,
   `NOTICE`, and any `MANIFEST.in`; produce a buildable wheel + sdist with the right
   contents (static assets in, `tests/` out, `LICENSE`/`NOTICE` in both).
2. **Shim resilience** — upgrade `bin/*` to prefer installed-package imports and fall back
   to checkout `src/`, working in both modes.
3. **Packaging smoke tests** — build real artifacts, install into a clean venv **outside**
   the checkout, and prove the installed package works end-to-end (entry points, viewer
   static serving incl. the hard criterion, `python -m`, simulated platform-failure, shim
   two-path matrix, `tests/` exclusion).

Phase 1 is foundational (produces the artifact + entry points). Phase 2 is independent of
Phase 1's runtime but logically follows it (the "prefer installed" behavior only matters
once installable). Phase 3 depends on both (it builds/installs the Phase 1 artifact and
tests the Phase 2 shim matrix).

## Success Metrics
- [ ] All specification acceptance criteria met (see spec's Acceptance criteria list).
- [ ] Wheel + sdist build cleanly with standard tooling (`python -m build`).
- [ ] Clean-venv install **outside the checkout** runs `ai-observe`, `ai-observe-viewer`,
      and `python -m ai_observe.viewer` with no `PYTHONPATH=src` and no `bin/*`.
- [ ] **Hard criterion**: installed wheel viewer serves `/`, `/static/index.html`,
      `/static/style.css` (and all six assets) outside the checkout.
- [ ] Wheel **excludes** `tests/`; wheel + sdist **include** `LICENSE`, `NOTICE`, and all
      six viewer static assets.
- [ ] Existing ~208 tests still pass; new packaging smoke tests pass (Linux, `strace`
      available) or skip with a clear reason where capability is absent.
- [ ] Zero new runtime dependencies (unless a concrete one is justified and recorded).

## Phases (Machine Readable)

```json
{
  "phases": [
    {"id": "phase_1", "title": "Packaging metadata + Apache-2.0 license"},
    {"id": "phase_2", "title": "Resilient checkout bin/* shims"},
    {"id": "phase_3", "title": "Packaging smoke tests (installed-artifact)"}
  ]
}
```

## Phase Breakdown

### Phase 1: Packaging metadata + Apache-2.0 license
**Dependencies**: None

#### Objectives
- Make `ai-observe` build into a wheel + sdist with correct PEP 621/639 metadata, the two
  default console scripts, single-sourced version, packaged viewer static assets, and
  excluded `tests/`.
- Add the Apache-2.0 `LICENSE` and `NOTICE` and ship them in both artifacts.

#### Deliverables
- [ ] `pyproject.toml` at repo root with:
  - `[build-system] requires = ["setuptools>=77"]`, `build-backend = "setuptools.build_meta"`.
  - `[project]`: `name = "ai-observe"`, `description`,
    `readme = {file = "docs/observe.md", content-type = "text/markdown"}`,
    `requires-python = ">=3.10"`, `authors`, `keywords`, classifiers (Python versions,
    `Operating System :: POSIX :: Linux`, Development Status, Environment/Topic as
    appropriate), `dynamic = ["version"]`.
    - **README decision** (resolving Codex plan review): the repo has **no root
      `README*`**. Rather than create a stub README that SPIR B's docs rewrite would
      replace (README/docs rewrite is an explicit SPIR-B non-goal), point `readme` at the
      existing, accurate `docs/observe.md`. setuptools embeds it as the long-description and
      auto-includes the referenced file in the sdist, so a rebuild-from-sdist stays valid.
      (If pointing at `docs/observe.md` proves problematic at build time, the fallback is a
      minimal root `README.md` — but the existing file is preferred to avoid scope creep.)
  - **PEP 639**: `license = "Apache-2.0"`, `license-files = ["LICENSE", "NOTICE"]`.
    **No** `License :: OSI Approved :: Apache Software License` classifier.
  - `[project.scripts]`: `ai-observe = "ai_observe.observe:main_generic"`,
    `ai-observe-viewer = "ai_observe.viewer.__main__:main"` — and **no** `claude`/`codex`/
    `gemini`/`opencode` scripts.
  - `[project.urls]`: homepage/repository (SHOULD).
  - `[tool.setuptools.dynamic] version = {attr = "ai_observe.__version__"}`.
  - `[tool.setuptools.packages.find] where = ["src"]`.
  - `[tool.setuptools.package-data]` shipping `ai_observe.viewer` `static/*` (all six files).
- [ ] `LICENSE` — full, unmodified Apache-2.0 text + `Copyright 2025-2026 Mohid Makhdoomi`.
- [ ] `NOTICE` — one-line attribution.
- [ ] `MANIFEST.in` **only if** needed to guarantee sdist includes `LICENSE`/`NOTICE`/static
      assets (PEP 639 `license-files` + `include-package-data` may already suffice; add
      `MANIFEST.in` only if an sdist inspection shows a gap).
- [ ] Optional per-file `# SPDX-License-Identifier: Apache-2.0` headers (encouraged, not
      required; will not be added en masse if noisy).

#### Implementation Details
- Single-source version via setuptools `dynamic` `attr` pointing at
  `ai_observe.__version__` (`0.1.0`) — avoids drift between `pyproject.toml` and
  `__init__.py`.
- `packages.find where = ["src"]` ensures the `src/` layout is discovered and that root-level
  `tests/` is **outside** the package tree (so it is not packaged into the wheel).
- `package-data` for `ai_observe.viewer` `["static/*"]` ships all six assets into the wheel
  next to the module; combined with the existing on-disk `Path(__file__)` read, the wheel
  serves them correctly (validated for real in Phase 3).
- Keep `setuptools.build_meta` backend; pin `setuptools>=77` so the SPDX `license` string
  resolves.

#### Acceptance Criteria
- [ ] `python -m build` produces `dist/ai_observe-0.1.0-py3-none-any.whl` and
      `ai_observe-0.1.0.tar.gz` (or `ai-observe-…`; whichever setuptools normalizes to).
- [ ] Wheel contents (`unzip -l` / `python -m zipfile -l`) include
      `ai_observe/viewer/static/{index.html,index.js,aggregator.js,table.js,treemap.js,style.css}`
      and the dist-info `entry_points.txt` listing exactly `ai-observe` and `ai-observe-viewer`.
- [ ] Wheel contents do **not** include any `tests/` path.
- [ ] Wheel + sdist include `LICENSE` and `NOTICE` (sdist root and/or `*.dist-info`/
      `*.egg-info` as tooling places them).
- [ ] No legacy Apache classifier present; `license = "Apache-2.0"` resolves without a
      setuptools error.
- [ ] Existing test suite still passes (no import/layout regressions).

#### Test Plan
- **Manual/inline (this phase)**: build artifacts; inspect wheel + sdist contents via
  `python -m zipfile -l` / `tar -tzf`; run `pip show`/metadata check in a scratch venv is
  deferred to Phase 3. Confirm `python -c "import ai_observe; print(ai_observe.__version__)"`
  still imports from `src` in-checkout.
- **Automated**: formal automated assertions on artifact contents are authored in Phase 3
  (smoke tests). This phase's verification is the build + manual content inspection.

#### Rollback Strategy
- Delete `pyproject.toml`, `LICENSE`, `NOTICE`, `MANIFEST.in`; the checkout reverts to its
  prior `bin/*` + `python -m` workflow with zero runtime impact.

#### Risks
- **Risk**: `dynamic` version `attr` fails because setuptools can't import the package at
  build time under `src/` layout.
  - **Mitigation**: explicit `packages.find where=["src"]`; verified by a successful build
    in this phase's acceptance criteria.
- **Risk**: PEP 639 `license`/`license-files` rejected by an older setuptools.
  - **Mitigation**: pin `setuptools>=77`; build in this phase proves it resolves.

---

### Phase 2: Resilient checkout bin/* shims
**Dependencies**: Phase 1 (the installed-import path is meaningful once the package is
installable; functionally the shim change is independent and could stand alone, but it is
ordered after Phase 1 for coherence).

#### Objectives
- Upgrade all five `bin/*` shims to **prefer the installed-package import** and fall back to
  splicing the checkout `ROOT/src` onto `sys.path` only when the package import is
  unavailable — so the same shims work installed or in a bare checkout.

#### Deliverables
- [ ] `bin/ai-observe`, `bin/claude`, `bin/codex`, `bin/gemini`, `bin/opencode` updated to
      the try-installed-then-fallback pattern, preserving each shim's existing target
      (`main_generic` for `ai-observe`; `main_shim("<tool>")` for the named ones, including
      `codex`'s `error_prefix="codex-observe"`).
- [ ] A small shared idiom (inline per-file; no new importable runtime module is required —
      shims must work before the package is importable, so the fallback logic stays inline
      in each shim).
- [ ] Unit tests for both shim modes in `tests/test_shim_resilience.py` (authored here; see
      Test Plan). Note: shims use an `if __name__ == "__main__"` guard, so tests drive them
      via `subprocess`/`runpy` rather than importing them.

#### Implementation Details
- Pattern per shim:
  ```python
  try:
      from ai_observe.observe import main_generic  # or main_shim
  except ImportError:
      import sys
      from pathlib import Path
      sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
      from ai_observe.observe import main_generic
  ```
- Keep shdebang `#!/usr/bin/env python3` and the `raise SystemExit(...)` invocation.
- Do **not** convert shims into console scripts (the named ones must stay checkout-only per
  the baked decision); only `ai-observe`/`ai-observe-viewer` are console scripts (Phase 1).

#### Acceptance Criteria
- [ ] Each shim imports successfully and dispatches to the correct entry function in both
      modes.
- [ ] In a checkout **without** the package installed, the named shims still observe their
      tool exactly as before (behavior-preserving).
- [ ] No shim depends on `bin/` being on `sys.path` or on CWD.

#### Test Plan
- **Unit Tests** (`tests/`): a test that loads each shim's logic and asserts:
  - (a) **installed path**: when `ai_observe` is importable, the shim uses it without
    mutating `sys.path` with the checkout `src` (assert the import resolves to the installed
    module / that the fallback branch is not taken).
  - (b) **checkout-fallback path**: when `ai_observe` is not importable (simulate via a
    clean `sys.path`/`sys.modules` and `monkeypatch`), the shim adds `ROOT/src` and resolves.
  - Approach: drive the shims as subprocesses and/or `runpy`-execute them with a controlled
    environment so both branches are exercised deterministically. Prefer subprocess
    execution with a tailored `PYTHONPATH`/`sys.path` to avoid in-process import-cache
    bleed. (The full installed-vs-checkout matrix is also re-proven against a real installed
    wheel in Phase 3.)
- **Integration**: covered by Phase 3's installed-artifact run.
- **Manual**: `./bin/ai-observe --help` from the checkout still works.

#### Rollback Strategy
- Revert the five shim files to their prior single-path form; no other surface is affected.

#### Risks
- **Risk**: import-cache / `sys.modules` bleed makes the two branches hard to test
  in-process and yields flaky tests.
  - **Mitigation**: drive shims as subprocesses with controlled environment for the
    branch-selection tests; keep in-process tests (if any) hermetic via `monkeypatch` +
    `sys.modules` cleanup.

---

### Phase 3: Packaging smoke tests (installed-artifact)
**Dependencies**: Phase 1 (buildable artifact + entry points), Phase 2 (shim two-path
matrix to assert against a real install).

#### Objectives
- Prove, with automated tests over **real built artifacts** installed into a **clean venv
  outside the checkout**, that the installed package satisfies the spec — especially the
  hard static-asset criterion.

#### Deliverables
- [ ] A new smoke-test module (e.g. `tests/test_packaging_smoke.py`) covering the spec's
      MUST smoke list:
  - build wheel **and** sdist with standard tooling;
  - install from the **built wheel** into a fresh venv;
  - install from the **built sdist** (preferred per architect) — fall back to validating the
    sdist build path only if install-from-sdist is infeasible/offline-hostile;
  - installed package works **outside the checkout** (run tests from a temp CWD, scrub
    `PYTHONPATH`, ensure no reliance on `bin/*`);
  - `ai-observe --help` (or equivalent usage path) works;
  - one small observed command runs successfully **on Linux with `strace`** (guarded/skipped
    otherwise);
  - **unsupported platform/backend** path fails clearly — validated by a **targeted
    simulated unit test** (monkeypatch `sys.platform` / `shutil.which` so the strace backend
    raises its clear error), per spec (native non-Linux is a documented manual check);
  - **shim two-path matrix**: installed-import path and checkout-fallback path;
  - `ai-observe-viewer` starts/serves with `--no-browser`;
  - `python -m ai_observe.viewer` works after install;
  - installed viewer serves `/`, `/static/index.js`, `/static/style.css`;
  - **(hard criterion)** installed-from-wheel, outside checkout: viewer serves `/` and
    GETs of `/static/index.html` and `/static/style.css` return the assets;
  - installed package exposes all six static files;
  - wheel/sdist include viewer static assets;
  - wheel does **not** include `tests/`.
- [ ] Guards/skips with clear reasons for capabilities the environment lacks (no `strace`,
      no network, non-Linux), so the suite is honest without silently passing.

#### Implementation Details
- **Build once per session**: a session-scoped fixture builds wheel + sdist into a temp
  `dist/` (host environment, where the build backend is present) and yields paths.
- **Offline robustness — concrete install recipes** (resolving Codex plan review):
  - **Wheel install** (primary, used for the hard static-asset criterion): a fresh
    `python -m venv` already ships `pip`; a wheel needs **no build backend**, so
    `pip install --no-deps <wheel>` installs with no network (zero runtime deps → `--no-deps`
    is safe). This is the no-network-required happy path.
  - **Sdist install** (preferred per architect, with explicit pre-provisioning): installing
    an sdist **builds** it, so the venv must have the build backend **before** the
    `--no-build-isolation` install. Concretely, in the fresh venv:
    1. `pip install "setuptools>=77" wheel` (pre-provision the backend; this step may need
       network/cache — guard the whole sdist test and **skip with a clear reason** if the
       backend cannot be provisioned offline);
    2. `pip install --no-build-isolation --no-deps <sdist>`.
  - **Fallback** (only if install-from-sdist is infeasible/offline-hostile in the harness):
    validate the **sdist build path** instead — unpack the `.tar.gz`, assert it contains
    `pyproject.toml`, `LICENSE`, `NOTICE`, and the six static assets, and optionally
    `python -m build` from the unpacked tree. The architect prefers real install-from-sdist;
    this fallback keeps the suite honest where the environment forbids it.
- **Outside-checkout enforcement**: run the installed-package assertions from a temp working
  directory with `PYTHONPATH` cleared and `sys.path` not containing the checkout `src` (use
  the venv's interpreter via `subprocess`).
- **Viewer serving test**: start `ViewerServer` (or `ai-observe-viewer --no-browser`) against
  a tiny temp `.jsonl`, read the bound URL from stderr/`server.url`, HTTP-GET `/` and the
  static routes via `urllib`, assert 200 + asset bytes; tear the server down.
- **Static-asset arbiter**: if the hard criterion fails, the contingency is to fix
  `package-data`/`MANIFEST.in` first; only if a genuine loader issue remains is an
  `importlib.resources` change to `viewer/server.py` introduced (kept minimal). The hard
  test is the arbiter, per architect.

#### Acceptance Criteria
- [ ] All MUST smoke tests pass on Linux with `strace` + network-free install; capability-
      gated tests skip with explicit reasons elsewhere.
- [ ] The hard static-asset test passes against a wheel install **outside** the checkout.
- [ ] Existing ~208 tests still pass; no coverage regression on the existing modules.

#### Test Plan
- **Integration/Smoke**: the module above (this is the phase's primary product).
- **Manual**: run `python -m build`, `pip install dist/*.whl` in a throwaway venv in `/tmp`,
  `cd /tmp`, run the three entry points and curl the viewer — mirrors the automated path.

#### Rollback Strategy
- Remove the smoke-test module; the package and shims remain functional. (Removing tests is
  a last resort; the more likely "rollback" is fixing packaging config the tests exposed.)

#### Risks
- **Risk**: building/installing real artifacts is slow and could be flaky in CI.
  - **Mitigation**: session-scoped build fixture (build once); `--no-build-isolation
    --no-deps` for fast, network-free installs; capability guards.
- **Risk**: the hard static-asset test reveals a real `package-data` gap late.
  - **Mitigation**: Phase 1 already declares `package-data` and inspects wheel contents, so
    this is caught early; Phase 3 is confirmation, not first discovery.
- **Risk**: viewer server port binding / teardown races make the HTTP test flaky.
  - **Mitigation**: use the server's OS-chosen-port fallback and its real bound URL; poll the
    endpoint with a short bounded retry; always stop the server in a `finally`.

---

## Dependency Map
```
Phase 1 (pyproject + LICENSE/NOTICE) ──→ Phase 3 (smoke tests)
Phase 2 (resilient shims) ─────────────↗
```
Phase 3 depends on **both** Phase 1 (artifact/entry points) and Phase 2 (shim matrix).

## Resource Requirements
- **Environment**: Linux with `python -m build`, `pip`, and `strace` available for the full
  smoke suite; subsets skip cleanly elsewhere. No services, DB, or infra. No new runtime
  dependencies (build/test-time only: `build`, and the stdlib).

## Integration Points
- **Follow-on SPIR B (CI/docs)**: consumes the wheel this work produces. No code coupling
  here beyond producing valid local artifacts and documented entry points.
- **Internal**: `viewer/server.py` static serving (unchanged unless the hard test forces a
  minimal `importlib.resources` hardening); `backends/strace.py` runtime gating (unchanged,
  exercised by the simulated platform-failure test).

## Risk Analysis
### Technical Risks
| Risk | Probability | Impact | Mitigation | Owner |
|------|------------|--------|------------|-------|
| src-layout `package-data` footgun (wheel 404s on static) | M | H | Declare `package-data`; inspect wheel in Phase 1; hard smoke test as arbiter in Phase 3 | builder |
| `dynamic` version `attr` fails under src layout | L | M | `packages.find where=["src"]`; build verified in Phase 1 | builder |
| PEP 639 `license` rejected by old setuptools | L | M | Pin `setuptools>=77` | builder |
| Smoke tests slow/flaky (build, sockets) | M | M | Session-scoped build; `--no-build-isolation --no-deps`; bounded retries; capability guards | builder |
| Shim two-branch tests flaky from import-cache bleed | M | M | Subprocess-driven branch tests; hermetic monkeypatch cleanup | builder |

### Schedule Risks
| Risk | Probability | Impact | Mitigation | Owner |
|------|------------|--------|------------|-------|
| N/A — single-builder, no calendar coupling | — | — | Phases gated by "done", not time | builder |

## Validation Checkpoints
1. **After Phase 1**: wheel + sdist build; contents correct (static in, tests out,
   LICENSE/NOTICE in); existing tests pass.
2. **After Phase 2**: shims work in both modes; named shims behavior-preserving in checkout.
3. **After Phase 3**: full installed-artifact smoke suite green on Linux; hard static-asset
   criterion proven outside checkout; existing tests still pass.

## Monitoring and Observability
- N/A — this is a packaging change; no runtime services or metrics are introduced. The
  existing sensitive-data warning and viewer remain unchanged.

## Documentation Updates Required
- [ ] None required by this SPIR (README/docs rewrite is explicitly SPIR B). Inline
      docstrings on new test module and shims kept self-documenting. Arch/lessons doc updates
      handled in the Review phase if warranted.

## Post-Implementation Tasks
- [ ] (SPIR B) CI wires up the wheel build/install; not in scope here.
- [ ] Verify phase: pull integration branch, confirm installed entry points work.

## Expert Review
**Date**: 2026-06-29
**Model**: porch 3-way — Gemini, Codex, Claude

**Verdicts**: Gemini **APPROVE** (HIGH), Claude **APPROVE** (HIGH), Codex **REQUEST_CHANGES**
(HIGH — two concrete items, both addressed below).

**Key Feedback**:
- **Codex (REQUEST_CHANGES)**:
  1. The plan declared a `readme` but the repo has **no root `README*`** — underspecified.
  2. The sdist offline/`--no-build-isolation` install needs the build backend
     pre-provisioned in the fresh venv before install — hinted, not concrete.
- **Gemini (APPROVE)**: confirmed alignment with spec + baked decisions; endorsed Approach A,
  dynamic version, subprocess-driven shim tests, and offline install strategy.
- **Claude (APPROVE)**: verified every codebase claim (paths, signatures, six static assets,
  five shims, strace gating) against source; two non-blocking notes — name the shim test file
  and remember the `MANIFEST.in` fallback if `LICENSE`/`NOTICE` miss the sdist (both already
  anticipated; shim test file now named).

**Plan Adjustments**:
- Phase 1: `readme` now points at the existing `docs/observe.md` (no stub README; SPIR B owns
  the docs rewrite), with a minimal root `README.md` as fallback.
- Phase 3: added concrete offline install recipes — wheel via `pip install --no-deps <wheel>`
  (no network), sdist via pre-provisioning `setuptools>=77 wheel` then
  `pip install --no-build-isolation --no-deps <sdist>`, with a guarded skip and a
  build-path-validation fallback.
- Phase 2: named the shim test file `tests/test_shim_resilience.py` and noted the
  `__main__`-guard → subprocess/runpy testing approach.

## Approval
- [ ] Expert AI Consultation Complete (3-way)
- [ ] Human plan-approval gate

## Change Log
| Date | Change | Reason | Author |
|------|--------|--------|--------|
| 2026-06-29 | Initial plan draft | Plan phase start | builder spir-20 |
| 2026-06-29 | Address 3-way review (readme source, sdist offline recipe, shim test file) | Codex REQUEST_CHANGES + Claude notes | builder spir-20 |

## Notes
- **PR strategy**: all three phases are git commits on one branch / one PR (per issue #20 PR
  Strategy). PR opened during/after Phase 3 unless the architect requests an earlier PR.
- **Architect directives folded in**: prefer install-from-sdist; hard static-asset smoke
  test is the arbiter for any `importlib.resources` change.
- **Baked decisions** from the spec (Apache-2.0/PEP 639, setuptools + src layout, zero deps,
  default console scripts only, resilient shims) are honored and not relitigated here.
