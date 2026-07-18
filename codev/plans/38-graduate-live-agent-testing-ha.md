# Plan: Graduate the live-agent testing harness to a maintained opt-in test capability

## Metadata
- **ID**: plan-2026-07-17-graduate-live-agent-testing-ha
- **Status**: draft
- **Specification**: [codev/specs/38-graduate-live-agent-testing-ha.md](../specs/38-graduate-live-agent-testing-ha.md)
- **Created**: 2026-07-17

## Executive Summary

Implement the spec's `tests/agent_sessions/` package in six dependency-ordered
phases: (1) the graduated core harness + in-process ephemeral-port `ViewerMonitor`;
(2) the oracle + known-bug registry + the opt-in runner (CLI, gating, artifact
management); (3) the single-prompt scenarios S1–S4 with the **#32** flip-home;
(4) folding in the round-2 reusable pieces — the Exp-4 multi-turn chained driver and
the Exp-9 timeline probe — as S5/S6 with the **#33** flip-home; (5) the Exp-6 forced
degraded scenario S7 with the **#36** flip-home; (6) docs, `.gitignore`, README
pointer, and the final acceptance sweep.

**Two test tiers, and a hard CI rule.** The live scenarios (S1–S7) require installed,
authenticated agents and are **excluded from CI by construction** (they live under a
package CI never enumerates, in files not named `test_*.py`). The deterministic,
**tool-free** plumbing/oracle checks are *also* kept under `tests/agent_sessions/`
(named `selftest_*.py`) and run via `python -m tests.agent_sessions --selftest`, so
that **the CI-collected test set stays byte-identical** — M2 ("default CI matrix
provably unchanged") holds *literally*: no new top-level `test_*.py`, no new CI job,
no new skip. The self-test tier gives the plumbing real, runnable regression coverage
without agents; the live tier is the opt-in developer capability.

**Environment confirmed** (this worktree): `claude`, `agy`, `codex`, `strace` all
present; `python -m tests.agent_sessions` resolves from the repo root via PEP 420
namespace packages even though `tests/` has no `__init__.py` (architect's note — the
runner **must be run from the repo root**; documented in Phase 6). `ptrace_scope=1`,
matching the experiment environment.

## Success Metrics

Copied from the spec (M1–M6, N1–N3, I1), plus implementation-specific gates:
- [ ] **M1** one command runs the oracle-backed suite with authenticated tools
- [ ] **M2** CI-collected test set byte-identical (no new `test_*.py`, no new job/leg/skip)
- [ ] **M3** #32/#33/#36 each recorded `known-bug:#N`; each flips in a one-line change
- [ ] **M4** missing/unauthenticated requested tool fails loud, naming the tool
- [ ] **M5** Exp-4 multi-turn driver + Exp-9 timeline probe folded into the suite
- [ ] **M6** viewers use OS-assigned ephemeral ports; no sequential constants remain
- [ ] **N1** stdlib-only; no `sys.path.insert` into `experiments/`
- [ ] **N2** raw artifacts never enter git (auto-clean temp default; keep-artifacts bounded)
- [ ] **N3** `docs/agent-sessions.md` covers prereqs/auth/`--dangerously-skip-permissions`/sandbox/F5/F7
- [ ] **I1** `experiments/` unmodified (`git status --porcelain experiments/` empty)
- [ ] `--selftest` passes tool-free on any host (including CI-like, no agents)

## Phases (Machine Readable)

```json
{
  "phases": [
    {"id": "phase_1", "title": "Core harness + in-process ephemeral-port ViewerMonitor"},
    {"id": "phase_2", "title": "Oracle, known-bug registry, and opt-in runner (CLI + gating)"},
    {"id": "phase_3", "title": "Single-prompt scenarios S1-S4 + #32 flip-home"},
    {"id": "phase_4", "title": "Fold in round-2 drivers: multi-turn (Exp4) + timeline (Exp9) + #33 flip-home"},
    {"id": "phase_5", "title": "Degraded parse-failure scenario (Exp6) + #36 flip-home"},
    {"id": "phase_6", "title": "Docs, gitignore, README pointer, acceptance sweep"}
  ]
}
```

## Phase Breakdown

### Phase 1: Core harness + in-process ephemeral-port ViewerMonitor
**Dependencies**: None

#### Objectives
- Stand up the `tests/agent_sessions/` package and graduate `harness.py` from the
  experiment, with **no `sys.path.insert` into `experiments/`** and **checkout-first**
  `ai-observe` resolution (Decision 8).
- Replace the fixed/sequential viewer port with an **in-process `ViewerServer(port=0)`**
  monitor (Decision 6), attaching only after the session artifact exists (Decision 11).

#### Deliverables
- [ ] `tests/agent_sessions/__init__.py` — puts `src/` on `sys.path` once
      (`parents[2]/"src"`); **no live work at import**.
- [ ] `tests/agent_sessions/harness.py` — `run_observed_session`, `SessionResult`,
      `load_events`, `summarize_events`, `list_workdir`, `TOOLS` (single-prompt
      builders), `tool_available`, `resolve_ai_observe()` (checkout `bin/ai-observe`
      first, `shutil.which("ai-observe")` fallback), and `ViewerMonitor` rewritten to
      run `ai_observe.viewer.server.ViewerServer(jsonl, port=0)` in-process and read
      `.url` (raw-socket SSE `collect_events` retargeted to `server.url`'s host:port).
- [ ] `tests/agent_sessions/selftest/__init__.py` (makes the self-test tier an
      importable subpackage for explicit `unittest`/`loadTestsFromModule` loading).
- [ ] `tests/agent_sessions/selftest/selftest_harness.py` — tool-free `unittest`
      checks (see Test Plan).

#### Implementation Details
- Port `harness.py` almost verbatim; the only behavioral changes are (a) entrypoint
  resolution order, (b) in-process viewer + ephemeral port, (c) `ROOT` derived from
  `parents[2]` instead of the experiment's `bin/ai-observe` upward search (still valid,
  but anchored to the package location).
- `ViewerMonitor.start()` becomes: construct `ViewerServer(jsonl, port=0)`, capture
  `self.url` from `server.url`; `stop()` calls the server's shutdown. `collect_events`
  keeps its select-based settle loop, parsing `server.url` for host/port.
- Sequencing contract (Decision 11) documented in `run_observed_session`'s docstring:
  run the observed session to a finalized `.jsonl` **first**, then attach the viewer.

#### Acceptance Criteria
- [ ] `python -m unittest tests.agent_sessions.selftest.selftest_harness` (run from the
      repo root) passes with **no agent tools involved**. *(Runnable independently of
      Phase 2's `__main__.py` — the `--selftest` flag is Phase 2's convenience wrapper
      over this same module; Phase 1 does not depend on it.)*
- [ ] Two `ViewerMonitor`s constructed back-to-back bind **distinct nonzero ports**
      and both serve `/session` (no collision).
- [ ] `resolve_ai_observe()` returns the checkout `bin/ai-observe` when it exists.
- [ ] `grep -rn "78[0-9][0-9]\|79[0-9][0-9]"` over `tests/agent_sessions/` finds **no**
      hard-coded viewer port constant.

#### Test Plan
- **Self-test (tool-free, CI-shaped)**: attach a `ViewerMonitor` to the static fixture
  `tests/fixtures/viewer/basic.jsonl`, assert it serves `/session` and `collect_events`
  returns the fixture's events — exercises the *entire* viewer-monitor path with no
  agent. Assert ephemeral-port distinctness and entrypoint resolution.
- **Manual/live**: deferred to Phase 3 (first real `run_observed_session`).

#### Rollback Strategy
`git revert` the phase commit; the package is additive and imported by nothing else yet.

#### Risks
- **Risk**: `ViewerServer` lifecycle API differs from assumptions.
  - **Mitigation**: `server.py` already exposes `.url` and is used in-process by
    `tests/test_viewer_smoke_e2e.py`; mirror that usage exactly.

---

### Phase 2: Oracle, known-bug registry, and opt-in runner (CLI + gating)
**Dependencies**: Phase 1

#### Objectives
- Implement the three-view oracle and the rot-proof, one-line-flip known-bug registry
  (Decision 5).
- Implement the opt-in runner: preflight loud-fail (Decision 4), `--tools`/`--scenarios`/
  `--json`/`--keep-artifacts`/`--selftest` (Decision 10), and artifact management
  (auto-clean temp default + bounded `--keep-artifacts`, Decision 7).

#### Deliverables
- [ ] `tests/agent_sessions/oracle.py` — `KnownBug(issue, desc, active)`, `OPEN_BUGS`
      (`32/33/36`, all `active=True`), `CheckResult(scenario, tool, view, status, detail)`
      with `status ∈ {pass, fail, known-bug:#N}`; hard-assert helpers
      (`check_agent_file`, `check_captured`, `check_viewer`); known-bug gates
      (`expect_deletion_captured(…, bug=32)`, `expect_no_marker_noise(…, bug=33)`,
      `expect_authority_not_overstated(…, bug=36)`) built on one primitive
      `known_bug_gate(bug, buggy_present, correct_present)`.
- [ ] `tests/agent_sessions/__main__.py` — arg parsing; `shutil.which` preflight
      (loud, names the tool); scenario registry keyed by short-name; **applicability
      resolution** (see below); artifact dir manager (`TemporaryDirectory` unless
      `--keep-artifacts`; reject a `--keep-artifacts` path that resolves inside the repo
      working tree unless under the suite's ignored subtree); result aggregation →
      human summary (stderr) + `--json` (stdout); nonzero exit on any `fail`;
      `--selftest` loads `selftest/selftest_*.py` explicitly via
      `unittest.TestLoader().loadTestsFromModule`.
- [ ] `tests/agent_sessions/selftest/selftest_oracle.py`,
      `selftest/selftest_runner.py` — tool-free checks (see Test Plan), including a
      **fake-tool seam** for the unauthenticated branch (M4).

#### Implementation Details
- `known_bug_gate` semantics: **active** → `known-bug:#N` if `buggy_present` else
  `fail` ("#N no longer reproduces — flip OPEN_BUGS[#N].active=False"); **inactive** →
  `pass` if `correct_present` else `fail` ("#N regressed"). The flip is the single
  edit `active=False`.
- **Preflight — presence:** for each requested tool absent from PATH → print
  `tool '<t>' not found on PATH; install it or narrow --tools` to stderr, exit 2.
- **M4 — unauthenticated / unusable tool (installed but no auth / no events / immediate
  failure):** the oracle raises `ToolUnusable('<t>')` when a scenario's first agent
  invocation returns nonzero **or** yields zero watched-root events; the runner renders
  it as a **loud, named `fail`** (`tool '<t>' produced no events — not authenticated or
  agent error; rerun with --keep-artifacts to inspect stderr`) and exits nonzero. This
  branch is made **deterministically testable** via a **fake-tool seam**: the runner
  resolves each tool's command through `TOOLS`/`resolve_ai_observe`, and the self-test
  injects a stub "tool" (a tiny script on a temp `PATH` that exits nonzero or writes
  nothing) so `selftest_runner` exercises the unauth branch **without touching real
  agents** — closing Codex's gap that this path was otherwise only manually reachable.
- **Requested-but-non-applicable pairs (explicit exclusion, never silent):** each
  scenario declares `applies_to` (its tool set; e.g. `timeline`/`degraded` are
  claude-only). When a tool is **explicitly named in `--tools`** but a selected scenario
  excludes it, the runner emits an explicit `CheckResult(status="excluded", detail="scenario
  '<s>' does not apply to tool '<t>' (claude-only)")` that is **surfaced in the summary
  and `--json`** — an explicit, reasoned exclusion naming the tool, honoring the spec's
  "fail or exclude with an explicit reason." (On a default all-tools run, non-applicable
  pairs are informational, not requested; only `--tools`-named pairs produce the explicit
  `excluded` record. `excluded` is not a `fail` and does not by itself set nonzero exit.)
- **`--keep-artifacts` boundary (sealed):** resolve the path; **reject** when
  `path == ROOT or ROOT in path.parents` **unless** the path is under
  `tests/agent_sessions/<ignored-artifacts-dir>/` → exit 2 with a clear message; else
  accept. (The `path == ROOT` clause seals the `--keep-artifacts .`-from-root bypass
  Gemini flagged — `ROOT` is not in its own `.parents`.) Prefer `path.is_relative_to(ROOT)`
  where available (≥3.9) as the equivalent one-call form.

#### Acceptance Criteria
- [ ] `python -m tests.agent_sessions --tools nope --scenarios single_write` (from repo
      root) exits nonzero and names `nope` (S8, no tools needed).
- [ ] **M4 unauth branch:** a fake tool that is present-but-unusable (exits nonzero /
      emits no events) produces a **loud, named `fail`** and nonzero exit — verified
      deterministically in `selftest_runner` via the fake-tool seam (no real agent).
- [ ] **Non-applicable pair:** `--tools claude,codex --scenarios timeline` yields an
      explicit `excluded` record naming `codex` (claude-only) in both summary and
      `--json`; it is not silently dropped and is not a `fail`.
- [ ] `--selftest` passes tool-free; covers all four known-bug-gate branches and the
      stale-annotation failure path.
- [ ] `--keep-artifacts <tracked-in-repo-path>` is rejected — **including
      `--keep-artifacts .` run from the repo root**; an outside-repo path is accepted.
- [ ] Running with no `--keep-artifacts` leaves **no** residual artifact dir after exit.

#### Test Plan
- **Self-test (tool-free)**: `selftest_oracle` drives `known_bug_gate`/`expect_*` across
  active-buggy, active-fixed(→fail), inactive-correct, inactive-buggy(→fail).
  `selftest_runner` shells `python -m tests.agent_sessions …` **from the repo root**
  (inheriting `cwd`/`sys.path`) to assert: the missing-tool loud exit; the **fake-tool
  unauth loud fail**; the **explicit non-applicable `excluded` record**; the
  keep-artifacts boundary (incl. `.`-from-root and a symlink case); and temp-dir cleanup.
- **Manual/live**: none (this phase is fully tool-free).

#### Rollback Strategy
`git revert` the phase commit; scenarios (Phase 3+) not yet wired.

#### Risks
- **Risk**: the keep-artifacts boundary check has a path-resolution edge (symlinks).
  - **Mitigation**: compare `Path.resolve()` outputs; add a symlink case to `selftest_runner`.

---

### Phase 3: Single-prompt scenarios S1–S4 + #32 flip-home
**Dependencies**: Phase 2

#### Objectives
- Wire the first live scenarios through the three-view oracle: single-write (S1),
  ephemeral create-then-delete (S2, **#32** home), modify/append (S3), subprocess (S4).

#### Deliverables
- [ ] `tests/agent_sessions/scenarios/__init__.py`
- [ ] `scenarios/check_single_write.py` (S1; codex `#33` noise annotated)
- [ ] `scenarios/check_ephemeral.py` (S2; `expect_deletion_captured(bug=32)`)
- [ ] `scenarios/check_modify.py` (S3)
- [ ] `scenarios/check_subprocess.py` (S4)
- [ ] driver-argv self-test additions (`selftest/selftest_drivers.py`) asserting the
      exact per-tool single-prompt argv (tool-free).

#### Implementation Details
- Each scenario exposes `run(tool, ctx) -> list[CheckResult]`: agent-actual checks are
  **hard**; canonical-`.jsonl` checks hard except where a `known_bug_gate` applies;
  viewer checks assert completeness/shape. Scenarios declare which tools they apply to
  (e.g. S2 `#32` targets claude/agy; codex path asserts its own real `unlink`).
- The runner's scenario registry auto-discovers `scenarios/check_*.py` by short-name.

#### Acceptance Criteria
- [ ] `python -m tests.agent_sessions --scenarios single_write,ephemeral,modify,subprocess`
      runs against present+authenticated tools and reports per-scenario/tool results.
- [ ] S2 records `known-bug:#32` for claude/agy today; flipping `OPEN_BUGS[32].active`
      turns it into a hard assertion (verified by a temporary local flip, reverted).
- [ ] `selftest_drivers` (tool-free) passes: single-prompt argv per tool is exact.

#### Test Plan
- **Self-test (tool-free)**: driver-argv construction assertions.
- **Manual/live** (tools present in this worktree): run the four scenarios; capture the
  JSON report to confirm agent-actual passes and #32 annotation appears. Confirm F2's
  known behavior (deletion dropped) still reproduces, validating the annotation is live.

#### Rollback Strategy
`git revert`; earlier phases remain independently valid.

#### Risks
- **Risk**: a tool is present but unauthenticated in this worktree → scenario fails loud.
  - **Mitigation**: that is the *correct* Decision-4 behavior; narrow `--tools` to the
    authenticated set for the manual run and record which tools were exercised.

---

### Phase 4: Fold in round-2 drivers — multi-turn (Exp4) + timeline (Exp9) + #33 flip-home
**Dependencies**: Phase 3

#### Objectives
- Fold the two reusable round-2 pieces into the maintained suite (M5): the multi-turn
  chained driver (Exp 4) as S5 (**#33** home) and the timeline-sampling probe (Exp 9)
  as S6.

#### Deliverables
- [ ] `tests/agent_sessions/drivers.py` — `chained_multi_turn(tool, turns, workdir)`
      building the single `ai-observe -- bash -lc "<t1> && <t2> && …"` invocation with
      per-tool resume flags (claude `-c`, agy `-c --add-dir`, codex
      `exec --sandbox … resume --last`), ported from `multiturn.py`.
- [ ] `tests/agent_sessions/probes.py` — `sample_timeline(session, workdir, …)` porting
      Exp 9's attach-then-sample loop; uses the in-process ephemeral-port monitor.
- [ ] `scenarios/check_multi_turn.py` (S5; later-turn ops hard; codex `expect_no_marker_noise(bug=33)`)
- [ ] `scenarios/check_timeline.py` (S6; ≥3 distinct increasing viewer counts during
      the run; final viewer == canonical)
- [ ] `selftest/selftest_drivers.py` extended: assert the exact chained-shell string per
      tool (tool-free), including codex's `--sandbox`-before-`resume` ordering.

#### Implementation Details
- The chained driver reuses harness helpers; the timeline probe drives a long claude
  shell loop (Exp 9's task) and samples on a cadence, attaching one in-process viewer.
- `#33` gate on codex: assert the real ops are intact **and** the `/newroot` marker-noise
  deletes are present (annotated); the flip asserts noise is gone.

#### Acceptance Criteria
- [ ] `--scenarios multi_turn,timeline` runs live; S5 captures later-turn ops for all
      authenticated tools; S6 shows incremental (≥3 increasing) then complete.
- [ ] S5 records `known-bug:#33` for codex; a local flip turns it hard.
- [ ] `selftest_drivers` asserts chained-shell strings for all three tools (tool-free),
      locking the folded drivers without needing agents.

#### Test Plan
- **Self-test (tool-free)**: chained-driver + resume-flag argv assertions.
- **Manual/live**: run S5 (all authenticated tools) and S6 (claude); confirm the
  multi-turn continuity signal and the timeline incremental oracle.

#### Rollback Strategy
`git revert`; S1–S4 remain valid.

#### Risks
- **Risk**: codex resume-flag ordering regresses (documented footgun in Exp 4 notes).
  - **Mitigation**: the tool-free argv assertion pins the exact ordering.

---

### Phase 5: Degraded parse-failure scenario (Exp6) + #36 flip-home
**Dependencies**: Phase 4

#### Objectives
- Give **#36** its oracle flip-home (Decision 9): the forced degraded parse-failure
  path (S7), claude-only, via the in-tree `AI_OBSERVE_TEST_FAIL_AFTER` hook.

#### Deliverables
- [ ] `scenarios/check_degraded.py` (S7) — drive a paced multi-file claude task with
      `AI_OBSERVE_TEST_FAIL_AFTER=N`; read the `.meta.json`; assert agent-actual files
      exist (hard); `expect_authority_not_overstated(meta, bug=36)` — while #36 open,
      assert the sidecar **does** label the snapshot-only `.jsonl` `authoritative_complete`
      under `parser_status = parser_failure_partial` (the bug reproduces); the flip
      asserts the role is downgraded (e.g. `authoritative_net`/`None`).
- [ ] `selftest/selftest_degraded.py` — tool-free unit test of
      `expect_authority_not_overstated` against synthetic `.meta.json` dicts (both the
      buggy and the hypothetical-fixed shapes), exercising the flip logic without claude.

#### Implementation Details
- Port the relevant `_run_case` logic from `6_degraded_recovery/degraded.py` (the
  `parse_failure_partial` case only). Artifacts to a temp session dir (auto-clean).
- The oracle reads `meta["parser"]["status"]` and the artifact `role` fields;
  `known_bug_gate(36, buggy_present = role=="authoritative_complete" and status startswith "parser_failure", correct_present = not that)`.

#### Acceptance Criteria
- [ ] `--scenarios degraded` runs live (claude) and records `known-bug:#36`.
- [ ] `selftest_degraded` (tool-free) passes both buggy and flipped shapes.
- [ ] All three of #32/#33/#36 now have a dedicated flip-home; **M3 satisfied**.

#### Test Plan
- **Self-test (tool-free)**: synthetic-meta flip logic.
- **Manual/live**: run S7 with claude; confirm `parser_status=parser_failure_partial`
  and the overstated authority label reproduce (annotation live).

#### Rollback Strategy
`git revert`; S1–S6 remain valid.

#### Risks
- **Risk**: the `AI_OBSERVE_TEST_FAIL_AFTER` env facade name differs.
  - **Mitigation**: confirmed present in `src/ai_observe/observe.py:246`; the env facade
    prepends `AI_OBSERVE_` — mirror `degraded.py`'s exact key.

---

### Phase 6: Docs, gitignore, README pointer, acceptance sweep
**Dependencies**: Phase 5

#### Objectives
- Ship the docs (N3), the artifact `.gitignore` (N2), a README pointer, and run the
  final acceptance sweep (M2, I1).

#### Deliverables
- [ ] `docs/agent-sessions.md` — the one command + flags; **run from the repo root**
      (PEP 420 note); per-tool prereqs + **auth** expectations (why CI is local-only);
      `--dangerously-skip-permissions` implications + sandbox-friendly throwaway
      `workdir` guidance; the known-bug annotations and how to flip one on fix; the two
      informational round-2 notes — **F5** (viewer requires the `.jsonl` to exist at
      launch) and **F7** (orphaned-session recovery via `.trace` after observer SIGKILL).
- [ ] `tests/agent_sessions/.gitignore` — ignore the suite's artifact/work dir
      (mirroring `experiments/.gitignore` patterns).
- [ ] `README.md` — a short pointer to `docs/agent-sessions.md` under the testing/docs
      section.

#### Implementation Details
- Docs live in `docs/` beside `observe.md`/`viewer.md` (house convention).
- Acceptance sweep commands run and recorded in the review.

#### Acceptance Criteria
- [ ] **M2**: `ls tests/test_*.py` set is unchanged vs. `main`; no new CI job/leg; no new
      skip. (No new top-level `test_*.py` was added — all suite tests are under
      `tests/agent_sessions/` as `selftest_*.py`/`check_*.py`.)
- [ ] **I1**: `git status --porcelain experiments/` is empty across the whole branch.
- [ ] `python -m tests.agent_sessions --selftest` passes tool-free from the repo root.
- [ ] Docs cover every N3 item including F5 and F7.

#### Test Plan
- **Self-test (tool-free)**: full `--selftest` green.
- **Manual/live**: a full `python -m tests.agent_sessions` run against the authenticated
  tool set, captured into the review as the M1 evidence.

#### Rollback Strategy
`git revert`; the suite remains functional without docs.

#### Risks
- **Risk**: a stray top-level `test_*.py` slips in and changes the CI set.
  - **Mitigation**: the acceptance sweep diffs `ls tests/test_*.py` against `main`.

---

## Dependency Map
```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5 ──→ Phase 6
(harness)   (oracle+    (S1–S4,     (Exp4+Exp9, (Exp6,      (docs,
            runner)     #32)        #33)        #36)        sweep)
```

## Resource Requirements
- **Environment**: Linux + `strace`; `claude`/`agy`/`codex` installed and
  **authenticated** for the live tier (all present in this worktree). The `--selftest`
  tier needs none of these.

## Integration Points
- **Internal**: `ai_observe.viewer.server.ViewerServer` (in-process viewer),
  `bin/ai-observe` (wrapper entrypoint), `AI_OBSERVE_TEST_FAIL_AFTER` hook
  (`src/ai_observe/observe.py`). No external systems.

## Risk Analysis
### Technical Risks
| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Adding a top-level `test_*.py` breaks the CI-unchanged guarantee | L | H | All suite tests under `tests/agent_sessions/` (non-`test_*.py`); Phase 6 diffs the glob |
| A tool present but unauthenticated in this worktree blocks live verification | M | M | Decision-4 loud fail is correct; narrow `--tools` and record exercised set |
| Live scenarios are nondeterministic across agent versions | M | M | Agent-actual checks are the hard oracle; ai-observe-side divergences are bug-annotated |
| In-process viewer lifecycle differs from experiment subprocess | L | M | Mirror `test_viewer_smoke_e2e.py`'s in-process `ViewerServer` usage |

## Validation Checkpoints
1. **After Phase 2**: `--selftest` green tool-free; S8 loud-fail proven — the entire
   gating/oracle spine is verified without agents.
2. **After Phase 5**: all three bug flip-homes exist; a temporary local flip of each
   turns its annotation into a hard assertion (reverted before commit).
3. **Before PR**: full live run captured (M1); CI-set diff empty (M2); `experiments/`
   clean (I1).

## Documentation Updates Required
- [ ] `docs/agent-sessions.md` (new)
- [ ] `README.md` pointer
- [ ] Review doc records the live-run evidence and the CI-set diff

## Expert Review

### Plan iter 1 — 3-way (Gemini COMMENT / Codex REQUEST_CHANGES / Claude APPROVE)

Reviewers verified the load-bearing claims against the codebase (`ViewerServer(port=0)`
+ `.url` at `server.py:376`, `.stop()` at 432; `env_value("TEST_FAIL_AFTER")` at
`observe.py:246`; `tests/__init__.py` absent → PEP 420; `basic.jsonl` fixture present;
`test_viewer_smoke_e2e.py` uses in-process `ViewerServer(port=0)`). Adjustments applied:

- **Phase 1 AC self-containment (Gemini):** Phase 1's self-test now runs via
  `python -m unittest tests.agent_sessions.selftest.selftest_harness`, independent of
  Phase 2's `__main__.py`. Added `selftest/__init__.py`.
- **keep-artifacts boundary bypass (Gemini):** sealed the `--keep-artifacts .`-from-root
  hole — condition is now `path == ROOT or ROOT in path.parents` (or `is_relative_to`);
  added the `.`-from-root and symlink cases to `selftest_runner`.
- **M4 unauthenticated branch (Codex, blocking):** added a deterministic **fake-tool
  seam** so the present-but-unusable (nonzero / no-events) path is implemented and
  self-tested without real agents, not left to manual runs.
- **Requested-but-non-applicable pairs (Codex, blocking):** the runner now emits an
  explicit, named `excluded` `CheckResult` (surfaced in summary + `--json`) for a
  `--tools`-named tool a selected scenario excludes (e.g. `codex`+`timeline`), instead
  of silently dropping it; self-tested.
- **Minor (Claude):** `selftest/` is a subpackage (`__init__.py`); `selftest_runner`
  subprocess runs from the repo root inheriting `cwd`/`sys.path`.

**Plan Adjustments**: Phases 1–2 deliverables/ACs/Test Plans updated as above; no phase
added or removed; no scope change.

## Approval
- [ ] Expert AI Consultation Complete
- [ ] Human plan-approval gate

## Change Log
| Date | Change | Reason | Author |
|------|--------|--------|--------|
| 2026-07-17 | Initial plan | Spec 38 approved | builder spir-38 |
| 2026-07-17 | Plan iter-1 review fixes (Phase 1 AC, keep-artifacts seal, M4 fake-tool seam, non-applicable exclusion) | 3-way plan review | builder spir-38 |
| 2026-07-18 | **Phase 3 deviation (architect-endorsed): #32/#33 gates use deterministic `trace_parser` probes, not live-agent events.** During Phase 3 the live #32 gate flapped (known-bug ↔ FAIL) across runs because the deletion syscall form an agent emits is nondeterministic (annotated `unlinkat(AT_FDCWD<dir>)` that #32 drops vs a captured `rm`/plain form). Both #32 and #33 are `trace_parser`/watched-root-filter bugs, so the gates now reproduce them deterministically by feeding the exact annotated-`unlinkat` (#32) and `/newroot` mkdir + canonical rmdir (#33) forms through ai-observe's real `trace_parser` — the same way FINDINGS F1/F2 verified them. Probes live in the **tool-free self-test tier** (flip detection works with no agent; empirically confirmed the flip is the single `OPEN_BUGS[N].active` edit and is rot-proof both directions). Live scenarios keep agent-actual + viewer as HARD checks plus a **non-gating `INFO` record** of which deletion form the agent used each run (retains live evidence without gate flap). | Live nondeterminism found in Phase 3; architect endorsed | builder spir-38 |

## Notes
- **CI-unchanged strategy (M2, load-bearing):** *all* new test code — including the
  tool-free plumbing/oracle checks — lives under `tests/agent_sessions/` with
  `selftest_*.py` / `check_*.py` names. Nothing matches CI's `ls test_*.py` glob or
  `unittest discover`'s `test*.py` pattern, so the CI-collected set is byte-identical.
  The self-test tier is invoked via `python -m tests.agent_sessions --selftest`.
- **Run-from-root (architect note):** PEP 420 namespace resolution makes
  `python -m tests.agent_sessions` work from the repo root without `tests/__init__.py`;
  verified empirically. Documented as a hard requirement in `docs/agent-sessions.md`.
- **Commit cadence:** one atomic commit per phase, message
  `[Spec 38][Phase: <name>] <type>: <desc>`; new files staged the moment they are
  created (porch commit-sweep lesson).
