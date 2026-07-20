# Live-agent testing suite (`tests/agent_sessions`)

An **opt-in**, oracle-backed test suite that drives real coding agents (claude, agy,
codex) under `ai-observe` and asserts that what the agent actually did on disk matches
the canonical `.jsonl` and what the browser viewer serves. It graduates the round-1/2
experiment harness (`experiments/1_driving_mechanism/harness.py`) into a maintained
capability.

Because it needs installed **and authenticated** agent CLIs, network, and minutes of
wall time, it is **excluded from the default CI matrix by construction** (see
[CI exclusion](#why-this-is-not-in-ci)) and is run manually by a developer who has the
tools set up.

## The one command

Run everything from the **repository root**:

```bash
# Full live suite: every scenario against every applicable, authenticated tool.
python3 -m tests.agent_sessions

# Tool-free plumbing + oracle self-tests (no agents, no network — runnable anywhere).
python3 -m tests.agent_sessions --selftest
```

> **Run from the repo root.** `tests/` has no `__init__.py`; the package resolves via
> PEP 420 namespace packages, which requires the repo root to be the working directory
> (and on `sys.path`, which `python3 -m` arranges). Running from inside `tests/` will
> fail to import.

### Flags

| Flag | Meaning |
|------|---------|
| `--tools claude,agy,codex` | Comma-separated subset of tools (default: all three). An **unknown** tool name is an error. A named tool that a selected scenario needs but is **missing from PATH** is a loud failure — never a silent skip. |
| `--scenarios single_write,timeline,…` | Comma-separated scenario **short names** (the `check_` prefix dropped). Default: all discovered. `--tools` and `--scenarios` compose (cartesian, minus applicability). |
| `--json` | Emit a JSON report of every `CheckResult` on stdout (the human summary always goes to stderr). |
| `--keep-artifacts DIR` | Persist raw artifacts to `DIR` instead of the default auto-cleaning temp dir. `DIR` must be **outside** the repo working tree, or under the ignored `tests/agent_sessions/.artifacts/` subtree — a tracked in-repo destination is rejected so raw artifacts never enter git. |
| `--timeout S` | Per-session agent timeout in seconds (default: 240). |
| `--selftest` | Run only the tool-free self-tests and exit (no agents). |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Checks ran; none failed. (A `known-bug:#N` annotation is **not** a failure while the bug is open.) |
| `1` | At least one check failed. |
| `2` | Usage / argument error (unknown tool or scenario, or a rejected `--keep-artifacts` path). |
| `3` | Nothing runnable — zero actual checks for the requested tools/scenarios (e.g. only non-applicable pairs). This is **loud, never a silent green**. |

## Scenarios

Each scenario drives one real session and asserts a **three-view oracle**:
**agent-actual** (files/content left in the workdir — always a hard assertion),
**canonical** (the events `ai-observe` recorded, hard except behind a known-bug gate),
and **viewer** (the sanitized events the browser viewer served, hard for completeness).

| Short name | Tools | What it exercises |
|------------|-------|-------------------|
| `single_write` | claude, agy, codex | A single observed file write, end to end across all three views. |
| `ephemeral` | claude, agy | Create-then-delete; **#32** home (annotated deletion drop). |
| `modify` | claude, agy | Seed-then-modify; the seed file survives and the modify is captured. |
| `subprocess` | claude, agy, codex | Writes performed by a child process of the agent. |
| `multi_turn` | claude, agy, codex | A chained multi-turn conversation under one wrapper; **#33** home (codex marker noise — fixed, now a hard assertion). |
| `timeline` | claude | A long paced run; the viewer sees events **incrementally**, then completely. |
| `degraded` | claude | A forced direct-parser failure (`AI_OBSERVE_TEST_FAIL_AFTER`); **#36** home (sidecar authority overstatement — fixed, now a hard assertion). |

## Per-tool prerequisites

Every tool must be **installed on `PATH`** *and* **logged in** — the suite invokes each
agent non-interactively, so an unauthenticated tool cannot prompt for login and instead
produces zero events, which the suite reports as a **loud, named failure** (never a
silent skip). To run only the tools you have, narrow with `--tools`.

The suite bakes in each tool's non-interactive invocation:

| Tool | Invocation | Notes |
|------|-----------|-------|
| `claude` | `claude -p <prompt> --dangerously-skip-permissions` | — |
| `agy` | `agy -p <prompt> --dangerously-skip-permissions --add-dir <workdir>` | `--add-dir` makes the throwaway workdir writable so `ai-observe` (watching that root) sees the writes. |
| `codex` | `codex exec --sandbox workspace-write <prompt>` | `workspace-write` lets codex write in the cwd without an approval prompt. |

### `--dangerously-skip-permissions` and throwaway workdirs

The suite drives agents **non-interactively with permission prompts disabled** so a run
can complete unattended. That means an agent can freely read/write/execute within its
working directory. Every scenario therefore runs each session in a **fresh, disposable
workdir** under the run's artifact directory (an auto-cleaning temp dir by default) —
**never a real project checkout**. If you point `--keep-artifacts` somewhere, treat that
directory the same way: disposable and sandbox-friendly. Do not run this suite against a
directory whose contents you care about.

## Known-bug annotations and how to flip one

Three `ai-observe` bugs are tracked via a rot-proof gate in
[`tests/agent_sessions/oracle.py`](../tests/agent_sessions/oracle.py); while a bug is
open, its signature is tolerated as **expected-and-annotated** rather than a hard
failure:

| Bug | Signature | Home scenario |
|-----|-----------|---------------|
| **#32** | Annotated `AT_FDCWD` deletion dropped (the delete is never reported). | `ephemeral` |
| **#33** | codex `/newroot` mount-namespace probing left unpaired `delete` events — **fixed**; the gate now hard-asserts pairing. | `multi_turn` |
| **#36** | On the direct-parser-failure path the sidecar labeled a snapshot-only `.jsonl` `authoritative_complete`, overstating fidelity — **fixed**; the gate now hard-asserts the `authoritative_net` role downgrade. | `degraded` |

While a bug is **active**, its gate asserts the bug **still reproduces** — so a fix that
lands *without* flipping the flag fails loudly ("flip the flag"). When the corresponding
`ai-observe` fix merges, flip the annotation to a hard assertion with a **one-line
change** in `oracle.py`:

```python
OPEN_BUGS[32].active = False   # was True
```

After the flip the gate asserts the *correct* behavior and fails if the bug ever
regresses. The gate is an assertion path end to end (never a `unittest.skip`), so it does
not interact with the CI no-silent-skip rule.

## Why this is not in CI

`ai-observe`'s CI fails loud on **any** unittest skip, and it provisions no agent CLIs,
no auth, and no network for live agents. A capability-gated skip would turn the matrix
red, and a live-agent leg would be non-deterministic and slow. So this suite is kept out
of the collected set **by construction**: its tests live under `tests/agent_sessions/`
as `selftest_*.py` and `check_*.py`, neither of which matches CI's `test_*.py` discovery
glob. Running `python3 -m unittest discover -s tests` collects exactly the same tests it
did before this suite existed. The tool-free `--selftest` tier can be run anywhere; the
live tier is the manual opt-in described above.

## Artifacts and sensitive data

Raw session artifacts (`.trace`, `.jsonl`, `.jsonl.partial`, `.jsonl.rebuilt`,
`.meta.json`, and the wrapper `*.stdout.log`/`*.stderr.log`) contain absolute paths,
wrapped-command argv/prompts, and raw syscall text — treat them as **sensitive** (see the
[README sensitive-data warning](../README.md)). They go to an auto-cleaning temp dir by
default; `--keep-artifacts` persists them, and `tests/agent_sessions/.gitignore` keeps
the in-repo `.artifacts/` destination out of git.

## Notes (round-2 findings)

These two behaviors are **informational and by design**, not bugs — documented here so
they are not rediscovered as surprises.

### F5 — the viewer requires its target `.jsonl` to exist at launch

The viewer is an **attach-to-existing-artifact** tool: pointing it at a missing `.jsonl`
prints `path does not exist` and exits. You cannot pre-launch a viewer that waits for a
session to appear. Once the artifact exists, a viewer attaching at **any** later time
still gets the complete set (backlog from byte 0 plus the live SSE stream). The suite
encodes this ordering directly (Decision 11): the drivers start the observed session
first, wait until the `.jsonl` exists and is non-empty, and only **then** attach the
in-process `ViewerMonitor`.

### F7 — orphaned-session recovery after an observer SIGKILL

A `SIGKILL` of the `ai-observe` coordinator (whole process group) skips finalize, so
**no `.meta.json` and no snapshot layer** are written. What survives is the live-tailed
`.jsonl` (direct events up to the kill, matching disk, no phantom entries) plus the raw
`.trace`. The viewer tolerates the meta-less `.jsonl` (`parser_status = None`) rather than
crashing. For manual recovery, the **`.trace` is the input**: it holds the full syscall
record from which a canonical `.jsonl` can be rebuilt. A `.meta.json`-per-launch invariant
therefore cannot be assumed.

## See also

- [docs/observe.md](observe.md) — the runtime model, artifacts, and event schema the
  oracle asserts against.
- [docs/viewer.md](viewer.md) — the browser viewer whose served events the suite checks.
- `experiments/FINDINGS.md`, `experiments/FINDINGS-round2.md` — the evidence base (the
  immutable historical record this suite supersedes).
