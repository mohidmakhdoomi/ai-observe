# air-45 thread — Publish GitHub release v0.2.0 (issue #45)

Builder: air-45 (AIR, strict mode). Issue #45: cut a release covering everything since v0.1.0.

## Implement phase

- Architect decision (2026-07-25): version is **0.2.0** (new features + bugfixes since 0.1.0).
- Bumped `__version__` in `src/ai_observe/__init__.py` (single source; pyproject reads dynamically).
- Added `[0.2.0] - 2026-07-25` section to `CHANGELOG.md` (Added / Fixed / Testing-and-maintenance)
  covering PRs #28, #39, #42, #40, #41, #44, #30, #34, #37, plus the release-tag link.
- First full test run: 266 tests, 2 failures — both were the packaging smoke tests pinning
  `version == "0.1.0"` (`tests/test_packaging_smoke.py`, sdist install + import-outside-checkout
  assertions). These pin the release version by design; bumped both to "0.2.0". Re-running suite.
- Release checklist of record: `RELEASING.md` (mirrors the v0.1.0 process, Air #25 / PR #26).
  Build tooling provisioned in `/tmp/aio-build-venv` (system pip is PEP 668-managed).

## Plan for the rest

1. Full suite green with zero skips.
2. `python3 -m build` → inspect wheel/sdist contents (static assets, LICENSE/NOTICE, no tests/, no strays).
3. Clean-venv install outside checkout → one e2e observed session → viewer static-asset smoke.
4. Commit, PR with review in body, notify architect.
5. After merge: tag `v0.2.0` on the release commit and publish the GitHub release with notes.
