# Review: graduate-live-agent-testing-ha

## Summary

Graduated the round-1/2 live-agent testing harness from `experiments/1_driving_mechanism/harness.py`
into a **maintained, opt-in test capability** at `tests/agent_sessions/`. The suite drives
real coding agents (claude, agy, codex) under `ai-observe` and asserts a **three-view
oracle** — agent-actual files vs. the canonical `.jsonl` vs. what the browser viewer serves.
It is excluded from the default CI matrix **by construction** (its files are named
`check_*.py`/`selftest_*.py`, never `test_*.py`), with a tool-free `--selftest` tier that
runs anywhere and a live tier that a developer with authenticated tools opts into.

Delivered across six phases:
1. Core harness (`harness.py`): checkout-first `resolve_ai_observe`, in-process
   `ViewerMonitor` on an OS-assigned **ephemeral port** (no port constants), F5/Decision-11
   sequencing.
2. Oracle + rot-proof known-bug registry (`oracle.py`) + opt-in runner (`__main__.py`) with
   loud-named tool-usability gating and a sealed `--keep-artifacts` boundary.
3. Single-prompt scenarios S1–S4 + the **#32** flip-home.
4. Round-2 drivers folded in: multi-turn chained driver (`drivers.py`, S5) + timeline probe
   (`probes.py`, S6) + the **#33** flip-home.
5. Degraded parse-failure scenario (`check_degraded.py`, S7) + the **#36** flip-home,
   completing M3 for all three open bugs.
6. Docs (`docs/agent-sessions.md`), artifact `.gitignore`, README pointer, acceptance sweep.

## Spec Compliance

Functional (MUST):
- [x] **M1** — A single command (`python -m tests.agent_sessions`) runs the suite and yields
  pass/fail with oracle-backed assertions. Live-verified per scenario across phases (see
  Live Evidence) and by the capstone full sweep.
- [x] **M2** — Default CI matrix **provably unchanged**: `tests/test_*.py` set is byte-identical
  main-vs-HEAD; `unittest discover -s tests -p test_*.py` = 236 tests, **zero skips**; no new
  job/leg. Suite lives under `tests/agent_sessions/` as non-`test_*.py` files.
- [x] **M3** — #32/#33/#36 each recorded as `known-bug:#N`; flip is a one-line
  `OPEN_BUGS[N].active = False`. Each has a dedicated flip-home scenario (ephemeral/#32,
  multi_turn/#33, degraded/#36) and rot-proof self-tests.
- [x] **M4** — A missing or unauthenticated requested tool is a **loud, named** failure
  (`ToolUnusable` → nonzero exit naming the tool); never a silent skip. Self-tested via the
  fake-tool seam.
- [x] **M5** — Multi-turn chained driver (Exp 4) and timeline probe (Exp 9) folded in
  alongside `run_observed_session`/`ViewerMonitor`.
- [x] **M6** — Viewer uses OS-assigned ephemeral ports (`ViewerServer(port=0)`); no sequential
  port constants remain; parallel runs cannot collide by construction.

Non-functional (SHOULD):
- [x] **N1** — Stdlib-only; no `sys.path.insert` into `experiments/` (imports `ai_observe` via
  the `src/`-on-path convention). Verified behaviorally in the phase-1 self-test.
- [x] **N2** — Raw artifacts never enter git: auto-cleaning temp dir by default; the sealed
  `--keep-artifacts` boundary refuses tracked in-repo destinations; `tests/agent_sessions/.gitignore`
  ignores `.artifacts/` + raw artifact/log files (proven via `git check-ignore`).
- [x] **N3** — `docs/agent-sessions.md` covers per-tool prereqs + auth, the one command + all
  flags, repo-root/PEP-420 note, `--dangerously-skip-permissions` implications + throwaway
  workdirs, the known-bug flip howto, and the **F5** and **F7** notes.

Immutability:
- [x] **I1** — `experiments/` untouched: `git status --porcelain experiments/` is empty across
  the whole branch.

## Deviations from Plan

- **Phase 3 (endorsed by architect) — #32/#33 gates moved to deterministic parser probes.**
  The plan implied gating #32/#33 on live-agent runs. In practice the deletion syscall form
  (#32) and the codex `/newroot` marker-probe volume (#33) are **nondeterministic run-to-run**,
  so a live-event gate flaps between `known-bug` and a "no longer reproduces" FAIL. The
  rot-proof gate caught this on the first run. Fix: reproduce each bug deterministically by
  feeding the exact syscall forms through ai-observe's real `trace_parser` (exactly how
  FINDINGS F1/F2 verified them). The live scenarios keep agent-actual + viewer as HARD checks;
  only the bug **gate** stopped riding on agent nondeterminism. A non-gating `INFO` record
  retains the live deletion evidence. Architect endorsed with three follow-ups, all completed.
- **Phase 5 — additive `extra_env` param on `run_observed_command` (touches the phase-1
  harness).** Rather than porting Exp 6's standalone subprocess logic into the scenario, the
  degraded scenario reuses the run core + M4 gate + event-load + file-list by passing
  `AI_OBSERVE_TEST_FAIL_AFTER` through a new optional `extra_env`. Backward-compatible; the
  scenario reads the full `.meta.json` separately for the #36 authority fields (the
  `SessionResult.meta` only carries warnings + a stderr tail). All three phase-5 reviewers
  approved unanimously.

