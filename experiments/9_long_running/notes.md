# Experiment 9: long-running command → incremental streaming to the viewer

**Status**: Complete

**Date**: 2026-07-17

**Gap (issue #35, priority 6)**: verify events stream to the viewer incrementally
*during* a long-running command, not only at the end.

## Goal

- **Question**: during a genuinely long observed command, do events become visible
  in the viewer progressively (as the command runs), or only in a dump at the end?
- **Success criteria**: the viewer-visible event count strictly increases across
  ≥3 distinct sample ticks *while the command is still running*, a meaningful
  fraction is visible by the midpoint, and the final set is complete (== canonical).

Distinct from Exp 5: Exp 5 proved **completeness** (a late attach loses nothing).
This proves **timeliness** (events arrive during the run).

## Effort

~1 hour.

## Approach

Drive a long ai-observe session (claude runs a shell loop writing one file every
~1.6s for 12 files ≈ 25s of active writing after ~11s startup). Attach one viewer
as soon as the `.jsonl` exists, then **sample** it every ~2s: at each tick open a
fresh `/events` connection and count the backlog it immediately delivers (exactly
what a browser connecting at that instant would receive), alongside the on-disk
`.jsonl` line count and files-written-so-far. Build a timeline.

Why sample via a fresh `/events` backlog read: `/session` exposes no live event
count, but each new SSE connection is served the current backlog synchronously —
a precise "what would a viewer show right now" probe.

## Environment & Reproduction

```bash
python3 incremental.py
```

Requires claude + ai-observe from the checkout. Raw artifacts in `data/output/`
(git-ignored); curated `incremental_report.json` committed.

## Code

- [`incremental.py`](incremental.py) — long paced driver, per-tick fresh-`/events`
  sampling into a timeline, and an incremental oracle (count distinct increasing
  viewer-visible values seen during the run) + final-completeness check.

## Results

### Summary

**Incremental streaming is confirmed.** The viewer-visible count rises in lockstep
with on-disk writes across 10 increasing ticks *during* the run (2 → 24 by t=32s;
midpoint 16 visible), not in an end-of-run dump. After finalization the 12 snapshot
(net) creates land in one burst, taking the viewer to the full canonical of 36.

### Timeline (abridged)

| t (s) | viewer_visible | disk_lines | files_written |
|-------|----------------|------------|---------------|
| 14 | 2 | 2 | 1 |
| 18 | 6 | 8 | 4 |
| 22 | 12 | 12 | 6 |
| 26 | 16 | 16 | 9 |
| 30 | 22 | 22 | 11 |
| 32 | 24 | 24 | 12 |
| (final, post-finalize) | **36** | 36 | 12 |

`distinct_increasing_ticks_during_run = 10`, `midpoint_visible = 16`,
`final_visible = 36 == canonical`. **INCREMENTAL_CONFIRMED = True**,
**FINAL_COMPLETE = True**.

### Key Findings

1. **Direct (strace) events stream live.** The viewer reflects each file write
   within a sample tick of it hitting the canonical `.jsonl` — the tailer +
   broadcaster push append batches over the open SSE connection as the trace grows.
2. **Snapshot (net) events arrive at finalization, in one burst.** The 12 net
   creates (24 → 36) appear only after the agent exits and the snapshot backend
   runs. This is the two-layer provenance model behaving as designed: direct =
   live/incremental, inferred/net = end-of-session.
3. **No end-loading of the direct layer.** A viewer watching a long session sees
   meaningful, growing content throughout — the property that makes live watching
   useful.

## What Worked

- Fresh-`/events`-per-tick sampling gives an exact, timestamped "visible now"
  count without a headless browser and without disturbing the writer.

## What Didn't Work

- N/A.

## Next Steps

- Gap **covered**. No new bug. (Note for consumers: the net/snapshot layer is a
  finalization-time addition, not a live stream — expected, not a defect.)

## References

- Issue #35 (gap 6); Exp 5 (completeness counterpart); viewer server SSE handler.
- Cross-experiment summary: `experiments/FINDINGS-round2.md`.
