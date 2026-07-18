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
- Plan committed; 3-way review: Claude APPROVE, Gemini COMMENT, Codex REQUEST_CHANGES → all fixed (M4 fake-tool seam, explicit named `excluded` for non-applicable pairs, sealed keep-artifacts `.`-from-root, Phase-1 selftest self-contained).
- Plan APPROVED by architect. Non-blocking note for REVIEW doc: wiring `--selftest` into CI could be a small FUTURE follow-up project (deliberately NOT this one — keep CI surface untouched). Reminder: porch staging lesson — `git add` each phase's new files immediately.

## Implement phase
- Phase 1 DONE: package skeleton (`__init__.py` puts ROOT/src on path, no experiments hack) + graduated `harness.py` (checkout-first `resolve_ai_observe`, in-process `ViewerServer(port=0)` ViewerMonitor, no port constants) + `selftest/selftest_harness.py`.
- Self-test green (4/4): viewer serves fixture 17/17, ephemeral ports distinct+nonzero, checkout-first entrypoint, N1 (no experiments/ on sys.path — behavioral check).
- M2 verified at checkpoint: `ls tests/test_*.py` unchanged vs main; `discover -s tests -p test_*.py` finds 236 tests, 0 from agent_sessions.
- Gotcha logged: N1 source-grep false-positived on docstrings mentioning `sys.path.insert`/`experiments`; switched to behavioral sys.path check.
- Phase 1 iter-1 review: Gemini/Claude APPROVE, Codex REQUEST_CHANGES (start() didn't catch ViewerServer construction failure; collect_events hardcoded host). Both fixed. Iter-2: unanimous APPROVE. Porch swept + advanced to phase_2.

- Phase 2 DONE: `oracle.py` (KnownBug/OPEN_BUGS 32/33/36, CheckResult, known_bug_gate rot-proof, expect_deletion_captured/#32, expect_no_marker_noise/#33, expect_authority_not_overstated/#36, ensure_tool_usable/ToolUnusable) + `__main__.py` (run_suite applicability→explicit named `excluded`, ToolUnusable→loud named fail, resolve_artifact_dir sealed boundary via is_relative_to, --selftest/--json/--keep-artifacts/--tools/--scenarios, keep-artifacts validated BEFORE tool preflight so boundary is tool-independent) + selftest_oracle + selftest_runner.
- `--selftest` green: 28/28. ACs verified: `--tools nope`→exit2 names nope; `--keep-artifacts .`→rejected; default→exit0 "no checks run"; `--json`→[].
- Deviation from plan wording (documented for review): the M4 "stub tool on temp PATH" seam realized as `ensure_tool_usable(SessionResult)` + fake-scenario raising ToolUnusable — keeps --selftest strace/agent-free (universally green) while still exercising the real detection rule + runner rendering.
- Phase 2 force-advanced at porch iteration ceiling (iter 3) after Codex kept surfacing new refinements each round (all addressed): temp-before-preflight, fake-tool seam, applicability ordering, nothing-runnable loud exit. Gemini+Claude APPROVE throughout.

## Phase 3 (single-prompt scenarios S1–S4 + #32)
- Built scenarios/ pkg: check_single_write (S1), check_ephemeral (S2/#32), check_modify (S3), check_subprocess (S4) + selftest_drivers (tool-free argv/registration). 40/40 selftests.
- **SIGNIFICANT PLAN DEVIATION (flagged to architect):** live-agent deletion syscall form is NONDETERMINISTIC run-to-run (claude sometimes emits captured `rm`/plain form, sometimes the annotated `unlinkat(AT_FDCWD<dir>)` that #32 drops). A live-event #32 gate FLAPS between known-bug and "no longer reproduces" FAIL. My rot-proof gate caught this immediately (first run FAIL "flip flag", second run known-bug). Same risk for #33 (codex marker noise volume varies).
  - **Fix:** #32/#33 gates now use DETERMINISTIC parser probes — feed the exact annotated `unlinkat(AT_FDCWD<dir>)` (#32) and `/newroot` mkdir + canonical rmdir (#33) forms through ai-observe's real `trace_parser` and assert, exactly how FINDINGS F1/F2 verified them. Tool-free, rot-proof, no flap. Live scenarios keep agent-actual + viewer HARD checks; the bug GATE no longer rides on agent nondeterminism.
  - Verified at parser level: annotated unlinkat → delete DROPPED (#32 repro); plain → captured. /newroot mkdir dropped + canonical rmdir → unpaired delete (#33 repro).
- LIVE M1 evidence (this worktree, all 3 tools authenticated):
  - claude single_write: 3/3 pass. claude ephemeral: agent-actual pass + #32 known-bug STABLE (rc0).
  - codex single_write: agent-actual + canonical(writes=3) pass + #33 known-bug + viewer(28ev). agy single_write: 3/3 pass.
- All exit 0 (known-bug is not a fail). Whole chain (agent→ai-observe→strace→canonical→in-process ephemeral-port viewer→oracle) works.
