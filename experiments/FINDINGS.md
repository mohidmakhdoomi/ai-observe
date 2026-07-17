# Findings — Harness for testing ai-observe against real agent sessions (issue #31)

**Status**: Complete · **Date**: 2026-07-16 · **Protocol**: EXPERIMENT (soft mode)

Three experiments built a repeatable harness for driving **real** agent sessions
(Claude Code, agy, codex) under `ai-observe` and observing what it captures vs.
what actually happened. This file is the cross-experiment summary: the coverage
matrix, the concrete ai-observe accuracy findings, and a graduation
recommendation.

- Exp 1 — [`1_driving_mechanism/notes.md`](1_driving_mechanism/notes.md): feasibility + the reusable harness.
- Exp 2 — [`2_scenario_coverage/notes.md`](2_scenario_coverage/notes.md): scenario × tool coverage matrix.
- Exp 3 — [`3_interrupt_recovery/notes.md`](3_interrupt_recovery/notes.md): the interrupt/recovery error path.

## Environment

Linux (WSL2), `strace` present, `kernel.yama.ptrace_scope=1`. All three tools
present **and** authenticated: claude 2.1.204, codex-cli 0.144.5, agy 1.1.3.
(The issue flagged agy/codex availability as unverified — both are available
here and were exercised.) ai-observe run from the checkout via `bin/ai-observe`.

## The harness (what graduated out of Exp 1)

`experiments/1_driving_mechanism/harness.py` — stdlib-only, importable by any
experiment:

- **Driving** = non-interactive single prompt. Chosen over tmux/expect send-keys
  because all three tools ship a print/exec mode; it is scriptable, hermetic,
  repeatable, and needs no PTY. Per-tool invocation (with quirks baked in):
  - claude: `claude -p "<prompt>" --dangerously-skip-permissions`
  - agy: `agy -p "<prompt>" --dangerously-skip-permissions --add-dir <workdir>` (else writes to its private scratch workspace)
  - codex: `codex exec --sandbox workspace-write "<prompt>"`
- **Monitoring** = HTTP-poll of the viewer's sanitized `/session` + `/events`
  (raw-socket SSE reader) — proves "the viewer showed X" with no headless
  browser. `run_observed_session(...)` returns agent result + on-disk canonical
  events + viewer events + actual workdir files, so callers can triangulate
  agent-reality vs. ai-observe vs. viewer.

## Coverage matrix (scenario × tool → what ai-observe got right/wrong)

Legend: ✅ accurate · ⚠️ accurate-but-noisy/misleading · ❌ wrong/missing.
Cell = canonical events (by op); every "actual files" check passed on the agent
side — divergences are all in **ai-observe's reporting**.

| Scenario | claude | agy | codex |
|----------|--------|-----|-------|
| **single write** (Exp 1) | ✅ 4 (tmp+rename atomic write visible in direct layer; snapshot = net create) | ✅ 4 (in-place write) | ⚠️ 59 — real file captured, but 54 unpaired `delete`s of `.git/.agents/.codex` sandbox markers (see F1) |
| **subprocess** — 3 files via grandchild shell (Exp 2) | ✅ 9, all 3 files | ✅ 9, all 3 files | ⚠️ 15, all 3 files + 6 marker-noise deletes |
| **ephemeral** — create then delete (Exp 2) | ❌ deletion MISSED (F2); file shown as present | ❌ deletion MISSED (F2) | ⚠️ real `unlink(abs)` captured, but 55/57 events are marker noise |
| **modify** — append to existing (Exp 2) | ✅ 1 modify | ✅ 2 modify | ⚠️ 2 real modifies buried under 36 marker-noise deletes |
| **mid-session interrupt** (Exp 3) | ✅ accurate partial capture, authoritative `.jsonl` | — | — |

## Concrete ai-observe findings

