# Experiment 8: agent crash (SIGKILL / nonzero mid-write) vs. graceful signal

**Status**: Complete

**Date**: 2026-07-17

**Gap (issue #35, priority 5)**: Exp 3/7 covered a graceful SIGINT (forwarded to
a cooperative agent). This covers uncooperative terminations — SIGKILL and
nonzero-mid-write — and what ai-observe reports.

## Goal

- **Question**: when the agent is SIGKILLed (cannot flush or be forwarded a
  handler), or the whole observed group including ai-observe is SIGKILLed, or the
  agent exits nonzero mid-write — what artifacts result and are they coherent?
- **Success criteria**: the observer, when it survives, finalizes an accurate
  authoritative artifact of the pre-termination writes; when the observer itself
  is killed, the leftover state is accurate (no phantom/corruption) even if
  unlabeled; document each terminal state against the Artifact contract.

## Effort

~1.5 hours.

## Approach

Three cases (claude; tree is `ai-observe(py) → strace → agent → shell`):

- **agent_sigkill** — externally SIGKILL the *agent subtree* (all descendants of
  the strace pid), leaving ai-observe(py)+strace alive. strace observes its tracee
  die and exits; ai-observe finalizes. (Descendants found via `pgrep -P`.)
- **observer_sigkill** — SIGKILL the *entire process group* (ai-observe included).
  The coordinator never runs finalize.
- **nonzero_midwrite** — the agent's shell command exits 3 after writing 2 files
  (graceful process exit, just rc≠0).

## Environment & Reproduction

```bash
python3 crash.py
```

Requires claude + ai-observe from the checkout. Raw artifacts in `data/output/`
(git-ignored); curated `crash_report.json` committed.

## Code

- [`crash.py`](crash.py) — `_descendants` (pgrep-based tree walk), the three case
  drivers, and a shared `_inspect` recording artifacts / sidecar / viewer /
  captured-vs-actual (+ phantom check).

## Results

### Summary

When the **observer survives** (agent SIGKILL or nonzero exit), ai-observe
finalizes a clean authoritative `.jsonl` of the pre-termination writes — the same
contract as a graceful interrupt. When the **observer itself is SIGKILLed**, it
leaves an orphaned live-tailed `.jsonl` (accurate direct events up to the kill) +
the raw `.trace`, but **no `.meta.json` and no snapshot layer**; the viewer
tolerates the meta-less file rather than crashing.

### Metrics

| case | coord rc | finalized | .meta | parser_status | authoritative | surviving artifacts | captured==actual | phantom |
|------|----------|-----------|-------|---------------|---------------|---------------------|------------------|---------|
| agent_sigkill | 137 | ✅ | ✅ | ok | `.jsonl` | jsonl+meta+trace | ✅ f1,f2 | none |
| observer_sigkill | −9 | ❌ | ❌ | (none) | (none) | jsonl(live)+trace | ✅ f1,f2 | none |
| nonzero_midwrite | 0 | ✅ | ✅ | ok | `.jsonl` | jsonl+meta+trace | ✅ f1,f2 | none |

### Key Findings

1. **Agent SIGKILL → observer finalizes cleanly (positive).** rc 137 (128+9); the
   authoritative `.jsonl` + sidecar are written, `parser_status="ok"`, captured
   files equal on-disk files, no phantom. An abrupt tracee death is handled just
   like a graceful exit — strace records the exit and ai-observe finalizes.

2. **F7 (informational) — observer SIGKILL leaves an accurate-but-unlabeled
   orphan.** Because SIGKILL cannot be caught, the coordinator never runs
   finalize: **no `.meta.json`, no snapshot layer**. What survives is the
   live-tailed `.jsonl` (only direct/strace events up to the kill — here the 4
   modifies for f1,f2, which correctly match disk) plus the raw `.trace`. The
   viewer, pointed at this file, reports `parser_status=None` /
   `authoritative_artifact=None` and still renders the events (it **tolerates** a
   meta-less artifact). The surviving `.trace` allows manual post-hoc recovery of
   the full canonical if needed, but ai-observe has no built-in "recover orphaned
   session" command. This confirms Exp 3's F4 caveat: **a `.meta.json`-per-launch
   invariant cannot be assumed** under a hard kill of the observer. Not a bug —
   inherent to SIGKILL — but worth documenting.

3. **Nonzero mid-write → clean finalize.** `claude -p` exits 0 even though its
   inner shell command exited 3 (the tool masks the child rc), so ai-observe
   finalizes normally with an authoritative artifact. The distinction that matters
   to ai-observe is *how the observed process tree terminates* (signal vs. clean
   exit), not the numeric exit code.

## What Worked

- The `pgrep -P` descendant walk cleanly isolates "kill the agent" from "kill the
  observer", producing two genuinely different terminal states.

## What Didn't Work

- N/A — all three cases produced their intended terminal state.

## Next Steps

- Gap **covered**. F7 is informational — recommend a docs note on orphaned-session
  artifacts (and that the raw `.trace` is the recovery input) after an observer
  hard-kill. No bug to file.

## References

- Issue #35 (gap 5); Exp 3 F4; arch.md Artifact contract.
- Cross-experiment summary: `experiments/FINDINGS-round2.md`.
