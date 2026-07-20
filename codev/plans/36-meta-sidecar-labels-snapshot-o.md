# Plan 36: Meta sidecar must not label a snapshot-only `.jsonl` `authoritative_complete` after a direct-parser failure

## Metadata

- **ID**: plan-2026-07-19-meta-sidecar-authoritative-net
- **Status**: draft
- **Specification**: `codev/specs/36-meta-sidecar-labels-snapshot-o.md` (approved)
- **Created**: 2026-07-19

## Executive Summary

Implement the spec's Option A: introduce the role **`authoritative_net`** so that a
`.jsonl` promoted from the snapshot fallback after a direct-parser failure stays the
authoritative event artifact (`authoritative_event_path` unchanged) while its role
honestly describes net-only fidelity.

The entire behavioral fix lands in **one derivation site**: `build_session_meta`
(`src/ai_observe/observe.py`). Both the role downgrade (FR1) and the sidecar warning
(FR3) key off the same condition — `authoritative_path == logs.jsonl_path` and
`parser_status` outside the allow-list `{"ok", "live_error_rebuilt",
"backend_disabled"}`. `merge_snapshot_events` and the strace backend are **not
modified**: on every path where `.jsonl` is authoritative with a non-allow-listed
status, a snapshot promotion is the only way to get there (strace failure branches
null `authoritative_path`; strace-only mode has nothing to promote), so the meta
builder can derive "degraded promotion" without new plumbing. This was chosen over
threading a promotion flag through `BackendState` because it keeps the
role-and-warning logic in a single place with a single predicate, and avoids a
signature change to injected backend callables.

Four small phases: (1) core derivation + unit role-matrix, (2) integration and
pinning tests over the real CLI paths, (3) the Spec 38 harness known-bug flip with
its selftest updates, (4) docs. Each phase is a green, committable unit.

## Success Metrics

- [ ] All specification criteria met (spec §Success Criteria 1–5)
- [ ] Sidecar self-consistency invariant holds: a `.meta.json` recording a
      direct-parser failure never labels any artifact `authoritative_complete`
- [ ] Healthy paths byte-identical in role output (pinned by tests)
- [ ] Full unittest suite passes with **zero skips** (CI fail-loud rule)
- [ ] `OPEN_BUGS[36]` flipped inactive; harness selftests pass
- [ ] Documentation complete (`docs/observe.md`, `docs/agent-sessions.md`)

## Phases (Machine Readable)

```json
{
  "phases": [
    {"id": "phase_1", "title": "Core role derivation: authoritative_net + net-fallback warning + unit role matrix"},
    {"id": "phase_2", "title": "Integration and pinning tests over the real CLI degraded paths"},
    {"id": "phase_3", "title": "Harness known-bug flip: OPEN_BUGS[36] inactive + selftest updates"},
    {"id": "phase_4", "title": "Documentation: role vocabulary and known-bug table updates"}
  ]
}
```

## Phase Breakdown

### Phase 1: Core role derivation — `authoritative_net` + net-fallback warning + unit role matrix

**Dependencies**: None
**Status**: pending

#### Objectives

Make `build_session_meta` consult `parser_status`, emitting an honest role and a
degradation warning for snapshot-promoted `.jsonl` artifacts, and prove the full
status matrix with direct unit tests.

#### Deliverables

- [ ] `src/ai_observe/observe.py` — modified `build_session_meta`
- [ ] `tests/test_session_meta.py` — NEW unit test module (staged on creation)
- [ ] All existing tests still green

#### Implementation Details

In `src/ai_observe/observe.py`, adjacent to `build_session_meta`:

```python
# Statuses under which an authoritative .jsonl genuinely holds everything the
# session promised: full direct stream ("ok", "live_error_rebuilt") or net-only
# by configuration ("backend_disabled", snapshot-only mode). Expressed as an
# ALLOW-list so an unanticipated future status understates fidelity
# (authoritative_net) instead of overstating it (spec FR1).
_JSONL_COMPLETE_STATUSES = frozenset({"ok", "live_error_rebuilt", "backend_disabled"})

NET_FALLBACK_WARNING = "snapshot fallback: net events only; direct-layer detail was lost"
```

Rework the `authoritative_path == logs.jsonl_path` branch of `build_session_meta`
(observe.py:580–583):

