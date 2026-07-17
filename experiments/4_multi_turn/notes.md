# Experiment 4: multi-turn / repeated prompting in ONE observed session

**Status**: Complete

**Date**: 2026-07-17

**Gap (issue #35, priority 1)**: the original #31 use case — multiple prompts
within a single `ai-observe` session — was never exercised; every round-1 run was
one-shot. Does ai-observe capture file operations from turns 2, 3, … as well as
turn 1, under a single wrapper?

## Goal

- **Question**: under one `ai-observe` wrapper spanning a multi-turn conversation,
  are later-turn file operations captured with the same fidelity as turn 1, for
  each tool?
- **Success criteria**: for a 3-turn chain (create A, create B, append to A), the
  canonical `.jsonl` must show a write landing on the turn-2 file *and* a second
  write landing on the turn-1 file (the turn-3 op) — i.e. no later-turn ops lost.
  Actual on-disk files must match; the viewer must serve the same events.

## Effort

~2 hours (design + the codex resume-flag debugging + 3-tool runs).

## Approach

**Driving mechanism = one `ai-observe` wrapper over a chained shell driver**:
`ai-observe -- bash -lc "<turn1> && <turn2> && <turn3>"`. This is a single
ai-observe process (one strace tree); the per-turn agent invocations are
grandchildren captured by descendant tracing (already proven by round-1's
subprocess scenario). Per-turn conversation continuity is the *agent's* job, via
each tool's resume/continue print mode:

| tool | turn 1 | turns 2+ |
|------|--------|----------|
| claude | `claude -p <t>` | `claude -c -p <t>` |
| agy | `agy -p <t> --add-dir <wd>` | `agy -c -p <t> --add-dir <wd>` |
| codex | `codex exec --sandbox workspace-write <t>` | `codex exec --sandbox workspace-write resume --last <t>` |

Why a chained driver rather than tmux send-keys: scriptable, hermetic, repeatable
without a PTY — the same rationale that made non-interactive the harness default
in round 1. tmux send-keys remains the documented fallback for genuinely
interactive-only flows; multi-turn does not require it because all three tools
expose a resume/continue print mode.

**codex gotcha (debugged here)**: `--sandbox` is an `exec` global flag and must
precede the `resume` subcommand. `codex exec resume --last --sandbox …` is
rejected ("unexpected argument '--sandbox'"), which aborted the `&&` chain at
turn 2 on the first attempt. Correct form: `codex exec --sandbox workspace-write
resume --last <prompt>`.

## Environment & Reproduction

```bash
python3 multiturn.py                       # all 3 tools
python3 multiturn.py --tools claude        # single tool
```

Requires claude / agy / codex on PATH and authenticated; ai-observe from the
checkout (`bin/ai-observe`). Raw artifacts land in `data/output/` (git-ignored);
the curated `multiturn_report.json` is committed.

## Code

- [`multiturn.py`](multiturn.py) — builds the per-tool chained driver, runs it
  under one ai-observe wrapper, and compares canonical events / actual files /
  viewer count. `writes_onto(name)` counts direct writes landing on a final file
  (atomic-write tools rewrite via tmp+rename, so an "append" is a second rename).

## Results

### Summary

Multi-turn capture is **accurate for all three tools** under a single ai-observe
wrapper. Later-turn file operations are captured, not just turn 1, and continuity
works (the turn-3 append lands on turn1.txt). codex's marker-noise (issue #33)
scales with turn count but does not obscure the real file ops.

### Metrics

| tool | events | by_operation | viewer | turn2 create captured | turn3 op on turn1 captured | turn1 appended (continuity) |
|------|--------|--------------|--------|-----------------------|----------------------------|-----------------------------|
| claude | 8 | create 5, rename 3 | 8 | ✅ | ✅ (2 writes onto turn1) | ✅ |
| agy | 10 | modify 8, create 2 | 10 | ✅ | ✅ (5 writes onto turn1) | ✅ |
| codex | 94 | delete 84, modify 8, create 2 | 94 | ✅ | ✅ | ✅ |

### Key Findings

1. **Later-turn ops are never lost.** Each turn's file operations appear in the
   canonical `.jsonl` regardless of turn index; the single-wrapper/grandchild
   model captures the whole chain.
2. **Write shapes differ by tool, all faithful.** claude rewrites atomically
   (create `*.tmp` → rename onto target), so a turn-3 *append* shows as a *second*
   create+rename onto turn1.txt, not a "modify". agy modifies in place (8 modify
   events). Both are accurate representations of what happened.
3. **codex marker-noise (#33) scales with turns.** codex probes its workspace
   (`.git`/`.agents`/`.codex`) once per turn, so a 3-turn run shows 84 unpaired
   `delete`s — 3× the single-turn count. This is the known #33 signature; the real
   file ops (2 creates + 8 modifies for turn1/turn2) are intact underneath.

## What Worked

- The chained-driver design is a minimal, in-experiment harness extension (no
  changes to `harness.py` or ai-observe) that faithfully reproduces the multi-turn
  user path.
- All three tools genuinely maintained conversation context across turns.

## What Didn't Work

- First codex attempt aborted at turn 2 due to the `--sandbox`/`resume` flag
  ordering (now fixed and documented above).

## Next Steps

- Multi-turn is **covered**; no follow-up experiment needed.
- No new ai-observe bug. Only the pre-existing #33 signature appears (attributed,
  not re-reported).

## References

- Issue #35 (gap 1); round 1: #31, `experiments/FINDINGS.md`.
- Harness: `experiments/1_driving_mechanism/harness.py`.
- Cross-experiment summary: `experiments/FINDINGS-round2.md`.
