# Experiment 7: cross-tool mid-session interrupt (agy, codex)

**Status**: Complete

**Date**: 2026-07-17

**Gap (issue #35, priority 4)**: Exp 3's interrupt/recovery scenario (F4) was
claude-only. Repeat it for agy and codex.

## Goal

- **Question**: does the mid-session SIGINT contract from Exp 3 (F4) hold across
  tools with different process trees and — for codex — a mount-namespace sandbox?
- **Success criteria**: for each tool, a mid-session SIGINT finalizes an
  authoritative `.jsonl` (no degraded artifact) capturing exactly the files
  written before the interrupt, matching what is actually on disk, with no phantom
  captures.

## Effort

~1 hour.

## Approach

Reuse Exp 3's process-group SIGINT, but with a **robust kill trigger** instead of
a fixed offset: tools have very different startup latencies, so we poll the
workdir and send SIGINT a short grace (1.5s) *after the first paced file appears*.
This guarantees the interrupt lands mid-session (≥1 write captured, more pending)
for every tool. Per-tool invocation comes from the shared harness `TOOLS` command
builders.

## Environment & Reproduction

```bash
python3 interrupt_xtool.py --tools agy,codex,claude
```

Requires the tools on PATH + authenticated; ai-observe from the checkout. Raw
artifacts in `data/output/` (git-ignored); curated `xtool_interrupt_report.json`
committed.

## Code

- [`interrupt_xtool.py`](interrupt_xtool.py) — launches each tool under ai-observe
  in a new session, SIGINTs the process group after the first file appears, then
  records artifacts / sidecar / viewer / captured-vs-actual (with a phantom check).

## Results

### Summary

The Exp 3 interrupt/recovery contract (F4) **generalizes across all three tools**.
A mid-session SIGINT finalizes cleanly with `parser_status = "ok"`, produces **no**
degraded `.partial`/`.rebuilt` artifact, and captures exactly the pre-interrupt
files with **zero phantom captures**. codex's mount-namespace sandbox does not
break the interrupt/finalize path.

### Metrics

| tool | first file @ | interrupted mid-session | rc | parser_status | degraded artifact | captured | actual | phantom |
|------|--------------|-------------------------|----|---------------|-------------------|----------|--------|---------|
| agy | 13.0s | ✅ | 1 | ok | none | f1,f2 | f1,f2 | none |
| codex | 10.8s | ✅ | 1 | ok | none | f1,f2 | f1,f2 | none |
| claude | 12.4s | ✅ | 1 | ok | none | f1,f2 | f1,f2 | none |

### Key Findings

1. **Clean finalization on interrupt for every tool.** All three produce an
   authoritative `.jsonl` (`parser_status="ok"`), never the degraded paths — a
   graceful SIGINT is a clean shutdown, consistent with F4.
2. **No phantom captures, no missed pre-interrupt writes.** Captured `.txt` files
   exactly equal the files actually on disk at interrupt time.
3. **codex's sandbox is transparent to the interrupt path.** Despite the
   `/newroot` mount namespace (#33), the SIGINT/finalize flow works identically;
   the `.txt` oracle is clean (marker-noise deletes are filtered out of the
   `.txt` comparison and don't affect the fidelity result).

## What Worked

- Kill-after-first-file removes per-tool startup-latency guesswork; the interrupt
  reliably lands mid-session everywhere.

## What Didn't Work

- N/A.

## Next Steps

- Gap **covered**. No new bug (only the expected #33 marker-noise appears for
  codex, in filtered-out non-`.txt` events).

## References

- Issue #35 (gap 4); Exp 3 (`experiments/3_interrupt_recovery/notes.md`, F4).
- Cross-experiment summary: `experiments/FINDINGS-round2.md`.
