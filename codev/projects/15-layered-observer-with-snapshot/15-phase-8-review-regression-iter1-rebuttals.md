# Phase 8 rebuttal — iteration 1

## Reviews

- Gemini: `APPROVE`
- Codex: `REQUEST_CHANGES`
- Claude: `COMMENT`

## Changes made after review

1. **Fixed latent Phase 8 test flakiness**
   - Updated these older observe-cli tests to pin `AI_OBSERVE_BACKENDS=strace` because they are shim/strace compatibility tests, not layered snapshot tests:
     - `test_generic_wrapper_writes_schema_compatible_jsonl_and_preserves_exit`
     - `test_claude_named_shim_runs_with_fake_strace_and_records_command`
     - `test_codex_shim_runs_with_preferred_ai_real_codex`
   - Re-ran the affected tests and the full suite successfully.

2. **Corrected the review artifact wording**
   - Updated `codev/reviews/15-layered-observer-with-snapshot.md` so it no longer implies that the branch is already PR-finalized.
   - The artifact now explicitly says it is a **pre-PR review artifact** written while the strict-mode porch review/rebuttal cycle is still active.
   - The validation section now states that the 204-test result was obtained in the active worktree at review time.
   - The git-status section now explicitly says the branch is not yet claiming PR-ready finalization in the artifact.

## Codex feedback

### 1. `status.yaml` still shows `phase-8-review-regression` in progress with `pr` / `verify-approval` pending

**Response:** No code change required; artifact wording corrected.

This is the expected strict-mode porch state during the consultation/rebuttal loop. Builders must not edit `status.yaml` directly, and the project is not supposed to show `pr` / `verify-approval` as complete before porch closes the current review cycle.

To avoid overstating completion, I updated the review artifact so it no longer frames the branch as already finalized/PR-ready.

### 2. The repo/worktree is still dirty and later phases are not yet reflected as finalized PR-ready commits

**Response:** Partially addressed by making the review artifact accurate about current state.

The review artifact previously read too much like a final PR-ready declaration. That wording has been corrected. In the current strict-mode porch step, the worktree is still live because:

- the final implementation review cycle is still open;
- porch has not yet closed this iteration; and
- the consultation/rebuttal artifacts for later phases are part of the live worktree state during that process.

I did **not** try to force porch-owned state transitions or claim that PR creation has already happened. The artifact now states that final PR creation remains pending porch/architect workflow.

### 3. The review artifact overstated completion by presenting “204 tests passed” as if the branch were already finalized

**Response:** Addressed.

The artifact now states that the 204-test result was obtained in the active worktree at review time, not that the branch had already completed the full porch PR/verification flow.

Additionally, after review feedback I fixed the latent observe-cli flakiness noted during the Claude comment review and reran:

```bash
python3 -m unittest \
  tests.test_observe_cli.ObserveCliIntegrationTests.test_generic_wrapper_writes_schema_compatible_jsonl_and_preserves_exit \
  tests.test_observe_cli.ObserveCliIntegrationTests.test_claude_named_shim_runs_with_fake_strace_and_records_command \
  tests.test_observe_cli.ObserveCliIntegrationTests.test_codex_shim_runs_with_preferred_ai_real_codex

python3 -m unittest discover -s tests
```

Results after the fix:

- affected observe-cli tests: **3 passed**
- full suite: **204 passed**

## Summary

- **Implemented fix:** pinned three older shim/compat tests to `AI_OBSERVE_BACKENDS=strace` to remove latent snapshot-related flakiness.
- **Artifact correction:** rewrote the review artifact language to accurately reflect the still-open strict-mode porch state instead of implying PR-finalized status.
- **Disagreement:** the in-progress porch state itself is not a defect; it is the expected state during the final review/rebuttal loop and is not builder-editable.
