# experiment-35 thread — ai-observe live-agent testing, round 2

Driving issue: #35 (EXPERIMENT protocol, soft mode). Gate: `experiment-complete`.
Builds on #31 / PR #34 (`experiments/1-3`, `harness.py`, `FINDINGS.md`).

## Mandate (issue #35)
Close the 6 coverage gaps left by round 1 — **new scenarios reusing the existing
harness**, no new infra. Continue numbering `experiments/4_...`. Priority order:
1. Multi-turn / repeated prompting within one observed session (never exercised).
2. Viewer opened mid-session vs. before start (async jsonl tailing backlog).
3. Forced degraded recovery (.partial/.rebuilt) — clean SIGINT never triggers them.
4. Cross-tool interrupt (agy, codex; round 1 was claude-only).
5. Agent crash (SIGKILL / nonzero mid-write) vs. graceful signal.
6. Long-running commands — verify incremental streaming to viewer during the run.

## Constraints
- No changes to ai-observe source / pyproject / CI. Harness deps stay in experiment folders.
- Raw .trace/.jsonl/.meta.json stay git-ignored; commit only curated relative-path evidence.
- Known bugs #32 (annotated AT_FDCWD delete drop) & #33 (codex /newroot marker noise) WILL
  appear — attribute to existing issues, don't re-report, don't let them mask new findings.
- Root-cause any NEW bug with a minimal repro; hold for architect go-ahead before filing.

## Environment (verified hypothesis phase)
Linux WSL2, strace present, ptrace_scope=1. All 3 tools present + auth:
claude 2.1.204, agy 1.1.3, codex-cli 0.144.5. Matches round 1.

## Source levers found (for scenario 3, degraded recovery)
- `AI_OBSERVE_LIVE_JOIN_TIMEOUT` (clamp 0.1–600, default 30): tiny → live tailer join
  times out → rebuild from full trace → `.jsonl.rebuilt` authoritative (`live_timeout_rebuilt`).
- `AI_OBSERVE_TEST_FAIL_AFTER=N`: parser raises ParserFailure after N events →
  `parser_failure_partial`, writes `.jsonl.partial`, authoritative=None. Deterministic partial path.
- Trace corruption → post-hoc parse failure → `.jsonl.partial`.
- `AI_OBSERVE_STRICT_PARSE` → return 1 on parse failure.
- parser_status vocabulary: ok | live_timeout | live_timeout_rebuilt | parser_failure_partial |
  live_error | live_error_rebuilt | parser_failure_empty_partial | *_rebuild_failed …

## Plan (experiment dirs 4-9, one per scenario family)
- Exp 4: multi-turn (harness extension: chained --resume/--continue under one wrapper)
- Exp 5: viewer timing (before / mid / canonical)
- Exp 6: forced degraded recovery (.partial/.rebuilt via the levers above)
- Exp 7: cross-tool interrupt (agy, codex)
- Exp 8: agent crash (SIGKILL)
- Exp 9: long-running / incremental streaming

## Log
- hypothesis: context + environment verified, harness + degraded-path source read. Proceeding to smoke test then Exp 4.
- smoke: harness end-to-end PASSED (claude write hello.txt, jsonl authoritative, viewer served).
- Exp 4 (multi-turn) COMPLETE — gap 1 COVERED. Design: single ai-observe wrapper over a
  chained `bash -lc "<t1> && <t2> && <t3>"` driver; per-turn continuity via each tool's
  resume/continue print mode (claude -c, agy -c, codex exec --sandbox ... resume --last).
  Debugged codex: `--sandbox` MUST precede the `resume` subcommand (rejected after it) — my
  first invocation aborted the chain at turn 2. All 3 tools: multi-turn capture ACCURATE —
  later-turn file ops captured, not just turn 1 (claude 8ev, agy 10ev, codex 94ev). Continuity
  works (turn-3 append lands). codex marker-noise (#33) scales with turn count (84 deletes / 3 turns).
  No NEW bug. claude append shows as atomic tmp+rename (2nd rename onto turn1), not a "modify".
- Exp 5 (viewer timing) COMPLETE — gap 2 COVERED. Two sub-questions, different answers:
  * Q-A: viewer CLI REFUSES to start if the target .jsonl doesn't exist yet (prints
    "path does not exist", exits 0). Pre-attaching before the session creates its artifact
    is UNSUPPORTED. Informational/UX (by-design) → F5.
  * Q-B: once the .jsonl exists, a viewer attached mid-session (at 2 / 12 events on disk) or
    post-finalization gets the COMPLETE set (24/24, missing=0). Backlog from byte 0 + live SSE
    stream. Verified viewer live-push in isolation too (8/8, 6/6). Late attach loses nothing.
  * Fixed a harness-settle artifact: settle=4 closed collection before the finalization snapshot
    burst → spurious "missing snapshot events". settle=9 spans it. NOT a viewer defect.
