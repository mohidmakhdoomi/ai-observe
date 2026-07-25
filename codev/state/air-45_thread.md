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

## PR phase

- Full suite re-run green: 266 tests, OK, zero skips (porch check also green).
- Built `ai_observe-0.2.0` wheel + sdist; wheel contents verified (static assets in, tests/ out,
  LICENSE/NOTICE in dist-info, no strays).
- Clean-venv install outside checkout → imports 0.2.0 from the venv; e2e observed session produced
  direct strace events + snapshot event + `.meta.json`; viewer served `/` and all 5 static assets (200).
- PR #46 opened with the AIR review in the body (release checklist results included).
- `porch gate 45`: pr gate requested — waiting for human approval.
- Remaining after merge: tag `v0.2.0` on the release commit, publish the GitHub release with notes.
