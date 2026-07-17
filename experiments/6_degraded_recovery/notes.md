# Experiment 6: forced degraded recovery (.partial / .jsonl.rebuilt)

**Status**: Complete

**Date**: 2026-07-17

**Gap (issue #35, priority 3)**: Exp 3 proved a *clean* SIGINT never produces
`.partial`/`.rebuilt`. Those degraded artifacts belong to the parse-failure /
live-timeout paths, which round 1 left untested. Force them deliberately and
verify artifact authority behaves per the arch.md Artifact contract.

## Goal

- **Question**: when the live parser times out or fails mid-stream, which
  artifacts does ai-observe produce, and does the sidecar (`.meta.json`) + viewer
  correctly report the authoritative artifact and parser status?
- **Success criteria**: the live-timeout path yields a complete `.jsonl.rebuilt`
  marked authoritative; the parse-failure path yields a `.jsonl.partial` and a
  parser_status flagging the failure; the viewer agrees with the sidecar.

## Effort

~2.5 hours (including root-causing the F6 sidecar-coherence finding).

## Approach

All forcing uses **supported ai-observe env knobs — no source changes**:

| case | env | forces |
|------|-----|--------|
| control_clean | (none) | `.jsonl` authoritative, `parser_status="ok"` |
| live_timeout_rebuilt | `LIVE_JOIN_TIMEOUT=0.1` + `LIVE_POLL_MS=2000` | live tailer isn't drained within 0.1s of stop → rebuild full trace → `.jsonl.rebuilt` |
| parse_failure_partial | `TEST_FAIL_AFTER=2` | parser raises `ParserFailure` after 2 events → `.jsonl.partial` |
| parse_failure_strict | `TEST_FAIL_AFTER=2` + `STRICT_PARSE=1` | as above, and ai-observe returns exit 1 |

`TEST_FAIL_AFTER` is the in-tree deterministic-failure hook; it reaches the same
terminal state (`parser_failure_partial`) that a genuinely **corrupt `.trace`**
(the issue's other suggested trigger) reaches via a mid-stream unparseable line.
The paced task (claude writing 5 files) gives the live tailer a real backlog and
lands the injected failure mid-stream.

## Environment & Reproduction

```bash
python3 degraded.py
```

Requires claude + ai-observe from the checkout. Raw artifacts in `data/output/`
(git-ignored); curated `degraded_report.json` committed.

## Code

- [`degraded.py`](degraded.py) — runs the four cases, then for each records
  artifact existence/size, `meta.json` parser_status + artifact roles, the
  viewer's reported authoritative artifact + status, and captured-vs-actual files.

## Results

### Summary

The **live-timeout → `.jsonl.rebuilt`** path works correctly: a complete
canonical is rebuilt from the full trace, marked `authoritative_complete`, and the
viewer/sidecar agree. The **parse-failure → `.partial`** path writes the partial
direct events and flags `parser_failure_partial`; `STRICT_PARSE` makes ai-observe
exit 1 even though the agent exited 0. **New finding F6**: under
`parser_failure_partial`, the sidecar is internally **incoherent** — it flags the
failure yet simultaneously labels `.jsonl` `authoritative_complete`.

### Metrics

| case | agent rc | parser_status | authoritative | .jsonl | .partial | .rebuilt | captured==actual |
|------|----------|---------------|---------------|--------|----------|----------|------------------|
| control_clean | 0 | ok | `.jsonl` | 5 direct+net | – | – | ✅ 5/5 |
| live_timeout_rebuilt | 0 | live_timeout_rebuilt | `.jsonl.rebuilt` | partial_live | – | complete | ✅ 5/5 |
| parse_failure_partial | 0 | parser_failure_partial | **`.jsonl` (see F6)** | 5 (snapshot-only) | 2 direct | – | net ✅ |
| parse_failure_strict | **1** | parser_failure_partial | `.jsonl` | 5 (snapshot-only) | 2 direct | – | net ✅ |

### Key Findings

1. **`.jsonl.rebuilt` (live-timeout) is robust and complete.** Forcing a join
   timeout triggers a full-trace post-hoc rebuild; the rebuilt artifact captured
   all 5 files and is correctly marked authoritative. `parser_status =
   "live_timeout_rebuilt"`, `warnings_count = 1`, viewer agrees. **Positive.**

2. **F6 (new, not #32/#33; HELD for architect go-ahead before filing) — sidecar
   coherence under `parser_failure_partial`.** The `.meta.json` reports
   `parser.status = "parser_failure_partial"` **and** `jsonl.role =
   "authoritative_complete"` with `authoritative_event_path = <session>.jsonl`,
   at the same time. Root cause (traced through the source):
   - the strace `live_pf` branch sets `authoritative_path = None`, truncates
     `.jsonl` to empty, and writes the 2 partial *direct* events to `.partial`;
   - the **snapshot backend** then runs `merge_snapshot_events` with
     `authoritative_path = None`, hits the `jsonl exists && size == 0` branch,
     writes its 5 **net (inferred)** creates into `.jsonl`, and **returns
     `authoritative_path = jsonl_path`** — flipping authority back on;
   - `build_session_meta` derives `jsonl_role = "authoritative_complete"` purely
     from `authoritative_path == jsonl_path`, ignoring the failed `parser_status`.

   **Consequence**: the "authoritative" `.jsonl` is **snapshot-only** (every event
   `source = snapshot`); all direct-layer detail beyond the first 2 events is gone.
   For this create-only task the net view happens to list all 5 files, so
   "complete" *looks* right — but for a task with **ephemeral/transient** ops
   (create-then-delete, in-place modifies) the net view would silently miss them
   while the sidecar **still** says `authoritative_complete`. A consumer keying off
   `authoritative_event_path` alone treats a degraded session as healthy.
   Defensible as a net-snapshot fallback, but the role label **overstates
   fidelity** when the direct parser has failed. Minimal repro: the
   `parse_failure_partial` case here.

3. **`STRICT_PARSE` works as an opt-in fail-loud.** Same partial state, but
   ai-observe returns exit 1 while the agent itself exited 0 — useful for CI-style
   gating on capture integrity.

## What Worked

- Deterministic forcing of both degraded paths via documented env knobs.
- Root-causing F6 end-to-end through `strace.py` finalize → `merge_snapshot_events`
  → `build_session_meta`.

## What Didn't Work

- N/A — all four cases ran as designed. The F6 incoherence is a *finding*, not a
  harness failure.

## Next Steps

- Gap **covered**. **F6 held for architect** — recommend filing as an ai-observe
  issue (sidecar role should reflect that a `.jsonl` reconstructed from snapshot
  only, after a direct parse failure, is *net-only* / not full-fidelity
  `authoritative_complete`). Pinpoint fix direction: don't label `.jsonl`
  `authoritative_complete` when `parser_status` indicates a direct-parser failure
  and the surviving `.jsonl` is snapshot-only.

## References

- Issue #35 (gap 3); arch.md Artifact contract; `src/ai_observe/backends/strace.py`
  (`finalize`, `_write_partial`), `src/ai_observe/observe.py`
  (`merge_snapshot_events`, `build_session_meta`).
- Cross-experiment summary: `experiments/FINDINGS-round2.md`.
