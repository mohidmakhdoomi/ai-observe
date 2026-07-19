# Phase 4 — Rebuttal to impl iter 3 review

Verdicts: **Gemini APPROVE**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH
confidence). Codex's single point accepted and fixed. Gemini and Claude both confirmed
the iter-2 timeline stderr-persistence fix landed correctly.

## Codex (REQUEST_CHANGES)

1. **agy multi-turn test uses `assertIn` (substring), not an exact chained-shell string
   pin — below the phase's "exact chained-shell string per tool" acceptance bar
   (`selftest_drivers.py:49-52`).**
   *Accepted — fixed.* Codex is right: `test_agy_chain_continue_and_add_dir` pinned only
   two substrings (`t1`, `t2`) via `assertIn`, leaving the full 3-turn agy chain's
   ordering, the `t3` turn, and the join structure unguarded — asymmetric with the
   claude/codex tests, which already `assertEqual` the full string. The plan's Deliverable
   and Acceptance Criteria both say "assert the **exact** chained-shell string per tool
   (tool-free)."

   **Change (`selftest/selftest_drivers.py`):** replaced the two `assertIn` checks with a
   single `assertEqual` on the complete expected string:

       agy -p 't1' --dangerously-skip-permissions --add-dir '/tmp/wd' && \
       agy -c -p 't2' --dangerously-skip-permissions --add-dir '/tmp/wd' && \
       agy -c -p 't3' --dangerously-skip-permissions --add-dir '/tmp/wd'

   This now locks (a) turn ordering, (b) the `-c` continue flag on turns 2+ only, (c)
   `--add-dir <wd>` on **every** turn, and (d) the `&&` join. Added a negative
   `assertNotIn("agy -c -p 't1'", chain)` to guard that turn 1 never carries `-c` (no
   prior session to continue) — parity with the codex test's `--sandbox`-after-`resume`
   negative guard. All three tools now pin the exact chained string. `--selftest` 44/44.

## Gemini / Claude (APPROVE)
No changes requested. Gemini explicitly confirmed the iter-2 fix ("persist wrapper stderr
and inline it in the ToolUnusable failure for debugging"). Claude confirmed all iter-1/
iter-2 fixes applied, the #33 flip-home wired, and 44/44 deterministic self-tests.

## Net changes
`selftest/selftest_drivers.py`: agy multi-turn chain now pinned by exact-string
`assertEqual` (+ a turn-1 `-c` negative guard), matching claude/codex. No source-behavior
change; test-tightening only. `--selftest` 44/44. No reviewer point declined.
