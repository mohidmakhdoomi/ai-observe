# spir-36 thread — meta sidecar labels snapshot-only .jsonl authoritative_complete (#36)

## Session start — Specify phase

Strict mode, porch-driven. No spec existed; issue #36 is detailed (root-caused via
experiments/6_degraded_recovery, architect-verified), so drafting the spec directly
from the issue + source study rather than blocking on clarifying questions.

Source study findings that shape the spec:

- `prepare_logs` pre-creates an empty `.jsonl` (`exclusive_touch`), so EVERY
  direct-parser-failure path (live and post-hoc) leaves an existing empty `.jsonl`,
  and `merge_snapshot_events`'s empty-file fallback (observe.py:530-532) promotes it
  to authoritative whenever snapshot events exist. The bug reproduces on six failure
  statuses, not just `parser_failure_partial`.
- Snapshot-only mode (`AI_OBSERVE_BACKENDS=snapshot`, status `backend_disabled`)
  ALSO reaches the promotion branch — by design. The fix predicate must not
  downgrade that path (scope decision surfaced in the spec).
- The Spec 38 harness already ships a rot-proof #36 gate: `OPEN_BUGS[36]` +
  `expect_authority_not_overstated` in `tests/agent_sessions/oracle.py`; the
  selftest's hypothetical FIXED_SHAPE uses role `authoritative_net`. Fixing the bug
  requires flipping `OPEN_BUGS[36].active = False` and updating two selftest_oracle
  expectations in the same change. `selftest_degraded.py` is registry-explicit and
  needs no changes.
- No existing test asserts `authoritative_complete` on a failure path; the viewer
  passes role strings through without name-matching. Blast radius is small.

Recommending Option A from the issue: new role `authoritative_net`, keep
`authoritative_event_path` pointing at the promoted `.jsonl`.

## Specify → gate

Spec drafted, 3-way reviewed (gemini APPROVE / claude APPROVE / codex COMMENT),
codex's two minor items folded in (FR3 warning substring pinned +
integration-test assertion; explicit coverage-split preamble in Test scenarios).
Commits: 41854ef (draft), de24831 (with multi-agent review).
Now WAITING at spec-approval gate; architect notified via afx send.