- Exp 6 (degraded recovery) COMPLETE — gap 3 COVERED. Forced both degraded paths via supported
  env levers (no source changes):
  * live_timeout → .jsonl.rebuilt: LIVE_JOIN_TIMEOUT=0.1 + LIVE_POLL_MS=2000. Works CORRECTLY —
    rebuilt canonical from full trace, marked authoritative_complete, COMPLETE (all 5 files),
    viewer + meta agree (parser_status=live_timeout_rebuilt, auth=rebuilt, warnings=1). POSITIVE.
  * parse_failure → .partial: TEST_FAIL_AFTER=2 (in-tree deterministic hook; same terminal state
    as a corrupt trace). STRICT_PARSE=1 variant → ai-observe returns exit 1 while agent exited 0. Works.
  * **NEW FINDING F6 (not #32/#33; HELD for architect go-ahead before filing).** Under
    parser_failure_partial, the sidecar is INCOHERENT: `parser.status="parser_failure_partial"` yet
    `jsonl.role="authoritative_complete"` + `authoritative_event_path=<session>.jsonl`. Root cause:
    strace live_pf branch sets authoritative=None + truncates .jsonl to empty + writes the 2 partial
    direct events to .partial; THEN the snapshot backend's merge_snapshot_events hits the
    `jsonl exists & size==0` branch, writes its 5 NET (inferred) creates to .jsonl and RETURNS
    authoritative_path=jsonl_path — flipping authoritative back on. build_session_meta then labels
    .jsonl authoritative_complete purely from `authoritative_path==jsonl_path`, ignoring the failed
    parser_status. Consequence: `.jsonl` is SNAPSHOT-ONLY (all source=snapshot); the direct layer's
    detail beyond the first 2 events is gone. For a create-only task the net view looks complete, so
    "authoritative_complete" seems fine — but for ephemeral/transient ops the net view would silently
    MISS them while STILL labeled authoritative_complete. A consumer keying off authoritative_event_path
    alone treats a degraded session as healthy. Repro: experiments/6_degraded_recovery/degraded.py
    (parse_failure_partial case). Defensible-as-net-fallback but the role label overstates fidelity.
- Exp 7 (cross-tool interrupt) COMPLETE — gap 4 COVERED. Robust kill-after-first-file design
  (adapts to per-tool startup latency). agy, codex, claude ALL: mid-session SIGINT →
  parser_status='ok', NO degraded artifact, captured==actual (f1,f2), zero phantom captures.
  F4 (interrupt robustness) GENERALIZES across tools; codex's mount-namespace sandbox doesn't
  break the interrupt/finalize path. POSITIVE.
- Exp 8 (agent crash) COMPLETE — gap 5 COVERED.
  * agent_sigkill (SIGKILL agent subtree, observer survives): clean finalize, authoritative .jsonl
    + meta, parser_status='ok', captured==actual, no phantom. Same contract as graceful interrupt. POSITIVE.
  * observer_sigkill (SIGKILL whole group incl. ai-observe): coordinator never finalizes → orphaned
    live-tailed .jsonl (accurate pre-kill DIRECT events, no phantom) + raw .trace, but NO .meta.json
    and NO snapshot layer. Viewer TOLERATES the meta-less .jsonl (parser_status=None, auth=None) rather
    than crashing. Accurate-but-unlabeled; .trace enables manual recovery. Informational F7 — consistent
    with Exp 3 F4's caveat that a .meta.json-per-launch invariant can't be assumed under hard-kill. Not a bug.
  * nonzero_midwrite: clean finalize (claude -p masks the inner shell rc as 0). POSITIVE.
- Exp 9 (long-running incremental streaming) COMPLETE — gap 6 COVERED. Timeline sampling (fresh /events
  backlog read per tick) shows viewer-visible count grows in lockstep with disk writes across 10 increasing
  ticks DURING the run (2→24 by t=32s, midpoint 16) — incremental, NOT end-loaded. The 12 snapshot NET creates
  land at finalization (24→36 = canonical). INCREMENTAL_CONFIRMED + FINAL_COMPLETE. POSITIVE. Confirms the
  two-layer model: direct/strace streams live, snapshot/inferred appears at finalize.
- ALL 6 GAPS COVERED. Writing per-experiment notes.md + cross-experiment FINDINGS-round2.md, then commit + gate.
