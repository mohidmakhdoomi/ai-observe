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

## Plan → gate

Spec approved by architect (verified promotion/role-derivation claims against
source). Plan drafted with 4 phases; key decision: NO plumbing changes — both the
role downgrade and the net-fallback warning derive from one predicate in
build_session_meta (jsonl-authoritative + status outside allow-list is only
reachable via snapshot promotion). 3-way reviewed (gemini APPROVE / claude
APPROVE / codex COMMENT); codex's viewer pass-through pinning test added to
Phase 2. Commits: a4cdd4c (draft), 1bb509e (with multi-agent review).
Now WAITING at plan-approval gate; architect notified.

## Implement — phase_1 build

Plan approved by architect (session restarted fresh at the gate). Note: `porch run`
is removed in this porch version — the loop is `porch next` → work → `porch done`.

Phase 1 (core role derivation) built:
- `build_session_meta` now consults `parser_status` on the `.jsonl`-authoritative
  branch: allow-list `_JSONL_COMPLETE_STATUSES = {ok, live_error_rebuilt,
  backend_disabled}` keeps today's roles; anything else → `authoritative_net` +
  `partial_direct` + `NET_FALLBACK_WARNING` appended via rebind (caller's list
  not mutated). Reachability argument documented in a code comment.
- NEW `tests/test_session_meta.py`: 8-test role matrix (six failure statuses,
  allow-list boundary, unknown-status direction, rebuilt/no-promotion branches
  unchanged, warning-substring pin, mutation guard). Staged on creation.
- Full suite: 263 tests, 0 failures, 0 skips.

## Implement — phase_2 build

Phase 1 committed after unanimous 3-way APPROVE (gemini/codex/claude, no issues).

Phase 2 (integration + pinning tests) built — tests only, no product code:
- `test_observe_cli.py`: post-hoc repro now asserts the full fixed meta shape
  (status `parser_failure_partial`, authoritative `.jsonl`, `authoritative_net`,
  `partial_direct`, `rebuilt` absent, warning substring); snapshot-only test pins
  `backend_disabled` + `authoritative_complete` + no net warning.
- `test_live_trace.py`: existing live TEST_FAIL_AFTER test extended into the
  no-promotion guard (null authority, `inferred_or_empty_placeholder`, no
  warning); NEW live truncate-then-promote repro with strace+snapshot backends.
- `test_viewer_server.py`: NEW session-endpoint pinning — `authoritative_net`
  passes through verbatim, `authoritative_artifact`/`default_artifact` = jsonl.
- Gotcha worth remembering (strace parsing & path identity lesson confirmed):
  the parser resolves relative syscall args against initial_cwd, not the fd
  annotation, and watched-roots scope-drop runs BEFORE the fail-after counter —
  live-repro fixtures need absolute in-root path arguments or the injected
  failure never fires and the test silently exercises the healthy path.
- Full suite: 265 tests, 0 failures, 0 skips.

## Implement — phase_3 build

Phase 2 committed after unanimous 3-way APPROVE (no issues from any reviewer).

Phase 3 (harness known-bug flip, spec FR4):
- `OPEN_BUGS[36].active = False` in `tests/agent_sessions/oracle.py` (one line,
  matching the #32/#33 flip convention).
- The two default-registry #36 selftests in `selftest_oracle.py` rewritten in
  the registry-tracking style (`..._gate_tracks_registry`): buggy shape → FAIL
  "regressed" when inactive / known-bug when active; healthy shape → PASS
  "#36 fixed" when inactive / stale-annotation FAIL when active.
- Grep audit: `selftest_degraded.py` and `check_degraded.py` are
  registry-explicit — untouched, still green (23 selftest-package tests OK).
- Full suite: 265 tests, 0 failures, 0 skips.