- `parser_status in _JSONL_COMPLETE_STATUSES` → today's roles unchanged
  (`authoritative_complete` / `absent` / `absent_or_parser_failure_partial`).
- otherwise → `jsonl_role = "authoritative_net"`,
  `partial_role = "partial_direct"`, `rebuilt_role = "absent"`, and the emitted
  `warnings` list becomes `[*warnings, NET_FALLBACK_WARNING]` (rebind — the
  caller's list object must NOT be mutated).

No other branch changes. No changes to `merge_snapshot_events`, the strace
backend, exit codes, or `schema_version` (stays `1`). The warning contains the
spec-pinned stable substring `"snapshot fallback: net events only"`.

**Why derivation is sound (no promotion flag needed)**: `.jsonl`-authoritative +
non-allow-listed status is reachable *only* via `merge_snapshot_events`' empty-file
promotion — every strace failure branch nulls `authoritative_path`, and without the
snapshot backend nothing restores it. Documented in a code comment.

#### Acceptance Criteria

- [ ] Unit role-matrix (below) fully passes
- [ ] Existing suite green (`python -m unittest discover -s tests`) with no skips
- [ ] `warnings` input list is not mutated by the call

#### Test Plan

**Unit tests** (`tests/test_session_meta.py`, calling `build_session_meta`
directly with a synthetic `LogPaths`) — this is the spec's designated home of the
**broad failure set**:

- `.jsonl` authoritative × all six affected failure statuses
  (`parser_failure_partial`, `parser_failure_empty_partial`,
  `live_error_rebuild_parser_failure`, `live_error_rebuild_failed`,
  `live_timeout_rebuild_parser_failure`, `live_timeout_rebuild_failed`) →
  `authoritative_net` + `partial_direct` + `rebuilt` `absent` + exactly one
  `NET_FALLBACK_WARNING` appended + `authoritative_event_path` still the `.jsonl`
  name.
- `.jsonl` authoritative × allow-list boundary (`ok`, `live_error_rebuilt`,
  `backend_disabled`) → `authoritative_complete`, no net warning.
- `.jsonl` authoritative × hypothetical unknown status (e.g.
  `"future_new_status"`) → `authoritative_net` (proves allow-list direction).
- `.jsonl.rebuilt` authoritative × `live_timeout_rebuilt` → rebuilt
  `authoritative_complete`, jsonl `partial_live` (unchanged branch).
- Authoritative `None` × `parser_failure_partial` →
  `inferred_or_empty_placeholder`; × `live_timeout_rebuild_failed` →
  `partial_live` (unchanged else-branch, guards against over-reach).
- Caller's `warnings` list identity/content unchanged after a degraded call.

#### Rollback Strategy

Single-commit revert; no data or schema migration involved.

#### Risks

- **Risk**: deriving the warning in the meta builder fires it on an unforeseen
  non-promotion path.
  - **Mitigation**: the unit matrix enumerates every reachable
    status × authoritative-path combination from the spec's tables; the
    no-promotion cases assert no warning is added.

---

### Phase 2: Integration and pinning tests over the real CLI degraded paths

**Dependencies**: Phase 1
**Status**: pending

#### Objectives

Prove the end-to-end sidecar shape through the real CLI on the repro paths, and
pin the healthy paths against drift.

#### Deliverables

- [ ] `tests/test_observe_cli.py` — extended post-hoc repro + snapshot-only pinning
- [ ] `tests/test_live_trace.py` — live repro + no-promotion guard

#### Implementation Details

Integration tests are **`parser_failure_partial`-specific by construction** (the
`AI_OBSERVE_TEST_FAIL_AFTER` hook produces only that status); breadth lives in
Phase 1's unit matrix (spec's coverage split).

1. **Post-hoc repro** — extend
   `test_snapshot_parser_failure_keeps_partial_direct_and_writes_inferred_jsonl`:
   read `pf-snapshot.meta.json` and assert
   `parser.status == "parser_failure_partial"`,
   `artifacts.authoritative_event_path == "pf-snapshot.jsonl"`,
   `artifacts.jsonl.role == "authoritative_net"`,
   `artifacts.partial.role == "partial_direct"`,
   `artifacts.rebuilt.role == "absent"`, and some warning containing
   `"snapshot fallback: net events only"`.
