# Specification: Graduate the live-agent testing harness to a maintained opt-in test capability

## Summary

The live-agent testing harness (`experiments/1_driving_mechanism/harness.py`) drives
a **real** AI coding agent (claude / agy / codex) in non-interactive mode under
`ai-observe` and triangulates three views of what happened: the agent's actual
filesystem effects, the canonical `.jsonl` events ai-observe recorded, and the
sanitized events the browser viewer served. Two experiment rounds (#31 → PR #34,
#35 → PR #37) proved its value — it found three real, still-open bugs (#32, #33,
#36) and validated the core observation paths across all three tools.

This spec graduates that harness from throwaway experiment code into a
**maintained, opt-in test capability**: a real package under `tests/`, with an
assertion layer (a captured-vs-actual **oracle**), that a developer with the three
tools authenticated can run with one command. The default CI matrix stays
**provably unchanged** — the live suite is *excluded by construction*, not gated by
a skip that could silently turn CI green.

The point-in-time reports committed in `experiments/` were evidence, not
regression fixtures. The graduated suite replaces "look at the report" with "drive
the scenario and assert," while tolerating the three known-open bugs as
**expected-and-annotated** signatures (tied to their issue numbers) that flip to
hard assertions in a one-line change when each fix merges.

## Background and current state

### What the experiment rounds established (the substrate this builds on)

- `harness.py` (stdlib-only, 438 lines) exposes `run_observed_session(...)` →
  `SessionResult` (agent result + on-disk canonical events + viewer events + actual
  workdir files), plus `ViewerMonitor` (HTTP-polls the viewer's sanitized
  `/session` + `/events`), `load_events`, `summarize_events`, `list_workdir`,
  `TOOLS`, `tool_available`.
- Per-tool invocation quirks are already baked in: claude `-p … --dangerously-skip-permissions`;
  agy `-p … --dangerously-skip-permissions --add-dir <workdir>`; codex `exec --sandbox workspace-write`.
- Round 2 added two reusable pieces the issue wants folded in:
  - **Exp 4** `4_multi_turn/multiturn.py` — a per-tool *chained* multi-turn driver
    (`ai-observe -- bash -lc "<turn1> && <turn2> && <turn3>"`, one wrapper = one
    strace tree; continuity via each tool's resume/continue flag).
  - **Exp 9** `9_long_running/incremental.py` — a **timeline-sampling probe** that
    attaches one viewer and samples the visible-event count on a cadence to prove
    *timeliness* (events appear progressively, not end-loaded).
- **Exp 6** `6_degraded_recovery/degraded.py` forces the degraded parse-failure
  path via the in-tree hook `AI_OBSERVE_TEST_FAIL_AFTER=N` (claude-only, no extra
  tools) — this is the only driver that surfaces bug **#36**.

### The three open bugs the oracle must account for (all confirmed OPEN)

| Issue | Signature | Where it surfaces |
|-------|-----------|-------------------|
| **#32** | Annotated `unlinkat(AT_FDCWD<dir>, "f", 0)` deletions are silently dropped → a deleted file is never reported deleted (claude/agy). | A create-then-delete (ephemeral) scenario. |
| **#33** | codex's mount-namespace sandbox routes `mkdir`s through `/newroot/…` while `rmdir`s use the canonical path → dozens of unpaired `delete` events implying destruction. | *Every* codex scenario; volume scales with turns. |
| **#36** | On the direct-parser-failure path the `.meta.json` labels a snapshot-only `.jsonl` `authoritative_complete`, overstating fidelity. | The forced degraded parse-failure path (Exp 6). |

### Relevant system-shape facts (consulted per the issue)

- **CI is two explicit steps** (`arch.md` §Continuous integration): the main suite
  runs from `tests/` cwd as `python -m unittest -v $mods` where
  `mods = ls test_*.py | grep -v test_packaging_smoke`, then the smoke module alone.
  Discovery is an **explicit module list over top-level `test_*.py`**, not
  `discover` recursion.
- **CI fails loud on ANY unittest skip** (`arch-critical.md`): both steps grep the
  verbose output for `\.\.\. skipped|skipped=[0-9]` and fail the job. Capability
  gating must therefore *exclude by construction*, never skip.
- The viewer supports **OS-assigned ephemeral ports**: `ViewerServer(path, port=0)`
  binds `127.0.0.1:0` and exposes the chosen address via `.url`
  (`server.py` reads `self._httpd.server_address`). Existing viewer smoke tests run
  it in-process this way.
- **`experiments/` is immutable historical record**; raw `.trace`/`.jsonl`/`.meta.json`
  are large + sensitive (absolute paths, argv/prompts, raw syscalls) and are
  git-ignored today under `experiments/.gitignore`.

## Constraints (fixed by the architect / issue — do not relitigate)

These come straight from issue #38 and its referenced rules:

1. **No live-agent runs in the default CI test matrix** — they need network, auth,
   and minutes of wall time and are nondeterministic.
2. **No silent skips.** Honor the arch-critical rule: a capability-gated skip is a
   local-dev affordance only; an un-provisioned environment must be **loud or
   excluded by construction, never silently green**.
3. **Keep the harness stdlib-only** unless the spec justifies otherwise (it does
   not need to depart from this).
4. **Raw session artifacts produced by test runs must stay out of git** (extend
   ignore patterns as needed).
5. **`experiments/` directories are immutable** — the copies stay untouched; the new
   module supersedes them. No `sys.path.insert` path hacks in the graduated module.
6. **Tool-absence behavior:** when a tool is missing/unauthenticated, **fail or
   exclude with an explicit reason naming the tool** — never silently skip a tool.
7. **Open-bug signatures** (#32/#33/#36) are **expected-and-annotated** (tied to
   their issue numbers) until fixed, then **flip to hard assertions** — a one-line
   change — without violating the no-silent-skip rule.

## Stakeholders

- **ai-observe maintainers** — get a maintained regression oracle over the real
  agent→strace→artifact→viewer path, and an automatic tripwire that fires when a
  known bug is fixed (or regresses) so the annotation can't rot.
- **Contributors fixing #32/#33/#36** — get a one-line flip that converts the
  annotated tolerance into a hard regression assertion the moment their fix lands.
- **CI** — is a stakeholder by *exclusion*: its matrix, timing, and skip-count must
  be provably identical after this change.

## Solution exploration

### Decision 1 — Module location & shape: a non-discoverable `tests/agent_sessions/` package

**Chosen:** a real package at `tests/agent_sessions/` (`__init__.py` present),
containing the graduated harness plus the oracle and scenarios. It imports
`ai_observe` the way sibling tests do (`sys.path.insert(0, ROOT/"src")` at the
package boundary — this is the sanctioned test convention, *not* the
experiment's cross-folder `sys.path.insert` hack into a sibling experiment dir).

Proposed layout:

```
tests/agent_sessions/
  __init__.py            # puts src/ on sys.path once; no live work at import
  harness.py             # graduated core: run_observed_session, ViewerMonitor,
                         #   load_events, summarize_events, list_workdir, TOOLS,
                         #   tool_available, tool resolution
  drivers.py             # single-prompt + Exp-4 multi-turn chained driver
  probes.py              # Exp-9 timeline-sampling probe
  oracle.py              # captured-vs-actual assertions + known-bug registry
  scenarios/             # the oracle-backed scenarios (NOT named test_*.py)
    check_single_write.py
    check_ephemeral.py         # #32 home
    check_modify.py
    check_subprocess.py
    check_multi_turn.py        # Exp 4; #33 home for codex
    check_timeline.py          # Exp 9
    check_degraded.py          # Exp 6 forced parse-failure; #36 home
  __main__.py            # the one-command runner (preflight + report + exit code)
  README-linked docs     # see Decision 6
```

**Why excluded-by-construction, at two layers:**
- CI never references `tests/agent_sessions/` (its module list is top-level
  `test_*.py` in `tests/`), so the live suite cannot enter the matrix.
- Scenario files are named `check_*.py` (or live under `scenarios/`), which does
  **not** match unittest's default `test*.py` discovery pattern — so a developer
  running `python -m unittest discover -s tests` locally also does not accidentally
  fire live agents. Both the CI path and the local-discover path are excluded by the
  filename convention, with zero skip markers to grep.

**Alternatives considered:**
- *Top-level `tests/test_agent_sessions.py` with `@skipUnless`.* Rejected: it would
  be picked up by the CI module glob and its skips would trip the fail-loud gate
  (or, if it "passed" by skipping, silently lose the point of the suite). Violates
  Constraints 1 & 2.
- *`tools/`.* Rejected: the deliverable is a **test** capability (assertions,
  oracle) that belongs with the other tests and reuses `tests/_util.py`
  conventions; `tools/` implies a shipped utility, and `tests/` is excluded from the
  wheel already (only ships in the sdist).

### Decision 2 — Capability gating: exclude from CI, be loud locally

- **CI:** the suite is not wired into any CI job — not the matrix, and **not** a
  scheduled/`workflow_dispatch` job either, because GitHub runners have no
  authenticated claude/agy/codex accounts; a scheduled live job would be
  perpetually red or perpetually skipped. The default-matrix-unchanged guarantee is
  the priority. (A future scheduled job remains possible *if* authenticated runners
  ever exist; called out as a non-goal for now.)
- **Local opt-in:** `python -m tests.agent_sessions [--tools claude,agy,codex] [--scenarios …]`
  is the single command. It **preflights** each requested tool and is loud on
  absence (see Decision 4). There are **no `unittest.skip` / `@skipUnless`** calls
  anywhere in the suite — tolerance for missing capabilities is expressed as an
  explicit `--tools` narrowing (an operator choice recorded in the report), never as
  a silent skip.

### Decision 3 — The oracle: three-way, agent-reality is always hard

For each scenario the oracle compares three views and classifies each check:

| View | Source | Assertion class |
|------|--------|-----------------|
| **agent-actual** | files/content left in `workdir` | **always HARD** — every round confirmed the agent side worked; a failure here is a real product or scenario break. |
| **canonical `.jsonl`** | events ai-observe recorded on disk | HARD *unless* a known-bug annotation applies (Decision 5). |
| **viewer-served** | sanitized SSE events via `ViewerMonitor` | HARD for completeness/shape; informational for the F5/F7 timing notes (Decision 6). |

The oracle emits a structured result per check (`scenario`, `tool`, `view`,
`status ∈ {pass, fail, known-bug:#N}`, `detail`) and the runner aggregates them
into a report + a process exit code (nonzero on any `fail`).

### Decision 4 — Tool absence / non-auth: loud, naming the tool

- **Presence:** preflight with `shutil.which` for each *requested* tool. A missing
  requested tool → the runner exits nonzero with `tool 'codex' not found on PATH;
  install it or narrow --tools`. (Not a skip; the process is red.)
- **Authentication:** presence ≠ auth. The first scenario per tool acts as the auth
  probe — an agent invocation that returns nonzero **or** produces zero
  watched-root events is raised by the oracle as
  `tool 'agy' produced no events — not authenticated? (see docs)`, a **hard
  failure naming the tool**, not a skip. (Deliberately narrowing `--tools` to omit
  a tool you can't auth is the explicit, recorded escape hatch.)

### Decision 5 — Known-bug annotations that can't rot and flip in one line

A single registry, keyed by issue number:

```python
# oracle.py
OPEN_BUGS = {
    32: KnownBug(32, "annotated AT_FDCWD deletion dropped",            active=True),
    33: KnownBug(33, "codex /newroot marker-noise unpaired deletes",   active=True),
    36: KnownBug(36, "sidecar labels snapshot-only .jsonl authoritative", active=True),
}
```

At each interfering call site the scenario asks the oracle to check the *correct*
behavior **through** the bug:

```python
# ephemeral scenario, after asserting the agent actually deleted the file (HARD):
oracle.expect_deletion_captured(events, "ephemeral.txt", bug=32)
```

Semantics of `expect_…(…, bug=N)`:
- **While `OPEN_BUGS[N].active` is True** → assert the **buggy** signature *still
  reproduces* (e.g. the delete is *absent*). Result is recorded as `known-bug:#32`
  and passes. Crucially, if the buggy signature has *disappeared* (someone fixed #32
  without flipping the flag), the check **fails loudly**: `bug #32 no longer
  reproduces — flip OPEN_BUGS[32].active=False and enable the hard assertion`. The
  annotation therefore cannot silently rot.
- **When flipped to `active=False`** (the one-line change) → the same call site
  becomes the **hard assertion** that the correct behavior holds (the delete *is*
  captured). A lingering bug now turns the suite red.

This is an assertion path end to end — never a `@skip` — so it does not, and cannot,
interact with the no-silent-skip rule.

Bug homes: **#32** → `check_ephemeral.py`; **#33** → `check_multi_turn.py` /
any codex run in `check_single_write.py`; **#36** → `check_degraded.py`
(forced parse-failure via `AI_OBSERVE_TEST_FAIL_AFTER`, claude-only).

### Decision 6 — Viewer port allocation: in-process server on an OS-assigned port

Replace the experiment's fixed/sequential constants (7899, 7900, 7920, 7960…) by
running the viewer **in-process** as `ViewerServer(jsonl, port=0)` and reading the
chosen host:port from `server.url`. The OS never hands out the same listening port
twice, so **parallel runs cannot collide by construction** (Constraint/req 5), and
the subprocess + `PYTHONPATH` dance disappears. `ViewerMonitor`'s raw-socket SSE
reader is retargeted from a constant to `server.url`'s address; its collect/settle
logic is otherwise unchanged.

### Decision 7 — Artifacts out of git by construction + belt-and-suspenders ignore

- Raw `.trace`/`.jsonl`/`.jsonl.partial`/`.jsonl.rebuilt`/`.meta.json` and agent
  `workdir`s are written to an **OS temp dir** (`tempfile.mkdtemp`) by default, so
  nothing lands in the worktree at all. `--keep-artifacts <dir>` (opt-in, for
  debugging) is the only way to persist them.
- As belt-and-suspenders, add a `tests/agent_sessions/.gitignore` mirroring
  `experiments/.gitignore`'s patterns for any in-tree artifact/work dir a developer
  points `--keep-artifacts` at.

### Decision 8 — Resolving the `ai-observe` wrapper entrypoint

The graduated harness resolves the wrapper CLI as: prefer an **installed
`ai-observe` console script** on PATH, fall back to the checkout **`bin/ai-observe`**
(consistent with the packaging philosophy in `arch.md` §Packaging — the shim
prefers the installed package, falls back to the checkout). This removes the
experiment's hard dependency on running from a checkout, without a `sys.path` hack.

### Docs (requirement 7)

A `docs/agent-sessions.md` (alongside `docs/observe.md`, `docs/viewer.md`), linked
from `README.md`, covering:
- The one command and its `--tools` / `--scenarios` / `--keep-artifacts` flags.
- Per-tool prerequisites and **auth expectations** (each tool must be installed
  *and* logged in; CI has none, hence local-only).
- `--dangerously-skip-permissions` implications (the suite drives agents
  non-interactively with permission prompts disabled; run in a throwaway,
  sandbox-friendly `workdir`, never a real project).
- The two informational round-2 findings as notes:
  - **F5** — the viewer requires its target `.jsonl` to exist at launch (start the
    session first, then attach the viewer; it is attach-to-existing, not wait-for).
  - **F7** — orphaned-session recovery: an observer SIGKILL leaves an accurate but
    meta-less `.jsonl` + the raw `.trace`; the `.trace` is the manual-recovery input.

## Open questions

**Critical (blocks progress):**
- None — the issue's WHAT-list plus the arch/lessons consultation resolve the
  design. (If the architect disagrees with any Decision above, flag at the
  spec-approval gate.)

**Important (affects design):**
1. **Is the #36 degraded scenario in v1 scope?** Requirement 3 names #36, and the
   only way to give it a real flip-home is to fold in Exp 6's forced-degraded driver
   (claude-only, uses the in-tree `AI_OBSERVE_TEST_FAIL_AFTER` hook — no extra
   tools, no network beyond the one claude run). **Recommendation: yes, include it**,
   so all three bugs have a flip-home. Deferring it means #36 is documented but not
   oracle-covered. *(Decision deferred to the architect at the gate.)*
2. **Auth-probe cost.** Using the first real scenario as the auth probe adds no
   extra runs but means a mis-auth surfaces a few seconds in rather than instantly.
   A dedicated ultra-cheap warmup prompt per tool is the alternative. *(Recommend:
   reuse the first scenario; no separate warmup.)*

**Nice-to-know (optimization):**
3. Whether to add a `pytest`-style entry in addition to `python -m tests.agent_sessions`
   (the repo is unittest-based today; recommend staying unittest/stdlib to honor
   the stdlib-only constraint).

## Success criteria (acceptance)

Functional (MUST):
- **M1** — A developer with claude, agy, and codex installed **and authenticated**
  runs a single command (`python -m tests.agent_sessions`) and gets pass/fail with
  oracle-backed assertions across the scenarios.
- **M2** — The **default CI matrix is provably unchanged**: same collected tests,
  same skip count (still zero-tolerance), no new job, no new matrix leg. Demonstrated
  by showing `ls tests/test_*.py` (the CI glob input) is unchanged and the live
  suite lives under `tests/agent_sessions/` with non-`test_*.py` scenario files.
- **M3** — Each open-bug signature is recorded as `known-bug:#N` in results, and
  **flipping one to a hard assertion is a one-line change** (`OPEN_BUGS[N].active =
  False`). Demonstrated for at least #32 (and #33; #36 if Decision-Q1 = include).
- **M4** — A missing **or unauthenticated** requested tool causes a **loud, named**
  failure (nonzero exit, message naming the tool) — never a silent skip / green.
- **M5** — The multi-turn chained driver (Exp 4) and the timeline-sampling probe
  (Exp 9) are folded into the maintained suite alongside `run_observed_session` and
  `ViewerMonitor`.
- **M6** — Viewer instances use OS-assigned ephemeral ports; **no sequential port
  constants remain**; two suite runs can proceed without a port collision.

Non-functional (SHOULD):
- **N1** — The graduated module is **stdlib-only** and contains **no
  `sys.path.insert` into `experiments/`** (imports `ai_observe` via the sanctioned
  `src/`-on-path test convention or an installed package).
- **N2** — Raw artifacts never enter git (temp-dir default; ignore rules cover any
  `--keep-artifacts` in-tree dir).
- **N3** — Docs (`docs/agent-sessions.md`) cover per-tool prereqs, auth,
  `--dangerously-skip-permissions`, sandbox workdirs, and the F5/F7 notes.

Immutability:
- **I1** — `experiments/` is untouched (git shows no modifications under
  `experiments/`).

### Test scenarios (how M1–M6 are exercised)

- **S1 single-write** (claude/agy/codex): agent file present + content correct
  (HARD); canonical `create` present (HARD; codex noise annotated `#33`); viewer
  served the event (HARD count/shape).
- **S2 ephemeral create-then-delete** (claude/agy): file absent on disk (HARD);
  deletion-captured checked via `expect_deletion_captured(bug=32)` (annotated
  today, flips on #32 fix).
- **S3 modify/append** (claude/agy): appended content on disk (HARD); `modify`
  captured (HARD).
- **S4 subprocess** (grandchild shell writes 3 files): all three files on disk
  (HARD); all three captured via process-tree scoping (HARD).
- **S5 multi-turn 3-turn chain** (Exp 4, all tools): later-turn ops captured, not
  just turn 1 (HARD; codex `#33` annotated).
- **S6 long-running timeline** (Exp 9, claude): ≥3 distinct increasing
  viewer-visible counts *during* the run (HARD timeliness); final viewer ==
  canonical (HARD completeness).
- **S7 degraded parse-failure** (Exp 6, claude, forced): sidecar authority label
  checked via `expect_authority_not_overstated(bug=36)` (annotated today, flips on
  #36 fix). *(Included iff Decision-Q1 = include.)*
- **S8 tool-absence**: invoking with a requested-but-missing tool exits nonzero and
  names it (exercises M4 without needing the tool present — inject a bogus
  `--tools nope` and assert the loud failure).

## Non-goals

- Wiring live-agent runs into **any** CI job (matrix, scheduled, or manual) — see
  Decision 2. Revisit only if authenticated runners appear.
- Rewriting or "cleaning up" `experiments/` — it is immutable historical record.
- Fixing #32/#33/#36 themselves — this spec builds the oracle that *tolerates then
  traps* them; the fixes are their own issues.
- Adding a headless-browser check — the viewer's HTTP `/session` + `/events` are the
  sanctioned sanitized surface and remain the monitoring mechanism.
- Departing from stdlib-only or adding third-party test deps.

## Consultation Log

*(Populated by porch's 3-way review at the spec checkpoint.)*
