# Experiment 3: Interrupt / recovery (error path)

**Status**: Complete

**Date**: 2026-07-16

## Goal

Cover the error-path half of issue #31: interrupt a real agent session
mid-flight (SIGINT, as a terminal Ctrl-C would) and verify ai-observe's
degraded-session contract — which artifacts survive, what the sidecar/viewer
call *authoritative*, and whether the partial filesystem changes made before
the interrupt are captured **accurately** (no phantom or missing files).

Success criteria: after an interrupt, ai-observe finalizes a coherent session
record whose reported changes match what actually landed on disk.

## Effort

~1.5 hours (Popen/process-group signaling + tuning kill timing to claude's
startup latency).

## Approach

`interrupt.py` drives claude through a **paced** task — a shell loop writing one
file/second (`for i in $(seq 1 6); do echo file$i > f$i.txt; sleep 1; done`) —
under ai-observe, launched in its own process group (`start_new_session=True`)
so `killpg(SIGINT)` faithfully mimics Ctrl-C on the whole tree. Three cases:

- **early_interrupt** (kill @3.5 s) — lands during claude's LLM/startup phase,
  *before* the shell loop runs (claude spends ~11 s before its first tool call).
- **mid_interrupt** (kill @13 s) — lands after 1–2 files are written.
- **clean** control — no interrupt.

Each case inspects artifact existence, sidecar `authoritative_event_path`,
canonical events, viewer `parser_status`/`authoritative_artifact`, and compares
**captured .txt files vs. actual .txt files on disk**.

## Environment & Reproduction

```bash
python3 interrupt.py --early-kill 3.5 --mid-kill 13.0
```

Same environment as Experiments 1–2. Timing is claude-startup-dependent; the
defaults are tuned to this machine.

## Code

- [`interrupt.py`](interrupt.py) — process-group SIGINT driver + artifact/recovery inspector.

## Results

| Case | rc | Files written | ai-observe artifacts | authoritative | parser_status | captured == actual? |
|------|----|---------------|----------------------|---------------|---------------|---------------------|
| early_interrupt | 130 (SIGINT) | none | `.jsonl` (empty) + `.meta.json` + `.trace`¹ | `jsonl` | (n/a, 0 events) | ✅ both empty |
| mid_interrupt   | 1  | f1, f2 | `.jsonl` + `.meta.json` + `.trace` | `jsonl` | ok | ✅ `[f1,f2]==[f1,f2]` |
| clean           | 0  | f1..f6 | `.jsonl` + `.meta.json` + `.trace` | `jsonl` | ok | ✅ all 6 |

¹ **Startup race**: across repeated early-interrupt runs the outcome varied —
one run produced **no artifacts at all** (not even the trace), another produced
an **empty** `.jsonl` + sidecar + trace. There is a narrow window at session
start where a SIGINT lands before ai-observe has wired up artifact writing.

### Key Findings

1. **Interrupt recovery is reliable, and a clean SIGINT does *not* trigger the
   degraded artifacts.** For both mid-interrupt and clean runs, ai-observe
   finalized an authoritative `.jsonl` (`role: authoritative_complete`,
   `parser_status: ok`, 0 warnings). The `.jsonl.partial` / `.jsonl.rebuilt`
   recovery artifacts were **never produced** — they are for parse-failure and
   live-timeout paths, not for a graceful signal shutdown. The signal handler's
   forward-then-grace-then-finalize path works.

2. **Partial capture is accurate.** The mid-session interrupt captured exactly
   the two files that had been written (`f1.txt`, `f2.txt`) — 6 canonical events
   (2 create + 4 modify via both provenance layers) — and *nothing phantom* for
   the four files the loop never reached. Reported == reality.

3. **Very early interrupt can leave an empty or absent session record.** When
   the interrupt lands during agent startup (before any watched-root change),
   the session record is empty or missing. Low impact (there was nothing to
   observe), but a consumer that keys off "a `.meta.json` must exist per launch"
   can't rely on that invariant. Worth a note in ai-observe's docs.

## What Worked

- `start_new_session=True` + `killpg(SIGINT)` to mimic a real Ctrl-C on the tree.
- Pacing the task (1 file/sec) so the interrupt reliably lands mid-write, and
  tuning kill time to claude's ~11 s pre-tool startup.
- Comparing captured-vs-actual files as the correctness oracle.

## What Didn't Work

- First attempt killed at 3.5 s expecting mid-write, but claude hadn't begun the
  shell loop (LLM startup latency); had to add a later `--mid-kill`.

## Next Steps

1. **Immediate**: roll into the top-level findings summary as the positive
   counterpoint to the two accuracy gaps from Experiments 1–2.
2. **Follow-up experiments**: force the *degraded* paths deliberately (corrupt a
   trace / induce a live-parse timeout via `AI_OBSERVE_LIVE_JOIN_TIMEOUT`) to
   exercise `.partial`/`.rebuilt` authority, which a clean SIGINT never hits;
   and viewer-opened-mid-session vs. before.

## References

- Issue #31; `src/ai_observe/observe.py` signal handling (SIGINT/SIGTERM
  forward + `SIGNAL_GRACE`, authoritative-path finalization).