2. **Live repro** — new test in `tests/test_live_trace.py` modeled on the
   existing live `TEST_FAIL_AFTER` fixtures (~line 330): live parse enabled, fake
   strace, snapshot backend with a root containing a changed file so the
   truncate-then-promote path runs; assert the same meta shape as (1).
3. **No-promotion guard** — strace-only (`AI_OBSERVE_BACKENDS=strace`) parse
   failure: assert `authoritative_event_path` is `null`, jsonl role
   `inferred_or_empty_placeholder`, and no net-fallback warning (extend an
   existing strace-only failure test if one fits, else a small new test).
4. **Snapshot-only pinning** — extend
   `test_snapshot_only_mode_runs_without_strace_and_emits_inferred_events`:
   assert `parser.status == "backend_disabled"`,
   `artifacts.jsonl.role == "authoritative_complete"`,
   `authoritative_event_path == "snapshot-only.jsonl"`, no net-fallback warning
   (per the backend-scope test-pinning lesson).

#### Acceptance Criteria

- [ ] Spec Success Criterion 1 (repro flips) asserted end-to-end
- [ ] Spec Success Criterion 3 (no healthy-path drift) pinned
- [ ] Suite green, zero skips

#### Test Plan

Covered above — this phase *is* tests. Behavior-first: all four scenarios drive
the real `ai-observe` CLI binary and read real artifacts; no mocking of the
system under test.

#### Rollback Strategy

Single-commit revert (tests only).

#### Risks

- **Risk**: constructing the live truncate-then-promote fixture proves fiddly
  (timing of live tracer + snapshot root diff).
  - **Mitigation**: reuse the proven fake-strace live fixtures in
    `test_live_trace.py`; the post-hoc repro (1) already covers the promotion
    branch itself, so the live test's unique value is only the truncation path.

---

### Phase 3: Harness known-bug flip — `OPEN_BUGS[36]` inactive + selftest updates

**Dependencies**: Phase 1 (the fix must exist before the gate hard-asserts it)
**Status**: pending

#### Objectives

Land the Spec 38 rot-proof flip (spec FR4): the #36 gate becomes a hard
regression assertion, and the default-registry selftests reflect the flipped
state.

#### Deliverables

- [ ] `tests/agent_sessions/oracle.py` — `OPEN_BUGS[36].active = False` (one line)
- [ ] `tests/agent_sessions/selftest/selftest_oracle.py` — two updated tests

#### Implementation Details

- Flip `OPEN_BUGS[36]` to `active=False`, matching how #32/#33 are recorded.
- `selftest_oracle.py` updates (mirror the file's existing #32/#33 conventions,
  preferring registry-tracking assertions like
  `test_marker_noise_gate_tracks_registry` where they fit):
  - `test_authority_overstated_is_known_bug_36`: the buggy shape with the default
    (now-inactive) registry → assert `FAIL` with `"regressed"` in the detail.
  - `test_authority_ok_when_parser_healthy`: healthy shape with the default
    registry → assert `PASS` (`"#36 fixed: correct behavior asserted"` path).
