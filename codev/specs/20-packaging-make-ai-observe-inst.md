# Specification: Packaging — make ai-observe installable (pyproject + shims + smoke tests)

## Summary

`ai-observe` is a functionally coherent, Linux-first layered filesystem observer
(Python, `src/` layout, ~208 passing tests, a local-only browser viewer with static
assets). It is still **checkout-oriented** rather than **package-oriented**: there is no
`pyproject.toml`, no installable console scripts, the `bin/*` shims manually splice the
local `src/` directory onto `sys.path`, the viewer's static assets are not declared as
package data, and there is no `LICENSE` / classifiers.

This spec makes `ai-observe` **installable** and proves the **installed artifact** works
**outside the source checkout** — without changing core observation semantics. It is
**SPIR A of 2** ("make it installable"). The follow-on (SPIR B: "CI + test-reliability +
docs") depends on this work merging first, because CI installs the wheel this work
produces.

Target UX:

```bash
pip install .
# eventually: pipx install ai-observe

ai-observe --session demo -- python -c 'from pathlib import Path; Path("x.txt").write_text("hello")'
ai-observe-viewer .codev/observe/<session>.jsonl
python -m ai_observe.viewer .codev/observe/<session>.jsonl
```

Installation may succeed on non-Linux platforms where packaging permits it, but
Linux-only live observation paths must fail with a **clear runtime error** explaining the
Linux/`strace` requirement rather than cryptic subprocess/import failures.

## Background and current state

### Existing behavior

- Python package `ai_observe` under a `src/` layout (`src/ai_observe/...`).
- Entry points already exist as importable callables:
  - `ai_observe.observe:main_generic` — the generic `ai-observe -- <command>` wrapper.
  - `ai_observe.viewer.__main__:main` — the local browser viewer
    (`python -m ai_observe.viewer <jsonl>` already works in-checkout).
- `bin/*` shims: `ai-observe`, `claude`, `codex`, `gemini`, `opencode`. Each does
  `sys.path.insert(0, str(ROOT / "src"))` and then imports `ai_observe.*`. They only work
  from inside the checkout.
- Viewer static assets live in `src/ai_observe/viewer/static/` and are served by
  `viewer/server.py` via `_STATIC_DIR = Path(__file__).resolve().parent / "static"` and
  `path.read_bytes()`. Files: `index.html`, `index.js`, `aggregator.js`, `table.js`,
  `treemap.js`, `style.css`.
- Linux/`strace` gating already exists in `backends/strace.py`: it raises
  `"Linux required for strace backend"` when `sys.platform` is not Linux, and
  `"strace not found; install strace or set AI_OBSERVE_DISABLE=1"` when the binary is
  absent.
- A sensitive-data warning is printed on observed-command startup unless the
  `AI_OBSERVE_QUIET` env flag is set.
- Version source of truth: `src/ai_observe/__init__.py` → `__version__ = "0.1.0"`.

### Current limitations

- No `pyproject.toml` / PEP 621 metadata → not `pip install`-able.
- No console-script entry points → users must invoke `bin/*` or `python -m`.
- `bin/*` shims hard-require the checkout layout (`ROOT/src` on `sys.path`).
- Viewer static assets are not declared as package data → the classic src-layout wheel
  footgun: the wheel installs fine but the viewer 404s on `/static/*`.
- No `LICENSE` / `NOTICE` / classifiers → not redistributable, no PyPI-ready metadata.

## Baked decisions

The following decisions are fixed by the architect (issue #20) and are **not** to be
relitigated in this spec, the plan, or the implementation. If a serious problem with one
is discovered during implementation, raise it via `afx send architect` rather than
overriding it.

### License (Apache-2.0)

The project license is **Apache-2.0** (chosen for its express patent grant,
permissive/commercial-friendly terms, and built-in inbound = outbound contribution model
under §5). Declare it using **modern PEP 639** mechanics:

- `LICENSE` file at repo root containing the **full, unmodified Apache-2.0 text**, with a
  copyright line (e.g. `Copyright 2025-2026 Mohid Makhdoomi`).
- Minimal `NOTICE` file at repo root (one-line attribution is sufficient for this
  zero-dependency project; downstreams must preserve it if present).
- In `pyproject.toml`, use the **PEP 639 SPDX expression** form: `license = "Apache-2.0"`
  plus `license-files = ["LICENSE", "NOTICE"]`. **Do NOT** also add the legacy
  `License :: OSI Approved :: Apache Software License` classifier — the SPDX field is
  canonical for modern tooling/PyPI.
- Because PEP 639's SPDX `license` string requires recent setuptools, pin the build
  backend accordingly in `[build-system]` (e.g. `requires = ["setuptools>=77"]`) so the
  declaration resolves.
- Per-file SPDX headers (`# SPDX-License-Identifier: Apache-2.0`) are encouraged but
  optional.

### Packaging (workstream 1)

- PEP 621 metadata in `pyproject.toml`.
- Keep existing `src/` layout.
- `setuptools` build backend.
- `requires-python = ">=3.10"`.
- Declare **zero runtime dependencies** unless implementation discovers a necessary one.
- Use existing `ai_observe.__version__` (`0.1.0`) as the version source, or keep
  `pyproject.toml` synchronized with it.
- License is **Apache-2.0** declared via PEP 639 (see License section above); add
  `LICENSE` + `NOTICE` files and pin `setuptools>=77` in `[build-system]`.
- Include `src/ai_observe/viewer/static/*` as package data.
- Do **not** include `tests/` in the wheel.
- Preserve the runtime sensitive-data warning on observed-command startup unless quiet
  mode is explicitly enabled.

### CLI / shims (workstream 2)

Install only non-conflicting console scripts by default:

- `ai-observe` → `ai_observe.observe:main_generic`
- `ai-observe-viewer` → `ai_observe.viewer.__main__:main`

Do **not** install these named shim binaries by default (they shadow real tools):
`codex`, `claude`, `gemini`, `opencode`.

Upgrade the checkout `bin/*` shims to **prefer installed-package imports first**, falling
back to adding the checkout `src/` directory to `sys.path` only when the package import is
unavailable — so the same shims work in both installed and source-checkout workflows.

`python -m ai_observe.viewer` must remain supported after installation.

## Stakeholders and needs

- **End users / downstream installers**: want `pip install .` (eventually
  `pipx install ai-observe`) to yield working `ai-observe` and `ai-observe-viewer`
  commands without touching `PYTHONPATH` or `bin/*`.
- **The follow-on SPIR B (CI/docs)**: needs a buildable wheel + sdist and the documented
  entry points, because CI installs the wheel this work produces.
- **Contributors working from a checkout**: must retain the existing `bin/*` shim
  workflow (including the named `claude`/`codex`/`gemini`/`opencode` shims for in-checkout
  use), now resilient to whether the package is installed.
- **The maintainer (license/redistribution)**: needs Apache-2.0 correctly declared so the
  artifact is redistributable and PyPI-ready.

## Goals

1. `ai-observe` is installable from the checkout with standard Python packaging tooling
   (`python -m build`, `pip install .`).
2. The **installed** artifact works **outside** the source checkout, with no
   `PYTHONPATH=src` and no reliance on `bin/*`.
3. Default console scripts are exactly `ai-observe` and `ai-observe-viewer`.
4. The viewer's static assets are packaged and served correctly from an installed wheel
   (the hard criterion: no `/static/*` 404s).
5. `python -m ai_observe.viewer` continues to work after installation.
6. Checkout `bin/*` shims work in both installed and source-checkout workflows.
7. Apache-2.0 is declared via PEP 639 with `LICENSE` + `NOTICE` shipped in both artifacts.
8. Packaging smoke tests prove the above against real built artifacts.
9. Core observation semantics are unchanged; existing tests still pass.

## Non-goals (deferred / out of scope)

- GitHub Actions CI, test-reliability fixes (sleep→poll, umask determinism), README/docs
  rewrite, and the release checklist — all handled in the follow-on issue (SPIR B).
- Periodic snapshot reconciliation (#18).
- Replacing `strace` with fanotify/inotify/eBPF.
- Installing `codex`/`claude`/`gemini`/`opencode` as default entry points; adding an
  `install-shims` command.
- Publishing to PyPI; producing valid **local** release artifacts is sufficient.

## Solution exploration

The high-level decisions are baked (setuptools + `src/` layout + PEP 621/639). The
remaining real design choice is **how the viewer locates its static assets** so they
survive the wheel build/install round-trip. This is the spec's main risk surface.

### Static-asset access: Approach A — keep filesystem reads, fix package data (RECOMMENDED)

Keep `viewer/server.py`'s current `Path(__file__).parent / "static"` + `read_bytes()`
approach, and ensure setuptools ships the static files as package data so they are
installed unpacked next to the module in `site-packages`.

- **Pros**: Minimal code change (packaging config only); setuptools installs wheels
  unpacked, so on-disk `Path(__file__)` reads resolve correctly; lowest semantic-change
  risk.
- **Cons**: Not zip-safe (irrelevant — setuptools wheels install unpacked, not as zipimport);
  relies on `package-data`/`include-package-data` being configured correctly, which is
  exactly the footgun — so it MUST be covered by the hard smoke test.
- **Complexity**: Low. **Risk**: Low-medium (config footgun, fully mitigated by the smoke test).

### Static-asset access: Approach B — switch to `importlib.resources`

Rewrite static serving to read assets via `importlib.resources.files("ai_observe.viewer")
/ "static" / name`.

- **Pros**: The canonical, zip-safe way to access package data; robust across loaders.
- **Cons**: Larger code change to a stable, tested module; still requires the same
  `package-data` declaration to actually ship the files, so it does not remove the footgun
  it only adds robustness this project does not need (setuptools installs unpacked).
- **Complexity**: Medium. **Risk**: Medium (touches tested serving code for no functional
  gain on the supported install path).

**Decision**: Approach A. Keep the existing filesystem-based serving; the work is to
declare the static files as package data correctly and **prove** it with the hard smoke
test. The implementation may include a low-risk `importlib.resources` hardening only if it
is genuinely needed to make an installed wheel serve assets; otherwise it is out of scope
to avoid disturbing tested code.

### Version source: single-source via dynamic version

`pyproject.toml` declares `dynamic = ["version"]` and points setuptools at
`ai_observe.__version__` (`[tool.setuptools.dynamic] version = {attr = "ai_observe.__version__"}`),
so the version is single-sourced from `src/ai_observe/__init__.py`. (Baked decision allows
either dynamic attr or a kept-in-sync literal; dynamic attr is preferred to prevent drift.)

### Shim resilience: try installed import, fall back to checkout `src/`

Each `bin/*` shim attempts `import ai_observe...` first; on `ImportError`, it inserts the
checkout `ROOT/src` onto `sys.path` and retries. This keeps the named in-checkout shims
(`claude`, `codex`, `gemini`, `opencode`) working for contributors while making them
robust to an installed package, and is consistent with the baked CLI decision.

**Known developer caveat (documented, not a code requirement):** because installed imports
are preferred, a contributor who has done a non-editable `pip install .` and then edits
`src/` will have `bin/*` (and the console scripts) run the **stale installed** copy, not
their edits. The answer is the standard one — use an editable install (`pip install -e .`)
or uninstall while iterating. This is normal Python packaging behavior; the spec does not
add an `AI_OBSERVE_DEV` override or checkout-first heuristic to work around it.

## Functional requirements

### Packaging metadata (`pyproject.toml`)

#### MUST

- Use **PEP 621** metadata with the **`setuptools`** build backend and the existing
  `src/` layout.
- Set the distribution/package name explicitly to **`ai-observe`** (`project.name`), the
  import package remaining `ai_observe`.
- Configure setuptools package discovery for the `src/` layout
  (`[tool.setuptools.packages.find] where = ["src"]`) so the package and the dynamic
  version `attr` resolve at build time.
- Set `requires-python = ">=3.10"`.
- Declare **zero runtime dependencies** unless implementation discovers a concrete,
  justified one (justification recorded in the plan/review if so).
- Single-source the version from `ai_observe.__version__` (`0.1.0`) — preferably via
  setuptools `dynamic` version `attr`; otherwise keep `pyproject.toml` literally
  synchronized with it.
- Include project metadata and classifiers (name, description, authors, readme, keywords,
  and Python-version / OS classifiers as appropriate). Linux-first nature SHOULD be
  reflected in classifiers/metadata.
- Configure packaging so `src/ai_observe/viewer/static/*` (all six asset files) ships as
  **package data** in both wheel and sdist.
- Configure packaging so `tests/` is **excluded** from the wheel.

#### SHOULD

- Provide project URLs (homepage/repository) in metadata.
- Use per-file SPDX headers (`# SPDX-License-Identifier: Apache-2.0`) where convenient
  (optional, not required).

### License declaration (PEP 639)

#### MUST

- Add a `LICENSE` file at repo root containing the **full, unmodified Apache-2.0 text**
  with a copyright line (e.g. `Copyright 2025-2026 Mohid Makhdoomi`).
- Add a minimal `NOTICE` file at repo root (one-line attribution acceptable).
- In `pyproject.toml`, declare `license = "Apache-2.0"` (PEP 639 SPDX expression) and
  `license-files = ["LICENSE", "NOTICE"]`.
- Pin `requires = ["setuptools>=77", ...]` in `[build-system]` so the SPDX `license`
  string resolves.
- **Do NOT** also include the legacy
  `License :: OSI Approved :: Apache Software License` classifier.
- Ship both `LICENSE` and `NOTICE` in the wheel **and** the sdist.

### Console scripts / entry points

#### MUST

- Install exactly two default console scripts:
  - `ai-observe` → `ai_observe.observe:main_generic`
  - `ai-observe-viewer` → `ai_observe.viewer.__main__:main`
- **Not** install `codex`, `claude`, `gemini`, or `opencode` as default console scripts.
- Keep `python -m ai_observe.viewer` working after installation.

### Checkout `bin/*` shims

#### MUST

- Upgrade every `bin/*` shim (`ai-observe`, `claude`, `codex`, `gemini`, `opencode`) to
  **prefer the installed-package import** and fall back to inserting the checkout `src/`
  onto `sys.path` only when the package import is unavailable.
- Preserve current shim behavior in a source checkout (no installed package): the named
  shims still observe their respective tools as today.

### Runtime behavior preservation

#### MUST

- Not change core observation semantics.
- Preserve the sensitive-data warning on observed-command startup unless quiet mode
  (`AI_OBSERVE_QUIET`) is explicitly enabled.
- Ensure unsupported platform/backend runtime paths fail with **clear, actionable** errors
  (Linux/`strace` requirement explained) rather than cryptic subprocess/import failures.
  Installation itself MAY succeed on non-Linux where packaging permits.

#### SHOULD

- Keep the installed package importable on non-Linux (so `--help`, the viewer, and
  metadata work cross-platform), deferring the Linux requirement to the live-observation
  runtime path only.

### Viewer static assets (the footgun)

#### MUST

- The installed wheel must serve `/` and HTTP-GETs of `/static/index.html` and
  `/static/style.css` returning the assets, **in a clean venv, installed from the wheel,
  outside the checkout** (the hard acceptance criterion).
- The installed package must expose **all** viewer static files: `index.html`, `index.js`,
  `aggregator.js`, `table.js`, `treemap.js`, `style.css`.

## Packaging smoke tests (workstream 5)

Add smoke coverage for at least the following. These tests build/install **real
artifacts** and exercise the **installed** package (not the checkout `src/`):

#### MUST

- Fresh clone/checkout builds **wheel and sdist** with standard packaging tools
  (`python -m build` or equivalent).
- Fresh virtualenv installs from the **built wheel**.
- Fresh virtualenv installs from the **built sdist** (or validates the sdist build path).
- Installed package works **outside the repo checkout** with no `PYTHONPATH=src` and no
  reliance on `bin/*`.
- `ai-observe --help` (or equivalent usage path) works.
- One small observed command runs successfully **on Linux with `strace` available**.
- Unsupported platform/backend paths fail **clearly** at runtime when applicable. Because
  CI is Linux-only, this is validated by **targeted unit tests that simulate the failure**
  (e.g. monkeypatching `sys.platform` / `shutil.which` so the strace backend raises its
  clear error), not by requiring a native non-Linux runner. The native-failure case is a
  documented manual check, not an automated gate.
- **Shim two-path matrix**: cover **both** shim modes explicitly — (a) the
  installed-package import path (package importable → shim uses it) and (b) the
  checkout-fallback path (package not importable → shim splices `ROOT/src` and still
  works).
- `ai-observe-viewer` starts/serves **without opening a browser** (`--no-browser`).
- `python -m ai_observe.viewer` still works after install.
- Installed viewer serves `/`, `/static/index.js`, and `/static/style.css`.
- Installed package exposes all six viewer static files.
- Wheel/sdist include viewer static assets.
- Wheel does **not** include `tests/`.

#### SHOULD

- Tests that require Linux/`strace` or network sockets are guarded
  (skip with a clear reason) so the suite is honest on platforms/environments that lack
  them, while still failing loudly where the capability is expected (e.g. Linux CI).
- Building artifacts once per test session (fixture-scoped) rather than per-test, to keep
  the suite reasonably fast.
- **Offline / network-isolation robustness**: the test harness may run without PyPI
  access. Build the wheel/sdist in the host environment (where the build backend is
  already present) and install the **built artifact** into the clean venv. When installing
  in an isolated/offline environment, use `pip install --no-build-isolation --no-deps`
  (and/or pre-provision the build backend) so `pip` does not attempt to fetch
  `setuptools>=77` from the network. Tests that genuinely require network SHOULD be guarded
  with a clear skip reason rather than failing opaquely.

## Configuration

No new runtime configuration is introduced. Existing env flags (`AI_OBSERVE_QUIET`,
`AI_OBSERVE_DISABLE`, `AI_OBSERVE_NESTED`, etc.) are unchanged. Packaging configuration
lives in `pyproject.toml`; `MANIFEST.in` is added only if needed to ship sdist files
(`LICENSE`/`NOTICE`/static assets) correctly.

## Non-functional requirements

### Compatibility

- `requires-python >=3.10`.
- Installation permitted on non-Linux where packaging allows; live observation remains
  Linux+`strace` only, gated at runtime with a clear error.
- No reduction in existing test coverage; all ~208 existing tests continue to pass.

### Security and privacy

- The sensitive-data warning remains on by default (only `AI_OBSERVE_QUIET` suppresses
  it).
- No new runtime dependencies (smaller supply-chain surface); any dependency added must be
  justified.
- `git add` is always explicit (never `git add -A`/`.`), per project convention.

## Acceptance criteria

- [ ] `pyproject.toml` uses PEP 621 metadata, `setuptools`, `src/` layout,
      `requires-python >=3.10`, and includes metadata/classifiers.
- [ ] License is **Apache-2.0**, declared via PEP 639: `license = "Apache-2.0"` and
      `license-files = ["LICENSE", "NOTICE"]`, with `setuptools>=77` pinned in
      `[build-system]`; the legacy
      `License :: OSI Approved :: Apache Software License` classifier is **not** present.
- [ ] `LICENSE` (full Apache-2.0 text) and `NOTICE` files exist at repo root and are
      included in both wheel and sdist artifacts.
- [ ] The package declares zero runtime dependencies unless a concrete dependency is
      justified.
- [ ] Default installed console scripts are `ai-observe` and `ai-observe-viewer` only.
- [ ] Named shim binaries (`codex`, `claude`, `gemini`, `opencode`) are **not** installed
      as default console scripts.
- [ ] Checkout `bin/*` shims prefer installed-package imports and fall back to checkout
      `src/` imports only when necessary.
- [ ] A fresh clone can build a wheel and sdist with standard Python packaging tools.
- [ ] A fresh virtualenv can install the built package and run the documented CLI entry
      points.
- [ ] Installed-package smoke tests run **outside** the repo checkout without
      `PYTHONPATH=src`.
- [ ] `python -m ai_observe.viewer` works after installation.
- [ ] **(Hard criterion)** In a clean venv installed from the wheel, **outside the
      checkout**, the viewer serves `/` and HTTP-GETs of `/static/index.html` and
      `/static/style.css` return the assets.
- [ ] Wheel and sdist contain viewer static assets.
- [ ] Wheel does not include `tests/`.
- [ ] Unsupported platform/backend runtime paths fail clearly with actionable errors.
- [ ] Runtime sensitive-data warnings remain enabled by default unless quiet mode is
      explicitly enabled.
- [ ] Existing tests continue to pass.

## Open questions

### Critical

- None. The architect baked the key decisions in issue #20.

### Important

- **Sdist install vs build-path validation**: the issue allows either "fresh virtualenv
  installs from the built sdist" **or** "validates the sdist build path." The plan should
  pick the cheaper-but-sufficient option (install-from-sdist is the stronger guarantee;
  build-path validation is acceptable if install-from-sdist proves too slow/flaky in the
  test harness). Recommendation: install-from-sdist if feasible.
- **Does serving an installed wheel's static assets require any `importlib.resources`
  change at all?** Expected answer: no — setuptools installs wheels unpacked, so the
  existing `Path(__file__)` reads work once `package-data` is declared. The hard smoke
  test is the arbiter; only if it fails does Approach B's hardening come into scope.

### Nice-to-know

- Whether to add project URLs and a richer classifier set now (low effort) vs deferring
  polish to SPIR B. Leaning: include basic URLs/classifiers now since metadata is the
  subject of this work.

## Consultation Log

### Iteration 1 — 3-way spec review (Gemini, Codex, Claude)

- **Gemini — APPROVE (HIGH).** No blocking issues. Recommendations folded in:
  (1) explicitly add `[tool.setuptools.packages.find] where = ["src"]` so the package and
  dynamic version `attr` resolve under the `src/` layout → added to packaging MUSTs;
  (2) offline smoke-test footgun (sdist install may fetch the build backend from PyPI) →
  added an "Offline / network-isolation robustness" SHOULD prescribing host-built artifacts
  and `pip install --no-build-isolation --no-deps`; (3) editable-install developer caveat →
  documented in the shim section.
- **Codex — COMMENT (HIGH).** Strong/implementable; three clarifications, all folded in:
  (1) state the distribution identity explicitly (`project.name = "ai-observe"`) → added to
  packaging MUSTs; (2) specify how "unsupported platform/backend paths fail clearly" is
  tested on a Linux-only lane → smoke-test entry now says targeted simulated unit tests
  (monkeypatch `sys.platform`/`shutil.which`), native non-Linux is a manual check;
  (3) require an explicit two-path shim test matrix (installed path + checkout-fallback
  path) → added as a smoke-test MUST.
- **Claude — APPROVE (HIGH).** Verified every codebase claim in the spec against the actual
  source (version, entry-point signatures, six static assets, five shims, `AI_OBSERVE_QUIET`
  resolution, strace gating, absence of `pyproject.toml`); no contradictions, no technical
  blockers. The one note (stale non-editable install) overlaps Gemini's caveat and is now
  documented.

No reviewer requested a change that alters scope or the baked decisions; all feedback was
clarifying/hardening and has been incorporated above.
