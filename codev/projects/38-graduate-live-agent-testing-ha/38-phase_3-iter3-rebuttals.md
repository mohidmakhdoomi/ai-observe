# Phase 3 — Rebuttal to impl iter 3 review

Verdicts: **Gemini APPROVE**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH
confidence). Both Codex false-positive points accepted and fixed.

## Codex (REQUEST_CHANGES)

1. **S2 (`check_ephemeral.py`) could pass without the create-then-delete actually
   happening.** *Accepted — fixed.* Final-absence + the deterministic #32 parser probe did
   not prove the live run created the file. Added a **hard canonical assertion** that the
   create was captured live: `writes_onto(events, "ephemeral.txt") >= 1`. Now a run that
   never created the file (or touched a different one) fails. The #32 gate remains scoped
   to deletion capture only. Verified live: `writes_onto(ephemeral.txt)=2 (create captured
   live)` passes alongside absence + `known-bug:#32`.

2. **S3 (`check_modify.py`) accepted an overwrite as an append.** *Accepted — fixed.*
   `"appended" in content` would pass if the agent overwrote `notes.txt` with just
   `appended`. Now requires the **seed to survive**: `"line one" in content and "appended"
   in content` — an overwrite (seed gone) fails. Verified live: `seed_survived=True
   appended=True` passes.

## Gemini / Claude (APPROVE)
No changes requested. Both confirmed S1–S4, the three-view oracle, the deterministic
#32/#33 parser probes, applicability, and 40/40 tool-free self-tests with no CI impact.

## Net changes
`check_ephemeral.py`: +hard "create captured live" canonical check. `check_modify.py`:
append assertion now requires seed survival. Live re-verified (ephemeral, modify);
`--selftest` 40/40. No reviewer point declined.