## Lessons Learned

### What Went Well
- The **rot-proof known-bug gate** paid for itself immediately: it turned Phase 3's live
  nondeterminism into a hard, early signal instead of a latent flaky test, forcing the
  deterministic-probe pivot before it could rot.
- **Reusing `run_observed_command` as the shared run core** kept the multi-turn, timeline, and
  degraded scenarios thin; the `extra_env` seam was a one-line addition.
- **M2 was checkable, not aspirational**: diffing the `test_*.py` set against `main` and
  counting discovered tests made "CI matrix unchanged" a mechanical proof, not a claim.
- Per-phase **live verification** (not just self-tests) caught real behavior each phase and
  made the final review low-risk.

### Challenges Encountered
- **Live-agent nondeterminism** (Phase 3): resolved by moving bug gates to deterministic
  reproductions through the real parser (see Deviations).
- **Non-blocking probe couldn't reuse `capture_output`** (Phase 4, codex iter-2): the timeline
  probe `DEVNULL`-ed wrapper stderr, so the runner's "rerun with `--keep-artifacts` to inspect
  stderr" message was hollow. Resolved by redirecting the Popen streams to logs in the scenario
  `outdir` and inlining the stderr tail into the `ToolUnusable` failure.
- **Codex's iterative strictness** (phases 2/3/4 each hit the iteration-3 safety ceiling): each
  round surfaced a *new*, legitimate refinement (temp-before-preflight, exact argv pins,
  stderr persistence). All were addressed; the force-advances were on genuinely-approved-by-two
  work with a preserved rebuttal audit trail.

### What Would Be Done Differently
- Anticipate live-agent nondeterminism at **plan** time — the deterministic-probe strategy for
  bug gates could have been baked into the plan rather than discovered in Phase 3.
- State the "exact chained-shell string per tool" self-test bar uniformly up front, so the agy
  test wouldn't have shipped with `assertIn` and drawn a codex iter-3 REQUEST_CHANGES.

### Methodology Improvements
- The porch **iteration-3 safety ceiling** worked as intended for a strict reviewer that keeps
  finding small, real improvements: two approvals + a preserved rebuttal let the project
  progress without discarding codex's (addressed) input. No protocol change proposed.

## Architecture Updates

Routed one **COLD** (`arch.md`) entry; no HOT change.
- **`arch.md` → Continuous integration**: added a bullet documenting the opt-in live-agent
  suite at `tests/agent_sessions/` — that it is excluded from the CI-collected set by naming
  (`check_*`/`selftest_*` vs. the `test_*.py` glob), the three-view oracle, the tool-free vs.
  live tiers, and the one-line known-bug flip. This is reference detail about a subsystem's
  location/mechanism → cold.
- **No HOT (`arch-critical.md`) change**: the existing hot fact ("CI fails loud on ANY unittest
  skip") already governs the invariant this suite respects; the suite itself is reference
  detail, below the behavior-changing + cross-cutting bar for the capped hot tier. No
  displacement needed.

## Lessons Learned Updates

Routed one **COLD** (`lessons-learned.md`) entry; no HOT change.
- **`lessons-learned.md` → "Gate known bugs on deterministic reproductions, not live-agent
  nondeterminism"**: captures the Phase 3 pivot — don't gate a known-bug annotation on a live
  trigger whose form varies run-to-run; reproduce it deterministically through the real
  underlying component, pair it with a rot-proof both-directions gate, keep it an assertion
  path (never `unittest.skip`), and retain the noisy live signal as a non-gating `INFO` record.
- **No HOT (`lessons-critical.md`) change**: this is a testing-pattern recipe (spec-narrow to
  regression-gating), which belongs in the cold archive, not the always-on hot file. The hot
  lesson tier is reserved for broader cross-cutting build rules and is at/near cap.

## Technical Debt
- None introduced. The three known-bug gates are deliberate, tracked, and flip with a one-line
  change when each upstream fix lands.

## Consultation Feedback

### Specify Phase (Round 1)
Verdicts: Gemini REQUEST_CHANGES, Codex REQUEST_CHANGES, Claude APPROVE (all HIGH). Every
substantive point accepted.
#### Gemini
- **Concern**: CLI resolution should be checkout-first (a test suite must observe the tree it
  imports from). **Addressed**: Decision 8 flipped to checkout-first, installed script only as
  fallback.
- **Concern**: `mkdtemp` leaks temp dirs. **Addressed**: Decision 7 mandates an auto-cleaning
  temp dir; `mkdtemp` rejected.
- **Concern**: #36 scope; auth-probe could mask crashes. **Addressed**: Decision 9 (#36 in
  scope); Decision 4 failure message hints `--keep-artifacts`.
