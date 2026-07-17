# Findings — ai-observe live-agent testing, round 2 (issue #35)

**Status**: Complete · **Date**: 2026-07-17 · **Protocol**: EXPERIMENT (soft mode)

Round 2 closes the six coverage gaps left by #31 (round 1, PR #34), **reusing the
existing harness** (`experiments/1_driving_mechanism/harness.py`) — new scenarios,
not new infrastructure. One experiment directory per gap (`experiments/4_…9_`).
This file is the authoritative round-2 coverage matrix + findings; round 1's
matrix and F1–F4 remain in [`FINDINGS.md`](FINDINGS.md).

- Exp 4 — [`4_multi_turn/notes.md`](4_multi_turn/notes.md): multi-turn under one wrapper.
- Exp 5 — [`5_viewer_timing/notes.md`](5_viewer_timing/notes.md): viewer attach timing.
- Exp 6 — [`6_degraded_recovery/notes.md`](6_degraded_recovery/notes.md): forced `.partial`/`.rebuilt`.
- Exp 7 — [`7_cross_tool_interrupt/notes.md`](7_cross_tool_interrupt/notes.md): cross-tool interrupt.
- Exp 8 — [`8_agent_crash/notes.md`](8_agent_crash/notes.md): SIGKILL / nonzero mid-write.
- Exp 9 — [`9_long_running/notes.md`](9_long_running/notes.md): incremental streaming.

## Environment

Linux (WSL2), `strace` present, `ptrace_scope=1` — same as round 1. All three
tools present **and** authenticated: claude 2.1.204, codex-cli 0.144.5, agy 1.1.3.
ai-observe run from the checkout via `bin/ai-observe`. No changes to ai-observe
source, `pyproject.toml`, or CI; harness deps stay stdlib-only inside experiment
folders; raw `.trace`/`.jsonl`/`.meta.json` git-ignored, only curated relative-path
`*_report.json` committed.

## Gap coverage (all six: COVERED)

| # | Gap (issue #35) | Exp | Status | One-line result |
|---|-----------------|-----|--------|-----------------|
| 1 | Multi-turn within one session | 4 | ✅ COVERED | Later-turn file ops captured for all 3 tools under one wrapper; continuity works. |
| 2 | Viewer mid-session vs. before start | 5 | ✅ COVERED | Mid/late attach loses nothing (complete backlog + live stream); viewer refuses to start pre-artifact (F5). |
| 3 | Forced degraded recovery | 6 | ✅ COVERED | `.rebuilt` (live-timeout) complete + authoritative; `.partial` (parse-fail) flagged — but sidecar incoherence F6. |
| 4 | Cross-tool interrupt | 7 | ✅ COVERED | F4 generalizes: clean finalize, no degraded artifact, no phantom, for agy/codex/claude. |
| 5 | Agent crash | 8 | ✅ COVERED | Observer survives agent SIGKILL → clean; observer SIGKILL → accurate orphan, no meta (F7). |
| 6 | Long-running incremental streaming | 9 | ✅ COVERED | Direct events stream live (10 increasing ticks mid-run); snapshot/net arrives at finalization. |

## Coverage matrix (scenario × tool)

