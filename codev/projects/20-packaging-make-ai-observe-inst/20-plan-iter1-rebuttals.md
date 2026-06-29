# Plan iter-1 rebuttal — Project 20

Reviewer verdicts: **Gemini APPROVE**, **Claude APPROVE**, **Codex REQUEST_CHANGES**.

Codex raised two concrete points. I **agree with both** and revised the plan; no
disagreement. Claude (APPROVE) added two non-blocking notes, also addressed.

## Codex point 1 — `readme` declared but no root `README*` exists

**Agreed.** Verified: the repo has no `README*` at root (only `docs/observe.md` and
`docs/viewer.md`). The original Phase 1 listed `readme` without naming a source — underspecified.

**Change made (Phase 1):** `readme` now points at the existing, accurate `docs/observe.md`
via `readme = {file = "docs/observe.md", content-type = "text/markdown"}`. Rationale: a
README/docs **rewrite** is an explicit **SPIR-B non-goal**, so creating a stub root README
here would be scope creep that SPIR B then rewrites. `docs/observe.md` already states the
product promise accurately; setuptools embeds it as the long-description and auto-includes
the referenced file in the sdist, so rebuild-from-sdist stays valid. Documented fallback: a
minimal root `README.md` if pointing at `docs/observe.md` proves problematic at build time.

## Codex point 2 — sdist `--no-build-isolation` install needs the backend pre-provisioned

**Agreed.** Installing an sdist triggers a build, so with `--no-build-isolation` the fresh
venv must already contain the build backend. The original plan hinted at this but was not
executable.

**Change made (Phase 3):** added concrete install recipes:
- **Wheel** (primary; carries the hard static-asset criterion): wheels need no build backend,
  so `pip install --no-deps <wheel>` installs with **no network** (zero runtime deps →
  `--no-deps` is safe).
- **Sdist** (preferred per architect): in the fresh venv, first
  `pip install "setuptools>=77" wheel` to pre-provision the backend, then
  `pip install --no-build-isolation --no-deps <sdist>`. The sdist test is **guarded** —
  skip with a clear reason if the backend can't be provisioned offline.
- **Fallback** if install-from-sdist is infeasible in the harness: validate the sdist
  **build path** (unpack, assert `pyproject.toml`/`LICENSE`/`NOTICE`/six static assets,
  optionally `python -m build` from the unpacked tree).

## Claude (APPROVE) — non-blocking notes

- **Shim test file naming:** named it `tests/test_shim_resilience.py` and noted that the
  `__main__`-guarded shims are exercised via `subprocess`/`runpy`.
- **`MANIFEST.in` fallback:** already anticipated in Phase 1 (add `include LICENSE NOTICE`
  only if an sdist inspection shows `LICENSE`/`NOTICE` missing).

## Summary

All REQUEST_CHANGES items are resolved by plan edits (no points contested). The changes are
clarifying/hardening only — they do not alter the phase decomposition, scope, or any baked
decision.
