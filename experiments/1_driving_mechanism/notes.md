# Experiment 1: Establish the driving + monitoring mechanism

**Status**: Complete

**Date**: 2026-07-16

## Goal

Answer the genuinely uncertain feasibility question behind issue #31: **can an
agent drive another *real* interactive agent session while ai-observe wraps it,
and monitor the browser viewer concurrently?** Pick the mechanism(s) that work,
document trade-offs, and build a minimal reusable harness for experiments 2+.

Success criteria:
1. Drive a real agent (claude / agy / codex) to perform a filesystem action,
   non-interactively and repeatably, wrapped by `ai-observe`.
2. Confirm ai-observe captures the action end-to-end (canonical `.jsonl`).
3. Monitor the viewer concurrently and capture what it *serves* (not just that
   it starts) — without a headless browser if avoidable.
4. Package (1)–(3) into a reusable function experiments 2+ can import.

## Effort

~3 hours (setup + probing the three CLIs + harness + debugging the SSE reader).

## Approach

Two mechanisms were compared per the issue:

**Driving.** Non-interactive single-prompt invocation vs. tmux/expect send-keys.
All three tools ship a non-interactive mode, so that was chosen as the default:
it is scriptable, hermetic, and repeatable without a PTY. Exact invocations
(established by probing `--help` + live tests):

| Tool   | Non-interactive invocation | Writes into cwd? |
|--------|----------------------------|------------------|
| claude | `claude -p "<prompt>" --dangerously-skip-permissions` | yes |
| agy    | `agy -p "<prompt>" --dangerously-skip-permissions --add-dir <workdir>` | only with `--add-dir` (else its own `~/.gemini/antigravity-cli/scratch`) |
| codex  | `codex exec --sandbox workspace-write "<prompt>"` | yes |

tmux send-keys remains documented as the fallback for genuinely
interactive-only flows, but was **not** needed — every tool here has a print
mode, so the flaky PTY path is avoided.

**Monitoring.** HTTP-poll of the viewer server vs. a headless browser. The
viewer exposes everything the browser UI consumes over two endpoints
(`GET /session` sanitized JSON, `GET /events` sanitized SSE), so polling them
with the stdlib proves "the viewer showed X" without a headless browser (which
is heavier and not installed here).

## Environment & Reproduction

```bash
# from this directory
python3 harness.py --tool claude --session smoke      # single-tool smoke
python3 run_feasibility.py                            # all 3 tools, same task
```

**Dependencies**: none beyond the stdlib and the checkout's `bin/ai-observe`
(+ `src/ai_observe` on `PYTHONPATH`, which the harness sets for the viewer).
Tools driven must be installed and authenticated.

**Environment notes**: Linux, `strace` present, `kernel.yama.ptrace_scope=1`
(descendant tracing, which ai-observe relies on, works). claude 2.1.204,
codex-cli 0.144.5, agy 1.1.3. All three were already authenticated on this box.

## Code

- [`harness.py`](harness.py) — reusable harness. `run_observed_session(tool,
  prompt, session, workdir, outdir, ...)` drives one tool under ai-observe,
  monitors the viewer, and returns a combined report (agent result + on-disk
  canonical events + what the viewer served + actual workdir files).
  `ViewerMonitor` speaks raw-socket SSE to `/events`.
- [`run_feasibility.py`](run_feasibility.py) — runs the same write-a-file task
  under all three tools and writes `data/output/feasibility_summary.json`.

## Results

### Summary

**All four success criteria met.** Non-interactive driving works for claude,
agy, and codex; ai-observe captures each end-to-end; the viewer is monitorable
by HTTP-polling its sanitized endpoints. The same one-file task produced wildly
different event volumes across tools (claude 4, agy 4, codex 59), and codex's
run surfaced a concrete ai-observe accuracy divergence (below) on the very
first real scenario — validating that the harness is a useful testing tool, not
just a runner.

### Key Findings

1. **Non-interactive is sufficient and is the right default.** No PTY/tmux
   needed. Each tool has quirks worth baking into the harness: agy writes to a
   private scratch workspace unless given `--add-dir`; codex needs
   `--sandbox workspace-write` to write at all.