### F1 — codex's mount-namespace sandbox breaks path-prefix filtering *(accuracy / severity: med-high)*
codex probes its workspace by repeatedly creating and removing `.git`,
`.agents`, `.codex` marker dirs. The **`mkdir`s go through a `/newroot/...`
path** (codex's sandbox mount namespace) while the **`rmdir`s use the canonical
path**. ai-observe filters events by literal watched-root prefix, so it keeps
the canonical `rmdir`s (as `delete`s) but drops the `/newroot/…` `mkdir`s → an
**asymmetric stream of dozens of unpaired deletes** implying destruction where
the net effect was nothing. This dominates *every* codex scenario (e.g. 55 of 57
events on a create-one-file task). The snapshot (net) layer stays correct; the
**disagreement between the two provenance layers is the diagnostic**.
*Fix direction*: canonicalize paths (resolve the sandbox `/newroot` prefix, or
compare by resolved real path) before watched-root filtering.

### F2 — relative `unlinkat(AT_FDCWD<dir>, …)` deletions are silently dropped *(correctness / severity: high)*
claude and agy delete files with `unlinkat(AT_FDCWD<dir>, "f", 0) = 0`. strace's
default path-decoding annotates the dirfd as `AT_FDCWD<...>`; ai-observe's
`_at_path` compares `arg != "AT_FDCWD"`, mistakes the annotated form for a real
directory fd, fails to resolve it, and **discards the delete event**. Verified by
driving `trace_parser` directly:

| trace form | event? |
|---|---|
| `unlinkat(AT_FDCWD<dir>, "f", 0)` (annotated — what real tools emit) | **NO** ❌ |
| `unlinkat(AT_FDCWD, "f", 0)` (plain) | delete ✅ |
| `unlink("<abs>")` | delete ✅ |

Impact: for the common libc deletion path, ai-observe shows a file as
created/modified but **never reports its deletion** — the viewer misrepresents
the file's final state. *Fix direction*: in `_at_path`/`_dirfd_path`, treat a
dirfd of `AT_FDCWD` with **or without** a `<...>` annotation as "resolve against
cwd" (or extract the annotation).

### F3 — viewer sanitization confirmed; absolute paths retained *(informational)*
`/events` frames carry `operation`, `path`, `old_path/new_path`, `result`,
`source`, `confidence`, `timestamp`, and **strip** `raw_syscall`, argv/`command`,
`pid`, `session_id`. Absolute `path` is **retained** (the tree needs it) — so the
viewer still exposes absolute filesystem paths even though it hides argv/syscalls.
Matches the README's sensitive-data posture but worth stating explicitly.

### F4 — interrupt/recovery is robust; `.partial`/`.rebuilt` are not signal paths *(positive)*
A mid-session SIGINT finalizes an authoritative `.jsonl` (`parser_status: ok`)
capturing **exactly** the files written before the interrupt (captured ==
actual), with no phantom entries. The degraded `.partial`/`.rebuilt` artifacts
are **never** produced by a clean signal — they belong to the parse-failure /
live-timeout paths. Caveat: a *very early* interrupt (during agent startup,
before any watched-root change) can leave an empty or entirely absent session
record — a `.meta.json`-per-launch invariant can't be assumed.

### What works well *(positive)*
Process-tree scoping (grandchild-shell writes captured for all tools);
watched-root scoping (codex's hundreds of `~/.codex` ops correctly excluded);
the two-layer provenance model (direct strace vs. inferred snapshot/net) behaves
as designed — including the *intended* half of issue #18 where the snapshot
omits a net-zero ephemeral file.

## Recommendation — should this graduate to a maintained SPIR capability?

**Yes, partially — graduate the harness; keep scenarios as EXPERIMENT.**

- **Graduate `harness.py`** into a small, maintained test-support module (e.g.
  `tests/agent_sessions/` or `tools/`). It is stdlib-only, already abstracts the
  three tools' quirks, and gave a **high finding-per-line yield** (two real bugs
  on the first real scenarios). A SPIR spec should cover: (a) making it robust to
  tool absence / non-auth (skip-with-reason, honoring the CI "no silent skips"
  rule — capability-gate, don't hide), (b) an assertion layer (captured-vs-actual
  oracle), (c) fixed viewer port allocation to avoid collisions.
- **Do NOT** put live-agent runs in the default CI matrix: they need network +
  auth + minutes and are non-deterministic. Gate them behind an explicit,
  opt-in capability (a marker/label), consistent with `arch-critical.md`'s
  "CI fails loud on any skip" rule — so an un-provisioned CI turns red, not
  silently green — or run them as a scheduled/manual job.
- **File F1 and F2 as ai-observe bugs** (both have pinned root causes and minimal
  repros here). F2 is the higher priority (silent data loss on a common path).

## Reproduce

```bash
cd experiments/1_driving_mechanism && python3 run_feasibility.py       # feasibility + F1
cd experiments/2_scenario_coverage && python3 scenarios.py             # coverage matrix + F2
cd experiments/3_interrupt_recovery && python3 interrupt.py            # F4
```

Raw ai-observe artifacts (`.trace`/`.jsonl`/`.meta.json`) are git-ignored (large
+ sensitive per the repo's data warning); committed `*_summary.json` /
`*_matrix.json` / `*_report.json` hold curated, relative-path-only evidence.