Legend: ✅ accurate · ⚠️ accurate-but-noisy (known #33) · ❌ wrong/missing.
Every "actual files" oracle passed on the agent side in every cell — divergences
are in ai-observe's *reporting* and are attributed to known issues where noted.

| Scenario | claude | agy | codex |
|----------|--------|-----|-------|
| **multi-turn** 3-turn chain (Exp 4) | ✅ 8 ev, both files, turn-3 append captured | ✅ 10 ev, both files, turn-3 captured | ⚠️ 94 ev — real ops (2 create + 8 modify) intact; 84 marker-noise deletes (#33, ×turns) |
| **viewer mid/late attach** (Exp 5) | ✅ complete backlog (24/24) at every attach ≥ file-exists | — | — |
| **degraded: live-timeout→rebuilt** (Exp 6) | ✅ complete `.jsonl.rebuilt`, authoritative | — | — |
| **degraded: parse-fail→partial** (Exp 6) | ⚠️ `.partial` (2 direct) written; `.jsonl` is snapshot-only yet labeled authoritative_complete (**F6**) | — | — |
| **mid-session interrupt** (Exp 7) | ✅ clean, captured==actual, no phantom | ✅ clean, captured==actual | ✅ clean, captured==actual (sandbox transparent) |
| **agent SIGKILL** (Exp 8) | ✅ observer finalizes clean, authoritative | — | — |
| **observer SIGKILL** (Exp 8) | ⚠️ accurate orphan `.jsonl`+`.trace`, **no `.meta`/snapshot** (**F7**) | — | — |
| **nonzero mid-write** (Exp 8) | ✅ clean finalize (tool masks inner rc) | — | — |
| **long-running stream** (Exp 9) | ✅ incremental live; net layer at finalize | — | — |

## New findings (round 2)

### F5 — viewer requires its target `.jsonl` to exist at launch *(informational / by-design)*
`python -m ai_observe.viewer <missing.jsonl>` prints `path does not exist` and
exits (0). You cannot pre-launch a viewer that waits for a session to appear — it
is an attach-to-existing-artifact tool. Once the artifact exists, a viewer
attaching at **any** later time gets the complete set (backlog from byte 0 + live
SSE). *Impact*: live-observe workflow must start the session first, then launch
the viewer. *Direction*: a docs note (or, if desired, a `--wait-for-file` mode).

### F6 — sidecar labels a snapshot-only `.jsonl` "authoritative_complete" after a direct parse failure *(new bug candidate; HELD for architect go-ahead)*
Under `parser_status = "parser_failure_partial"`, the `.meta.json` **also** reports
`jsonl.role = "authoritative_complete"` and `authoritative_event_path =
<session>.jsonl`. Root cause: the strace `live_pf` branch nulls authority and
empties `.jsonl`; then `merge_snapshot_events` writes the **net (inferred)** events
into the empty `.jsonl` and **returns `authoritative_path = jsonl_path`**;
`build_session_meta` then derives `authoritative_complete` from that path alone,
ignoring the failed `parser_status`. The "authoritative" `.jsonl` is therefore
**snapshot-only** — all direct-layer detail beyond the pre-failure events is lost.
For create-only tasks the net view looks complete; for **ephemeral/transient** ops
it would silently miss them while still labeled `authoritative_complete`, so a
consumer keying off `authoritative_event_path` treats a degraded session as
healthy. Defensible as a net fallback, but the **role label overstates fidelity**.
Minimal repro: `6_degraded_recovery/degraded.py` (`parse_failure_partial`).
*Direction*: don't label `.jsonl` `authoritative_complete` when `parser_status`
indicates a direct-parser failure and the surviving `.jsonl` is snapshot-only
(e.g. a `net_only` / `authoritative_net` role, or keep authority = None).

### F7 — observer SIGKILL leaves an accurate but unlabeled orphan *(informational)*
SIGKILL of the ai-observe coordinator (whole process group) skips finalize: **no
`.meta.json`, no snapshot layer**. What survives is the live-tailed `.jsonl` (direct
events up to the kill, matching disk, no phantom) + the raw `.trace`. The viewer
tolerates the meta-less `.jsonl` (`parser_status=None`) rather than crashing. The
`.trace` is the manual-recovery input. Confirms round-1 F4's caveat that a
`.meta.json`-per-launch invariant cannot be assumed. Not a bug (inherent to
SIGKILL); *direction*: docs note on orphaned-session recovery.

## Positives confirmed

- **Multi-turn capture** is accurate for all three tools under a single wrapper
  (gap 1) — the primary #31 use case now validated.
- **Viewer completeness + incremental streaming** both hold (gaps 2, 6): late
  attach loses nothing; direct events stream live during long runs.
- **Interrupt/recovery (F4) generalizes** to agy and codex, including codex's
  mount-namespace sandbox (gap 4); **agent SIGKILL** is handled like a clean exit
  (gap 5).
- **`.jsonl.rebuilt` live-timeout recovery** produces a complete, correctly
  authoritative artifact (gap 3).

## Interference from known bugs (attributed, not re-reported)

- **#33** (codex `/newroot` marker noise): dominates codex event volume in Exp 4
  (84 deletes / 3 turns, scaling with turn count) and appears in Exp 7 (filtered
  out of the `.txt` oracle). Attributed to the open issue.
- **#32** (annotated `AT_FDCWD` deletion drop): not exercised here (round-2
  scenarios are create/modify/interrupt-centric, not deletion-centric); no new
  evidence, no masking observed.

## Recommendation

1. **File F6** as an ai-observe bug (pending architect go-ahead) — it is the only
   round-2 finding with a correctness angle (sidecar authority can overstate
   fidelity on the degraded parse-failure path). Fix direction pinned above.
2. **Document F5 and F7** (viewer pre-existence requirement; orphaned-session
   recovery via `.trace`) — informational, not bugs.
3. **No harness graduation change** beyond round 1's recommendation — the round-2
   experiments are further evidence that graduating `harness.py` into a maintained
   test-support module (round-1 rec) is worthwhile; the multi-turn chained-driver
   and the timeline-sampling probe are reusable additions worth folding in.

## Reproduce

```bash
cd experiments/4_multi_turn        && python3 multiturn.py
cd experiments/5_viewer_timing     && python3 viewer_timing.py
cd experiments/6_degraded_recovery && python3 degraded.py
cd experiments/7_cross_tool_interrupt && python3 interrupt_xtool.py
cd experiments/8_agent_crash       && python3 crash.py
cd experiments/9_long_running      && python3 incremental.py
```

Raw ai-observe artifacts are git-ignored (large + sensitive per the repo data
warning); committed `*_report.json` hold curated, relative-path-only evidence.
