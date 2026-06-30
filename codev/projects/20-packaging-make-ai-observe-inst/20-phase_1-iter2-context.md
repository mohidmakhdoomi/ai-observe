### Iteration 1 Reviews
- gemini: REQUEST_CHANGES — Phase 1 files (pyproject.toml, LICENSE, NOTICE) are implemented correctly on disk, but they are untracked in git and must be explicitly added to the change scope.
- codex: REQUEST_CHANGES — Phase 1 is not reviewable as implemented in the scoped PR files because the actual packaging deliverables (`pyproject.toml`, `LICENSE`, `NOTICE`, etc.) are absent from the changed file list.
- claude: APPROVE — Phase 1 deliverables are complete and correct — pyproject.toml, LICENSE, NOTICE all verified against spec with a successful wheel build proving all criteria (static assets packaged, tests excluded, PEP 639 license resolved, correct entry points).

### Builder Response to Iteration 1
# Phase 1 (packaging metadata + license) iter-1 rebuttal — Project 20

Reviewer verdicts: **Claude APPROVE**, **Gemini REQUEST_CHANGES**, **Codex REQUEST_CHANGES**.

Gemini and Codex requested changes for the **same single reason**: the Phase 1 deliverables
(`pyproject.toml`, `LICENSE`, `NOTICE`) were **untracked in git** at review time, so they
fell outside the reviewed change scope (the git diff). Codex additionally noted the
resulting mismatch between `status.yaml`/thread (which said the files were created and built)
and the empty implementation diff. **Neither contested the content** — Claude reviewed the
working-tree files directly and APPROVED them as complete and correct.

## Resolution

**Agreed — this was a staging gap, not a content gap.** The files existed and built
correctly on disk (Claude verified; I verified via a successful wheel+sdist build), but I
had not yet committed them, so the diff-scoped reviewers saw only docs. Fixed by committing
the deliverables:

```
0477f54 [Spec 20][Phase: packaging-metadata] feat: add pyproject, LICENSE, NOTICE
```

The commit contains exactly the Phase 1 deliverables: `pyproject.toml`, `LICENSE`, `NOTICE`.
This resolves both Gemini's and Codex's issue and the status/diff mismatch Codex flagged.

## Content evidence (already APPROVED by Claude, re-stated for the diff-scoped reviewers)

Built wheel + sdist via the PEP 517 backend (`setuptools.build_meta`, offline; the `build`
frontend is absent in this PEP 668 externally-managed env). Verified against Phase 1
acceptance criteria:

- **PEP 621/639**: `License-Expression: Apache-2.0`, `License-File: LICENSE` + `NOTICE`,
  **no** legacy `License :: OSI Approved :: Apache Software License` classifier;
  `setuptools>=77` pinned in `[build-system]` and the SPDX license resolved without error.
- **Console scripts**: `entry_points.txt` lists exactly `ai-observe` and `ai-observe-viewer`
  (named shims `claude`/`codex`/`gemini`/`opencode` are **not** entry points).
- **Version**: dynamic `attr = "ai_observe.__version__"` resolved to `0.1.0` (single-source).
- **Package data**: wheel ships all six `ai_observe/viewer/static/*` assets.
- **`tests/` exclusion**: wheel contains **no** `tests/` path (sdist does ship `tests/`,
  which is allowed — the spec requires only the **wheel** to exclude tests).
- **License shipping**: both wheel (`*.dist-info/licenses/`) and sdist (root) include
  `LICENSE` + `NOTICE`.
- **`requires-python`**: `>=3.10`.
- **Regression**: all 208 existing tests pass.
- **No `MANIFEST.in` needed** — `license-files` + `package-data` already ship everything.

## Note (non-blocking)

setuptools' sdist `check` emits a cosmetic `should have one of README, README.*` warning
because there is no root README. This is expected and harmless: the long-description IS
populated from `docs/observe.md` (verified in METADATA/PKG-INFO), per the plan's
`readme = {file = "docs/observe.md"}` decision (SPIR B owns the docs rewrite; a stub README
is the documented fallback, intentionally not added here).


### IMPORTANT: Stateful Review Context
This is NOT the first review iteration. Previous reviewers raised concerns and the builder has responded.
Before re-raising a previous concern:
1. Check if the builder has already addressed it in code
2. If the builder disputes a concern with evidence, verify the claim against actual project files before insisting
3. Do not re-raise concerns that have been explained as false positives with valid justification
4. Check package.json and config files for version numbers before flagging missing configuration