2. **The viewer is fully monitorable headless-free**, but the SSE stream has
   two traps the harness had to handle: (a) the viewer tails its jsonl
   *asynchronously*, so the backlog can be empty at connect and events arrive
   incrementally; (b) the server never closes the stream on its own (it can't
   know the writer exited). A raw non-blocking socket + `select`, reading until
   the event count *settles*, is the robust pattern. `urllib` read-after-timeout
   on this never-closing stream was unreliable.

3. **Viewer sanitization confirmed empirically.** `/events` frames carry
   `operation`, `path`, `old_path/new_path`, `result`, `source`, `confidence`,
   `timestamp` — but **strip** `raw_syscall`, `command`/argv, `pid`,
   `session_id`. Absolute `path` is **retained** (the tree needs it), so the
   viewer still exposes absolute paths even though it hides argv and syscalls.

4. **Cross-tool event-volume divergence is large** for an identical task
   ("create one file"): claude 4, agy 4, codex 59 canonical events. claude
   writes atomically (temp file + `rename`) so its direct stream shows a
   `create <tmp>` + `rename → final`; agy writes in place (3 `modify` + net
   `create`); codex is filesystem-noisy.

5. **ai-observe accuracy divergence found via codex (the headline result).**
   codex runs under a **mount-namespace sandbox**: the *same* logical directory
   appears under two paths in the trace — `mkdir("/newroot/.../work/codex/.git")`
   for creation vs `rmdir(".../work/codex/.git")` (canonical) for removal.
   ai-observe filters events by literal watched-root prefix, so it captured the
   54 canonical-path `rmdir`s as `delete`s but **dropped the 54 `/newroot/…`
   `mkdir`s** — producing an asymmetric stream of **54 deletes with zero
   matching creates**, implying files were destroyed when the net effect was
   nothing. The snapshot (inferred/net) layer correctly showed only `codex.txt`
   created; the *disagreement between the two provenance layers is itself the
   diagnostic signal*. This is precisely the "what the agent did vs. what
   ai-observe reported" gap issue #31 asked us to surface.

6. **Watched-root scoping works.** codex hammered `~/.codex` with 404 `mkdir` /
   216 `rmdir` calls in the raw trace; ai-observe correctly excluded every one
   (only the 54 inside the watched workdir were reported).

### Metrics

| Tool   | Duration | Canonical events | by source (strace/snapshot) | by operation | workdir files |
|--------|----------|------------------|-----------------------------|--------------|---------------|
| claude | 17.3 s   | 4  | 3 / 1 | create 2, modify 1, rename 1 | claude.txt |
| agy    | 16.4 s   | 4  | 3 / 1 | modify 3, create 1           | agy.txt    |
| codex  | 24.0 s   | 59 | 58 / 1 | delete 54, modify 4, create 1 | codex.txt |

### Output Files

- `data/output/feasibility_summary.json` — curated per-tool summary (committed).
- `data/output/*.jsonl`, `*.trace`, `*.meta.json` — raw ai-observe artifacts
  (git-ignored: large + sensitive per the repo's data warning).

## What Worked

- Non-interactive driving across all three tools — the core feasibility bet.
- Raw-socket SSE reader with a settle timeout — reliable viewer capture.
- Comparing three provenance/visibility surfaces at once (agent's actual files
  vs. canonical `.jsonl` vs. viewer SSE) — this triangulation is what exposed
  the codex `/newroot` asymmetry.

## What Didn't Work

- First SSE reader used `urllib` + read timeout; flaky on a stream that never
  closes and whose backlog is populated asynchronously. Replaced with raw
  socket + `select`.
- First harness run passed relative workdir/outdir and set `cwd=workdir`,
  double-resolving paths (`_smoke_work/_smoke_work`); fixed by resolving all
  paths to absolute up front.

## Next Steps

1. **Immediate**: reuse the harness for scenario coverage (Experiment 2):
   multi-turn, subprocess-spawning, and error/interrupt scenarios, plus a
   cross-tool matrix.
2. **Follow-up**: the codex `/newroot` create/delete asymmetry deserves a
   focused write-up and likely a GitHub issue against ai-observe — path
   canonicalization before watched-root filtering would fix the dropped
   `mkdir`s.
3. **Production path**: if the harness proves broadly useful, graduate it to a
   SPIR-spec'd testing capability (see the top-level findings summary).

## References

- Issue #31; ai-observe `README.md`, `docs/observe.md`, `docs/viewer.md`.
- Viewer routes: `src/ai_observe/viewer/server.py` (`/session`, `/events`).
