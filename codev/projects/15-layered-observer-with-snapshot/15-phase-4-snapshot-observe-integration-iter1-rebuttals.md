# Phase 4 Iteration 1 Rebuttal

## Summary

Gemini approved and Claude approved. Codex requested one substantive contract fix around the `no_roots` case. I agreed with that feedback, updated the implementation to fail when all configured roots are unusable, corrected the corresponding integration test, and added an extra timeout/rebuilt merge-target regression test while revalidating the phase.

## Codex: all configured roots invalid must fail, not continue

**Feedback:** When `AI_OBSERVE_ROOTS` resolves to no usable roots, the wrapper only warned and continued. The spec says missing roots are skipped **unless no root remains**, so this case should not proceed as a successful observed session.

**Resolution:** Updated `src/ai_observe/observe.py` so that after snapshot root resolution, if no usable roots remain:
- the wrapper writes a `<session>.meta.json` sidecar with `parser.status = "snapshot_root_error"` and the recorded snapshot diagnostics;
- then exits with `ObserveError("no usable snapshot roots remain after resolving AI_OBSERVE_ROOTS")` before launching the child command.

This preserves the diagnostics/meta trail while matching the required failure behavior.

## Codex: missing-roots integration test encoded the wrong contract

**Feedback:** The Phase 4 integration test expected exit code `0` when all configured roots were missing, which contradicted the spec.

**Resolution:** Renamed and updated the integration test in `tests/test_observe_cli.py` to `test_snapshot_missing_roots_fail_when_no_roots_remain`, and now assert:
- return code `1`;
- stderr includes the snapshot warning plus the no-usable-roots failure;
- the meta sidecar exists and records `snapshot_root_error` plus the `missing_root` / `no_roots` diagnostics.

## Additional follow-up coverage added during the fix

While addressing the review, I added an explicit live-timeout/rebuilt merge-target regression test in `tests/test_live_trace.py`:
- `test_join_timeout_rebuilt_merges_snapshot_events_into_rebuilt_artifact`

This proves snapshot/inferred events merge into `.jsonl.rebuilt` when the rebuilt artifact is authoritative after a live-parser join timeout.

## Verification

- `python3 -m unittest tests.test_live_trace tests.test_observe_cli tests.test_snapshot tests.test_observe_env` — passed (54 tests)
- `python3 -m unittest discover -s tests` — passed (188 tests)
