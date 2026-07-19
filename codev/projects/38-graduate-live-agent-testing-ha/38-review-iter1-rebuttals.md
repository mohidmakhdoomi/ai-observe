# Review (PR) — Rebuttal to PR-level iter 1 review

Verdicts: **Gemini APPROVE**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH).
Both Gemini and Claude independently re-ran the suites (Claude: 52/52 self-tests, 236 CI
tests, 0 skips; confirmed `port=0`, no `sys.path.insert`, no debug code) and approved with
zero key issues. Codex raised three points on branch hygiene — one accepted and fixed, one
rebutted, one N/A.

## Codex (REQUEST_CHANGES)

1. **Working tree is dirty — untracked `38-phase_4-iter3-context.md`.**
   *Accepted — fixed.* This was a porch-generated **transient consult-context file** left
   over from the phase-4 iter-3 review (no context file is tracked anywhere on the branch —
   they are inputs, not deliverables). Removed it; the working tree is now clean apart from
   this round's review consult outputs + this rebuttal, which are staged and committed as the
   record (consistent with every prior phase's `*-iter*-{gemini,codex,claude}.txt`).

2. **Commit history not PR-ready: 46 commits ahead of `main`, many `chore(porch): …`
   instead of `[Spec 38][Phase] …`.**
   *Rebutted (with a real merge answer).* The `chore(porch): …` commits are **porch's own
   orchestration commits**, authored by the porch state machine as it drives the mandated
   **strict-mode** SPIR protocol (build-complete, re-iter, force-advance, advance-plan-phase).
   The builder does not author them, and strict mode explicitly forbids the builder from
   editing porch's state/commits — rewriting 46 commits of orchestration history would risk
   corrupting the state machine and is out of scope. Builder-**authored** commits do follow the
   policy: the review commit is `[Spec 38][Phase: review] docs: …`. The branch is intended to be
   **squash-merged** by the architect (the PR title becomes the single canonical commit), which
   is exactly how the porch-orchestrated commit trail is meant to collapse on integration — so
   the merged history on `main` is one clean `[Spec 38]` commit, not 46 `chore(porch)` ones. If
   the architect prefers a rebase-merge with a curated history, that is an integration-time
   decision for them, not a builder branch-rewrite.

3. **Minor: codex could not independently validate the test results (no writable temp dir;
   viewer startup blocked in its sandbox).**
   *N/A — environment limitation on the reviewer side, not a code defect.* The suite is
   verified green in the builder worktree: `--selftest` 52/52, `unittest discover -s tests`
   236 tests / 0 skips, and the capstone live sweep (claude all-7 + agy all-5 green, the three
   known-bug gates annotating, codex loud-failing on M4 because it is unauthenticated here) —
   all captured in the review doc's Live Evidence section. Gemini and Claude both re-ran and
   confirmed. The graduated harness itself defends against exactly this class of environment
   fragility: `ViewerMonitor.start()` normalizes a viewer bind/serve failure into `False`
   (Phase-1 codex fix) rather than raising, and the default artifact dir is an auto-cleaning
   temp dir — but a reviewer sandbox with no writable temp at all cannot run the live tier.

## Net changes
Removed the stray transient context file (clean tree). No source or test change — the two
APPROVE reviewers found zero issues, and codex's only actionable point was tree hygiene. The
`chore(porch)` commit trail is inherent to the strict-mode protocol and resolves at
squash-merge. No reviewer point that identifies a code defect was declined (none was raised).