- `selftest_degraded.py` needs **no change** (registry-explicit both-direction
  coverage; its `FIXED_SHAPE` already matches Phase 1's real output shape).
- No change to the `authority_overstated` predicate (spec: out of scope).

#### Acceptance Criteria

- [ ] `python -m unittest tests.agent_sessions.selftest.selftest_oracle` and
      `...selftest_degraded` pass
- [ ] Full suite green, zero skips
- [ ] Spec Success Criterion 2 satisfied (gate PASS with flipped registry against
      the real fixed shape — demonstrated via `selftest_degraded`'s FIXED_SHAPE
      equivalence to Phase 2's asserted real sidecar)

#### Test Plan

The deliverables are themselves tests; additionally run the whole
`tests/agent_sessions/selftest/` package to catch cross-selftest registry
assumptions.

#### Rollback Strategy

Single-commit revert restores the annotated known-bug state (gate returns to
demanding-the-flip only if the Phase 1 fix is also reverted — the two commits
revert cleanly in reverse order).

#### Risks

- **Risk**: another selftest implicitly assumes `OPEN_BUGS[36].active` is True.
  - **Mitigation**: run the full selftest package; grep `OPEN_BUGS[36]` /
    `known_bug_status(36)` usages before committing.

---

### Phase 4: Documentation — role vocabulary and known-bug table

**Dependencies**: Phase 3 (docs describe the flipped state)
**Status**: pending

#### Objectives

Make the sidecar's role vocabulary change discoverable (spec FR5).

#### Deliverables

- [ ] `docs/observe.md` — document `authoritative_net`: when it appears (snapshot
      fallback after a direct-parser failure), what it means (net/inferred events
      only; transients missing), where direct evidence survives
      (`.jsonl.partial`), and the paired sidecar warning; touch the
      degraded-artifacts troubleshooting note
- [ ] `docs/agent-sessions.md` — #36 known-bugs table row marked **fixed**
      (matching #33's phrasing: the gate now hard-asserts the role downgrade);
      adjust the `degraded` scenario description

#### Acceptance Criteria

- [ ] Docs accurately reflect implemented behavior (roles, warning substring,
      unchanged `authoritative_event_path` semantics)
- [ ] No stale "while #36 is open" phrasing remains
      (`grep -rn "#36" docs/` audit)

#### Test Plan

Manual review against Phase 1/2 assertions; grep audit above.

#### Rollback Strategy

Single-commit revert (docs only).

#### Risks

- **Risk**: doc drift (describing intent rather than implementation).
  - **Mitigation**: write docs from the merged test assertions, not from the spec.

## Dependency Map

```
Phase 1 ──→ Phase 2
   └──────→ Phase 3 ──→ Phase 4
```

(Phases 2 and 3 both depend only on Phase 1 but are executed in order for a
linear, reviewable commit history.)

## Resource Requirements

Standard dev environment: Linux, Python 3 with `unittest`, `strace` present for
the integration tests that exercise the real backend (the repro tests use the
suite's fake-strace fixtures, so no special privileges are needed). No
infrastructure, config, or monitoring changes.

## Integration Points

- **Browser viewer** (`src/ai_observe/viewer/`): consumes roles pass-through; no
  code change (spec NFR4). Phase 2's assertions cover the sidecar side of that
  contract.
- **Live-agent harness** (`tests/agent_sessions/`): Phase 3 flips its gate; the
  opt-in live `degraded` scenario will hard-assert the fix from then on.

## Risk Analysis

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Unknown external role consumers see the new string | L | L | Docs (Phase 4); pointer semantics unchanged; vocabulary never a documented closed set |
| Warning derivation fires on a non-promotion path | L | M | Phase 1 unit matrix enumerates all reachable combinations |
| Live-repro fixture flaky | M | L | Deterministic fake-strace fixtures; post-hoc test already covers the promotion branch |
| Future parser status overstates again | L | M | Allow-list direction + unknown-status unit test |

## Validation Checkpoints

1. **After Phase 1**: unit matrix green; full suite green; no skip output.
2. **After Phase 2**: spec Success Criteria 1 and 3 demonstrably asserted.
3. **After Phase 3**: selftest package green; spec Success Criterion 2 met.
4. **Before PR**: `grep -rn "authoritative_net"` shows src + tests + docs + harness
   aligned; full suite + packaging smoke green.

## Documentation Updates Required

- [ ] `docs/observe.md` (Phase 4)
- [ ] `docs/agent-sessions.md` (Phase 4)
- [ ] Review-phase governance docs (`codev/resources/*`) handled in SPIR Review
      phase per protocol, not in this plan's phases

## Post-Implementation Tasks

- [ ] SPIR Review phase: review doc, lessons learned, arch-doc routing
- [ ] Optional follow-ups recorded in spec Open Questions (viewer banner notice;
      snapshot-only vocabulary uniformity) — explicitly NOT this plan

## Expert Review

*(populated by the porch-driven 3-way review)*

## Change Log

| Date | Change | Reason | Author |
|------|--------|--------|--------|
| 2026-07-19 | Initial plan | — | builder spir-36 |

## Notes

- Commit messages: `[Spec 36][Phase: <name>] type: description`, one commit per
  phase, PR opened after the final phase per the builder PR strategy.
- New test file `tests/test_session_meta.py` must be `git add`ed the moment it is
  created (porch commit-sweep lesson).
