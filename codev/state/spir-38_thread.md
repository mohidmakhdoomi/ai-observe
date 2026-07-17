# spir-38 — Graduate live-agent testing harness

## Context gathered (specify, iter 1)
- Issue #38: graduate `experiments/1_driving_mechanism/harness.py` into a maintained, opt-in test capability.
- Two experiment rounds proved value (bugs #32, #33, #36 — all confirmed OPEN via `gh issue view`).
- Key infra facts learned:
  - CI main suite globs `ls test_*.py` in `tests/` (top-level only) + explicit module list → a **subdir with non-`test_*.py` filenames is excluded by construction** from CI *and* from `unittest discover` (pattern `test*.py`). This is the strongest no-silent-skip gate.
  - Viewer supports OS-assigned ephemeral ports: `ViewerServer(path, port=0)` exposes `.url`. Existing smoke tests use it in-process. → graduated `ViewerMonitor` should run the server **in-process** with `port=0`, killing the sequential-port-constant problem (req 5) by construction.
  - Round-2 reusable pieces: Exp 4 multi-turn chained driver (`4_multi_turn/multiturn.py`), Exp 9 timeline probe (`9_long_running/incremental.py`), Exp 6 forced-degraded driver (`6_degraded_recovery/degraded.py`, needed to give #36 an oracle home via `AI_OBSERVE_TEST_FAIL_AFTER`).

## Design decisions (going into spec)
- Location: `tests/agent_sessions/` package; live scenario modules NOT named `test_*.py` → excluded by construction.
- No CI job (CI has no authenticated tools); local opt-in command is the deliverable.
- Known-bug oracle: `active` flag per issue; while active, assert the bug signature STILL reproduces (annotated pass, stale-annotation-proof); flip = one-line `active=False` → becomes hard assertion. Never a `@skip`.
- Tool absence/unauth → loud fail naming the tool (req 6); subset = explicit `--tools` narrowing, never silent skip.
- Raw artifacts → temp dir by default (out of git by construction) + gitignore belt-and-suspenders.

## Status
- Initial spec committed. 3-way review iter 1: Gemini/Codex REQUEST_CHANGES, Claude APPROVE.
- Applied all feedback (Decisions 9/10/11 added; 4/7/8 revised):
  - #36 degraded scenario now unconditional v1 scope (was contradictory open question).
  - Decision 8 flipped to checkout-first `bin/ai-observe` (test the working tree).
  - Temp dir must auto-clean; `--keep-artifacts` refuses tracked in-repo paths.
  - Auth-probe failure hints at `--keep-artifacts` to inspect stderr.
  - Runner output = human summary + `--json`; `--scenarios` short-names; driver sequencing (F5) in contract.
- Committing "spec with multi-agent review", then porch next (likely spec-approval gate → STOP for human).

## Plan phase (iter 1)
- Spec APPROVED by architect (verified the 3 load-bearing claims independently). Plan-phase note: `tests/` has no `__init__.py` → `python -m tests.agent_sessions` relies on PEP 420 namespace resolution from repo root — verify early + document run-from-root.
- Verified in this worktree: claude/agy/codex/strace ALL present; PEP 420 `python -m tests.agent_sessions` works from repo root (empirically). ptrace_scope=1.
- Key plan refinement locking M2 literally: ALL new test code (incl. tool-free plumbing/oracle checks) lives under `tests/agent_sessions/` as `selftest_*.py`/`check_*.py` — nothing matches CI's `ls test_*.py` glob → CI-collected set byte-identical. Tool-free tier runs via `--selftest`; live tier is opt-in.
- 6 phases: (1) harness+ephemeral-port ViewerMonitor, (2) oracle+registry+runner/gating, (3) S1–S4 + #32, (4) fold Exp4 multi-turn + Exp9 timeline + #33, (5) Exp6 degraded + #36, (6) docs/gitignore/README/sweep.
- Plan committed; running 3-way plan review.