#### Codex
- **Concern**: #36 in-scope-vs-deferred contradiction. **Addressed**: Decision 9 makes S7
  unconditional in v1.
- **Concern**: `--keep-artifacts` could point at a tracked subtree. **Addressed**: Decision 7
  refuses tracked in-repo destinations by construction.
#### Claude
- APPROVE. Minor suggestions folded in as Decisions 10 (stderr summary + `--json`; `--scenarios`
  short names) and 11 (start-session-then-attach-viewer sequencing).

### Plan Phase (Round 1)
Verdicts: Gemini COMMENT, Codex REQUEST_CHANGES, Claude APPROVE (all HIGH).
#### Codex
- **Concern**: M4 present-but-unusable branch untested. **Addressed**: Phase 2 fake-tool seam
  self-tests the `ToolUnusable` → loud-fail path with no real agent.
- **Concern**: requested-but-non-applicable tool/scenario pairs underspecified. **Addressed**:
  explicit named `excluded` CheckResult (distinct from `fail`), self-tested.
#### Gemini
- **Concern**: Phase-1 AC depended on Phase-2 `__main__`; `--keep-artifacts .`-from-root bypass.
  **Addressed**: phase-1 self-test runs standalone; boundary sealed via `is_relative_to` (equal
  path included), with `.`-from-root and symlink cases self-tested.
#### Claude
- APPROVE. `selftest/__init__.py`, repo-root cwd inheritance, codex argv pin — all folded in.

### Implement Phase 1
Verdicts: Gemini APPROVE, Codex REQUEST_CHANGES, Claude APPROVE (→ iter 2 unanimous APPROVE).
#### Codex
- **Concern**: `ViewerMonitor.start()` didn't catch `ViewerServer(...)` construction failure.
  **Addressed**: construction moved inside the `try`, normalizing to the boolean API.
- **Concern**: `collect_events()` hardcoded `127.0.0.1`. **Addressed**: host/port now via
  `urlsplit(self.url)`.

### Implement Phase 2
Verdicts: Gemini/Claude APPROVE throughout; Codex REQUEST_CHANGES ×3 (force-advanced at
iter-3 ceiling; two approvals + preserved rebuttal). All codex points addressed:
temp-dir-allocated-after-preflight (boundary independent of temp writability), fake-tool
`ToolUnusable` seam, applicability ordering, and a loud `EXIT_NOTHING_RUNNABLE` (3) so a
zero-check run is never a silent green.

### Implement Phase 3
Verdicts: Gemini/Claude APPROVE throughout; Codex REQUEST_CHANGES ×3 (force-advanced at
iter-3). **Addressed**: S1 exact-content match, viewer==canonical completeness on all four
scenarios, S2 create-captured-live, S3 seed-survival. The nondeterminism pivot to
deterministic parser probes (endorsed by the architect) landed here.

### Implement Phase 4
Verdicts: Gemini/Claude APPROVE throughout; Codex REQUEST_CHANGES on iters 1–3 (force-advanced
at iter-3). **Addressed**: (iter1) S6 completeness tightened to equality + timeline M4 gate;
(iter2) timeline probe now persists wrapper stderr to the scenario `outdir` and inlines the
stderr tail into the `ToolUnusable` failure (Decision-4 debuggability); (iter3) agy multi-turn
self-test now pins the **exact** chained-shell string (parity with claude/codex). Rebuttals
preserved as audit trail.

### Implement Phase 5
Verdicts: **Gemini/Codex/Claude all APPROVE (HIGH), zero key issues** — first clean
first-iteration pass. Codex confirmed `selftest_degraded` passes. No concerns raised.

### Implement Phase 6
Verdicts: **Gemini/Codex/Claude all APPROVE (HIGH), zero key issues.** No concerns raised —
all consultations approved the docs, `.gitignore`, README pointer, and acceptance sweep.

### PR-level review (Review phase, iter 1)
Verdicts: Gemini APPROVE, Claude APPROVE (both re-ran the suites; zero issues), Codex
REQUEST_CHANGES (branch hygiene). **Addressed**: removed a stray transient porch context file
(clean tree). **Rebutted**: the `chore(porch):` commit messages are porch's own orchestration
commits (strict mode forbids rewriting; the branch squash-merges to one clean `[Spec 38]`
commit). **N/A**: codex could not run the live tier in its sandbox (no writable temp) — an
environment limit, verified green locally + by two reviewers. See `38-review-iter1-rebuttals.md`.

