# Phase 2 (resilient shims) iter-1 rebuttal — Project 20

Reviewer verdicts: **Gemini APPROVE**, **Claude APPROVE**, **Codex REQUEST_CHANGES**.

Codex raised two concrete points. I **agree with both** and fixed them; no points contested.

## Codex point 1 — `except ImportError` is too broad

**Agreed.** Catching any `ImportError` would let a *broken/incomplete installed* package
(e.g. `ai_observe` present but `ai_observe.observe` missing, or an import-time bug) silently
fall through to the checkout `src/`, masking the real problem — contrary to the phase intent
of falling back *only when the package is unavailable*.

**Fix:** all five shims now catch `ModuleNotFoundError` and fall back **only when the
top-level package itself is absent**, re-raising anything deeper:

```python
except ModuleNotFoundError as exc:
    if exc.name != "ai_observe":
        raise          # broken/incomplete install — surface it, don't mask it
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from ai_observe.observe import main_generic   # (or main_shim)
```

Verified empirically: a missing top-level package yields `exc.name == "ai_observe"`
(→ fall back), whereas a missing submodule yields `exc.name == "ai_observe.observe"`
(→ re-raise).

## Codex point 2 — fallback tests are not hermetic

**Agreed.** The original fallback tests skipped (in-process) or could import an installed
copy (subprocess) when `ai_observe` was present in the test interpreter, so the two-path
matrix was not reliably proven everywhere (e.g. CI after `pip install .`).

**Fixes in `tests/test_shim_resilience.py`:**
- **In-process fallback** now installs a `sys.meta_path` blocker (`_AiObserveBlocker`) that
  hides `ai_observe*` until the checkout `src/` is on `sys.path`. This *forces* the fallback
  branch deterministically regardless of whether `ai_observe` is installed (the test now
  runs — not skips — in this env, where `ai_observe` is importable). The `skipTest` is gone.
- **Subprocess fallback** now runs the shim under `python -S` (drops site-packages) with no
  `PYTHONPATH` and from outside the checkout, so an installed `ai_observe` cannot satisfy the
  try-branch — the only way the import succeeds is the checkout fallback. The installed-mode
  test deliberately does **not** use `-S` (it relies on `PYTHONPATH=src`).
- **New test** `test_broken_installed_package_is_not_masked_by_fallback` directly asserts
  point 1's behavior: with `ai_observe` importable but `ai_observe.observe` absent, every
  shim re-raises `ModuleNotFoundError(name="ai_observe.observe")` and does **not** prepend
  `src/`.

## Result

213 tests pass (208 existing + 5 shim-resilience). Gemini's and Claude's APPROVE remain
satisfied; both of Codex's points are resolved by the commit landing alongside this rebuttal.
