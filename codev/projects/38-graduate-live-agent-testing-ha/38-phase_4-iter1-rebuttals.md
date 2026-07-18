# Phase 4 — Rebuttal to impl iter 1 review

Verdicts: **Gemini APPROVE**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH
confidence). Both Codex points accepted and fixed.

## Codex (REQUEST_CHANGES)

1. **S6 completeness weakened to `final_visible >= len(canonical)` (`probes.py`).**
   *Accepted — fixed.* Changed to **equality**: `final_complete = final_visible ==
   len(canonical) and len(canonical) > 0`. An over-serving viewer no longer passes.
   Verified live: `final viewer_visible=36 canonical=36` passes.

2. **Timeline path bypassed the `ToolUnusable` M4 gate (`probes.py` /
   `check_timeline.py`).** *Accepted — fixed.* `sample_timeline` now returns the
   session `returncode`, and `check_timeline` runs the **M4 gate** on it via
   `ensure_tool_usable(tool, SimpleNamespace(returncode=…, disk_events={"total":
   canonical_total}))` before asserting. An unauthenticated / failed / event-less claude
   run now surfaces as the loud, named `ToolUnusable` (Decision 4), not a generic viewer
   failure.

## Gemini / Claude (APPROVE)
No changes requested. Both confirmed the Exp-4 multi-turn chained driver (with the codex
`--sandbox`-before-`resume` argv pin), the Exp-9 timeline probe, the deterministic #33
flip-home, and the tool-free self-tests.

## Net changes
`probes.py`: completeness is now equality; report carries `returncode`.
`check_timeline.py`: applies the M4 `ensure_tool_usable` gate. Live re-verified;
`--selftest` 44/44. No reviewer point declined.