### Architect integration review (PR #39) — REQUEST_CHANGES, all addressed
The architect ran a full 3-way CMAP **plus live verification from the product side** and
found three harness bugs the PR-level consults missed because they never ran codex live:
- **Item 1 — codex `--skip-git-repo-check`.** *Addressed.* `codex exec` refuses a non-git
  temp workdir; added the flag at both invocation sites (`harness._codex_cmd`,
  `drivers.chain_for`), updated the exact-string argv self-tests, and corrected the M4
  narrative here and in the PR description. Verified by a green codex-only live sweep
  (17 pass, 3 known-bug:#33, exit 0).
- **Item 2 — failure path hid the evidence.** *Addressed.* Generalized the phase-4 iter-2
  timeline fix to the session path: `run_observed_command` now persists agent stdout/stderr to
  the session outdir (so the `--keep-artifacts` hint is true), and `ensure_tool_usable` folds
  the `stderr_tail` into the `ToolUnusable` detail so the reason shows inline. `check_timeline`
  now uses that same generic mechanism. Tool-free self-tests added.
- **Item 3 — timeout could orphan the agent process tree.** *Addressed.* Both
  `run_observed_command` (now `Popen` + `communicate(timeout)`) and `sample_timeline` launch
  with `start_new_session=True` and tear down via a shared `terminate_process_group` helper
  that `os.killpg`s the whole group (SIGTERM→SIGKILL), mirroring the product's
  `ai_observe.observe.wait_for_process`. Tool-free self-tests added (leader + grandchild).

Non-blocking notes recorded as Follow-up Items (atomic bug-flip in fix PRs; `--selftest`-in-CI
follow-up; the cosmetic `collect_events` `Host:` header).

## Flaky Tests
No flaky tests encountered. (The deterministic-probe pivot in Phase 3 specifically eliminated a
would-be flaky live-agent gate before it could land.)

## Live Evidence (M1)

Per-phase live verification (this worktree, tools authenticated):
- **S1 single_write**: claude/agy 3/3 pass; codex agent-actual + canonical(writes=3) +
  known-bug:#33 + viewer(28ev).
- **S2 ephemeral (#32)**: claude agent-actual pass + `known-bug:#32` stable, rc0.
- **S5 multi_turn**: claude 6/6 (turn-2/turn-3 later-turn writes captured, continuity, viewer 8/8).
- **S6 timeline**: claude 10 distinct increasing ticks during run + final 36/36 complete, rc0.
- **S7 degraded (#36)**: claude 5 agent-actual pass (d1–d5) + `known-bug:#36` with live
  signature `parser_status='parser_failure_partial' authority_overstated=True`, rc0.

Capstone full-suite sweep (`python -m tests.agent_sessions`, initial run): **pass=55,
known-bug=3 (#32×2, #36×1), info=2, fail=3** across 63 checks — claude (all 7 scenarios) and
agy (all 5) fully green; the 3 `fail`s were all codex.

**Root-cause correction (architect integration review, item 1).** The initial diagnosis of
the codex fails as "unauthenticated" was **wrong**. The architect reproduced them *fully
logged in*: `codex exec` refuses a non-git working directory with
`Not inside a trusted directory and --skip-git-repo-check was not specified`, and the
scenario workdirs are throwaway temp dirs. It was a **harness bug** — a missing
`--skip-git-repo-check` flag — that M4 correctly caught as a loud, named failure (the M4
mechanism worked; only its *narrative* was misattributed). Fixed at both invocation sites
(`_codex_cmd`, `chain_for`) with argv self-tests pinning the flag.

**Post-fix codex-only sweep** (`python -m tests.agent_sessions --tools codex`): **pass=17,
known-bug:#33=3, excluded=4, exit 0** — `single_write`, `subprocess`, and `multi_turn` all
green with the expected `known-bug:#33` annotations, viewer completeness 37/37, 15/15, 88/88.
Combined with the claude+agy capstone, the suite now demonstrates M1 (single-command
oracle-backed pass/fail), the three known-bug flip-homes (M3), and M4 (loud named
tool-unusable failure) across **all three** tools.

## Follow-up Items
- When each upstream fix merges, flip its gate: `OPEN_BUGS[32|33|36].active = False` in
  `tests/agent_sessions/oracle.py` (one line each; the rot-proof self-tests then enforce the
  corrected behavior).
- Optional future project (deliberately **not** this one): wire the tool-free `--selftest` tier
  into CI as its own step — keeps the live tier out while gaining plumbing/oracle coverage.
