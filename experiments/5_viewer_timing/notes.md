# Experiment 5: viewer opened mid-session vs. before session start

**Status**: Complete

**Date**: 2026-07-17

**Gap (issue #35, priority 2)**: deferred by Exp 2 and Exp 3. Does the viewer's
async jsonl tailing deliver a complete backlog when a client attaches late?

## Goal

- **Question**: if a viewer attaches after the session's `.jsonl` already has
  events (and is still growing), does it see the full history — backlog *plus*
  the events streamed after it attached? And can a viewer be pre-launched before
  the session exists at all?
- **Success criteria**: for any attach time at-or-after the artifact exists, the
  viewer's served event set equals the canonical `.jsonl` (nothing missing).

## Effort

~2 hours (including diagnosing and correcting an initial harness-settle artifact).

## Approach

Two distinct sub-questions with **different answers**:

- **Q-A — attach before the `.jsonl` exists**: probed directly
  (`_probe_pre_existence`). The viewer CLI validates its target path at startup.
- **Q-B — attach mid-session (file exists, still growing)**: drive one paced
  ai-observe session (claude writes 8 files, ~1/sec) and attach two viewers in
  background threads — `early` (the instant the `.jsonl` first exists) and `mid`
  (a fixed offset later) — plus an `after` replay viewer on the finalized file.
  Compare each viewer's event set (keyed by op+basename+source) to the canonical
  `.jsonl`.

**Timing correction (documented so it isn't repeated)**: strace events stream as
files are written, but the snapshot (net) backend appends its events in one burst
at *finalization*, after the agent exits. An initial version used `settle=4`,
which closed the SSE collection in the gap before that burst and spuriously
reported the snapshot events as "missing". `settle=9` spans the burst. This was a
**harness artifact, not a viewer defect** — confirmed by an isolation test where
the viewer live-pushed 8/8 and 6/6 events over an open connection.

## Environment & Reproduction

```bash
python3 viewer_timing.py
```

Requires claude on PATH + authenticated; ai-observe from the checkout. Raw
artifacts in `data/output/` (git-ignored); curated `viewer_timing_report.json`
committed.

## Code

- [`viewer_timing.py`](viewer_timing.py) — `_probe_pre_existence` (Q-A),
  `BackgroundViewer` (attaches on file-existence + offset, Q-B), set-difference
  completeness oracle.

## Results

### Summary

Once the `.jsonl` exists, a viewer attaching at **any** later time (mid-session or
after finalization) receives the **complete** event set — backlog from byte 0
plus everything streamed afterward. The **only** limitation is that the viewer
refuses to start if its target artifact does not yet exist.

### Key Findings

1. **F5 (informational / by-design): the viewer requires its target `.jsonl` to
   exist at launch.** `python -m ai_observe.viewer <missing.jsonl>` prints
   `path does not exist` and exits (0). You cannot pre-launch a viewer and have it
   wait for the session to appear — the viewer is an attach-to-existing-artifact
   tool. Practical implication for a live-observe workflow: start the session
   first, then launch the viewer once the `.jsonl` appears.
2. **Late/mid-session attach loses nothing (Q-B).** Attaching when only 2 events
   were on disk, or when 12 were, both yielded the full 24 — `complete_backlog =
   True, missing = 0` in every case. The viewer serves the persistent backlog and
   live-streams the remainder over the same SSE connection.

### Metrics

Canonical: 24 events (16 strace, 8 snapshot). `distinct keys = 16` (the 16 strace
modifies dedup to 8 by op+basename+source).

| attach timing | events on disk at attach | viewer served | complete backlog | missing |
|---------------|--------------------------|---------------|------------------|---------|
| early | 2 | 24 | ✅ | 0 |
| mid | 12 | 24 | ✅ | 0 |
| after (replay) | finalized | 24 | ✅ | 0 |
| **pre-existence (Q-A)** | file absent | — (viewer refused to start) | n/a | n/a |

## What Worked

- The completeness oracle (set difference viewer-vs-canonical) cleanly separates
  a real gap from a timing artifact.
- The isolation test (`ViewerMonitor` on a manually grown jsonl) proved live-push
  works independently of agent timing.

## What Didn't Work

- The initial `settle=4` produced a false "mid missed snapshot events" result;
  root-caused to the finalization-burst gap, not the viewer. Corrected.

## Next Steps

- Gap **covered**. F5 is an informational/UX limitation — flag to architect for a
  docs note ("launch the viewer after the session artifact exists"); not a data
  bug.

## References

- Issue #35 (gap 2); Exp 2/3 deferred next-steps; viewer server SSE handler
  (`src/ai_observe/viewer/server.py` — backlog then `wait_for_more` live push).
- Cross-experiment summary: `experiments/FINDINGS-round2.md`.
