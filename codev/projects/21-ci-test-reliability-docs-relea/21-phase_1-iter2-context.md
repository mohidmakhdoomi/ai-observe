### Iteration 1 Reviews
- gemini: REQUEST_CHANGES — Implementation is correct but tests/_util.py is missing from the commit scope
- codex: REQUEST_CHANGES — Phase 1 logic looks aligned, but the shared helper it now depends on is not actually in the tracked PR scope, so the phase is not complete/reviewable as-shipped.
- claude: APPROVE — Phase 1 fully delivers all planned items: shared poll helper consolidates two existing helpers, all convertible sleep sites use bounded polling, umask assertion is deterministic, intentional sleeps are clearly documented, and all tests pass under both umask 022 and 077.

### Builder Response to Iteration 1
# Rebuttal — phase_1 iteration 1

## Gemini (REQUEST_CHANGES) + Codex (REQUEST_CHANGES): `tests/_util.py` untracked

Both reviewers raised the identical, single blocking issue: the new shared helper
`tests/_util.py` — which the converted tests import — was untracked (`?? tests/_util.py`)
at review time, so the canonical PR file list omitted it and the committed diff would
have broken the suite with an `ImportError`.

**Accepted and fixed.** The file is now staged (`git add tests/_util.py`; `git status`
shows `A  tests/_util.py`), so it is part of the phase's commit scope and will be
included in porch's phase commit. No code change was needed — the file existed and the
suite passed locally precisely because it was present in the working tree; only the
staging was missing.

Both reviewers confirmed everything else in the phase is correct: sleep→poll
conversions, documented intentional fixed sleeps, and the umask fix (drop
test-created-dir `0o755` assertion, keep product-set `0o600` assertions).

## Claude (APPROVE)

No issues raised (KEY_ISSUES: None). Concurs the phase delivers all planned items and
that the suite passes under both `umask 022` and `umask 077`.


### IMPORTANT: Stateful Review Context
This is NOT the first review iteration. Previous reviewers raised concerns and the builder has responded.
Before re-raising a previous concern:
1. Check if the builder has already addressed it in code
2. If the builder disputes a concern with evidence, verify the claim against actual project files before insisting
3. Do not re-raise concerns that have been explained as false positives with valid justification
4. Check package.json and config files for version numbers before flagging missing configuration
