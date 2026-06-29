### Iteration 1 Reviews
- gemini: COMMENT — Phase 3 implementation is robust, complete, and all smoke tests pass; provided two minor suggestions to improve test run cleanliness and developer experience.
- codex: REQUEST_CHANGES — Phase 3 adds strong installed-artifact coverage, but it misses the real-install shim matrix promised for this phase and weakens the sdist install check with `--system-site-packages`.
- claude: APPROVE — Phase 3 smoke tests are thorough, well-structured, and cover every MUST item from the spec including the hard static-asset criterion.

### Builder Response to Iteration 1
# Phase 3 (packaging smoke tests) iter-1 rebuttal — Project 20

Reviewer verdicts: **Claude APPROVE**, **Gemini COMMENT**, **Codex REQUEST_CHANGES**.

I **agree with all four points** (Codex ×2 blocking, Gemini ×2 advisory) and fixed each; no
points contested.

## Codex point 1 — phase_3 shim two-path matrix not proven against a REAL install

**Agreed.** Phase 2's shim matrix simulates "installed" via `PYTHONPATH=src`, not an actual
wheel/venv install. Phase 3 explicitly called for proving the shim paths against the
installed artifact.

**Fix:** added `InstalledShimMatrixTests` to `tests/test_packaging_smoke.py`, which runs the
checkout `bin/*` shims **with the venv interpreter that has the wheel installed**, from
outside the checkout, using the `AI_OBSERVE_DISABLE` + `AI_OBSERVE_REAL_*` + marker idiom:
- `test_installed_interpreter_uses_installed_package`: venv interpreter (real installed
  package) → the try-branch dispatches.
- `test_isolated_interpreter_falls_back_to_checkout`: `venv/bin/python -S …` hides the venv
  site-packages, so the import can only succeed via the checkout `src/` fallback.

Both assert all five shims dispatch (exit 0, marker written, no `ModuleNotFoundError`).

## Codex point 2 — sdist install used `--system-site-packages`, masking requirements

**Agreed.** `--system-site-packages` leaned on ambient host packages and didn't match the
documented phase recipe (pre-provision the backend in a *clean* venv).

**Fix:** `test_install_from_sdist_best_effort` now creates a **clean** venv (no
`--system-site-packages`), **pre-provisions** the backend with
`pip install "setuptools>=77" wheel`, then installs with
`pip install --no-build-isolation --no-index --no-deps <sdist>`. It skips clearly if the
backend can't be provisioned (offline CI), and now also asserts the import resolves to the
**venv** (not the checkout `src/`). Verified: it runs (not skips) in this environment.

## Gemini point 1 (advisory) — `sys.executable` may lack setuptools

**Agreed.** Modern venvs ship without setuptools, so running the suite from such a venv would
have errored in `setUpModule`.

**Fix:** `setUpModule` now probes `import setuptools` in `sys.executable` and raises
`unittest.SkipTest` with a clear message if it's absent — the suite skips cleanly instead of
erroring. (The porch check uses the system `python3`, which has setuptools, so it runs there.)

## Gemini point 2 (advisory) — `HTTPError` left unclosed (ResourceWarning)

**Agreed.** Fixed: `_ViewerProc.get` now calls `exc.close()` on the `HTTPError` (in a
`finally`). (The `_ViewerProc.stop` pipe-close cleanup landed earlier in this phase.)

## Result

234 tests pass (213 + 21 packaging smoke). Claude's APPROVE and Gemini's COMMENT remain
satisfied; both of Codex's blocking points are resolved by the commit landing with this
rebuttal.


### IMPORTANT: Stateful Review Context
This is NOT the first review iteration. Previous reviewers raised concerns and the builder has responded.
Before re-raising a previous concern:
1. Check if the builder has already addressed it in code
2. If the builder disputes a concern with evidence, verify the claim against actual project files before insisting
3. Do not re-raise concerns that have been explained as false positives with valid justification
4. Check package.json and config files for version numbers before flagging missing configuration
