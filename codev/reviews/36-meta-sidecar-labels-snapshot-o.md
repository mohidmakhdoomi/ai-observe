# Review: Meta sidecar must not label a snapshot-only `.jsonl` `authoritative_complete` after a direct-parser failure

## Metadata

- **ID**: 36
- **Spec**: `codev/specs/36-meta-sidecar-labels-snapshot-o.md`
- **Plan**: `codev/plans/36-meta-sidecar-labels-snapshot-o.md`
- **Protocol**: SPIR (strict, porch-orchestrated)

## Summary

Implemented the spec's Option A. `build_session_meta` now consults `parser_status`
on the `.jsonl`-authoritative branch: statuses in the allow-list
`{"ok", "live_error_rebuilt", "backend_disabled"}` keep today's roles, while any
other status — reachable only via `merge_snapshot_events`' empty-file promotion —
yields `jsonl.role = "authoritative_net"`, `partial.role = "partial_direct"`, and a
sidecar warning containing the pinned substring `snapshot fallback: net events only`.
`authoritative_event_path` semantics, recovery behavior, exit codes, and
`schema_version` are unchanged. The Spec 38 harness known-bug gate was flipped
(`OPEN_BUGS[36].active = False`), making the fix a hard regression assertion, and
docs now describe the new role vocabulary.

Delivered in four phases (one commit each, porch-swept): core derivation + unit
role matrix; integration/pinning tests over the real CLI; harness flip; docs.

## Spec Compliance

- [x] **FR1** (honest role on degraded promotion): allow-list-driven derivation in
      `build_session_meta`; unit matrix covers all six failure statuses plus an
      unknown-status case proving the understate-not-overstate direction.
- [x] **FR2** (healthy paths unchanged): allow-list boundary, rebuilt-authoritative,
      and no-promotion else-branch all pinned by unit tests; snapshot-only mode
      pinned by integration test.
- [x] **FR3** (warning): `NET_FALLBACK_WARNING` appended by rebinding (caller's list
      never mutated); both integration repros assert the pinned substring in
      `meta["warnings"]`.
- [x] **FR4** (known-bug flip lands with the fix): `OPEN_BUGS[36].active = False` +
      the two default-registry selftests rewritten registry-tracking style;
      `selftest_degraded.py` unchanged (registry-explicit) and green.
- [x] **FR5** (docs): `docs/observe.md` documents `authoritative_net` + a degraded
      troubleshooting bullet; `docs/agent-sessions.md` marks #36 fixed mirroring
      #33's phrasing.
- [x] **NFR1–NFR4**: no artifact-content/exit-code/schema changes; no viewer code
      change (pass-through tolerance pinned by a new viewer test); suite green with
      zero skips.
- [x] **Success criteria 1–5**: repro flips asserted end-to-end (post-hoc + live);
      oracle agreement via selftests; healthy paths pinned; suite green/zero skips;
      the self-consistency invariant is now enforced by the unit matrix and recorded
      in `codev/resources/arch.md`.

## Deviations from Plan

- **Phase 2 live repro fixture**: the plan's sketch ("fake strace + snapshot root
  with a changed file") needed one non-obvious adjustment — the synthetic trace's
  syscall path *arguments* had to be absolute and inside the watched root. The
  parser resolves relative arguments against the session cwd (not the fd
  annotation), and watched-root scope-dropping runs before the fail-after counter,
  so the plan-shaped fixture silently exercised the healthy path. No scope change;
  same deliverable.
- **Phase 3 selftest naming**: the two #36 selftests were renamed to the
  `..._gate_tracks_registry` convention (per the plan's "prefer registry-tracking
  assertions" guidance) rather than keeping their old names with flipped bodies;
  both registry states are asserted in each test.
- No other deviations; phases landed as planned with no review iterations.

## Lessons Learned

### What Went Well

- The plan's central bet — deriving both the role downgrade and the warning from
  one predicate in `build_session_meta`, with no promotion-flag plumbing — held up
  exactly as argued; the diff to product code is ~20 lines.
- The spec's allow-list direction and pre-enumerated status tables translated
  directly into the unit matrix; no test-design ambiguity remained at implement
  time.
- The Spec 38 harness's anticipated fixed shape (`FIXED_SHAPE` with
  `authoritative_net`, and the pinned warning phrasing) meant the ecosystem
  converged with zero friction — the fix landed where the harness already
  expected it.
- All twelve consultation reviews (4 phases × 3 models) approved on iteration 1.

### Challenges Encountered

- **Live repro fixture silently healthy**: the first fixture produced
  `parser.status = "ok"` because scope-dropping consumed both direct events before
  the injected failure could fire. Resolved by isolating the parser in a probe
  script, then switching to absolute in-root path arguments. The debugging cost was
  ~15 minutes; the general rule is now in `codev/resources/lessons-learned.md`.

### What Would Be Done Differently

- When a fixture drives an *injected failure*, assert the failure-mode
  precondition (here: `parser.status`) first, before the behavior under test —
  the initial fixture failure surfaced as a missing artifact two assertions
  downstream of the actual problem.

### Methodology Improvements

- None to propose for SPIR itself. Porch note for future builders: `porch run` is
  removed in the current porch; the strict-mode loop is `porch next` → work →
  `porch done` (the builder role docs still describe `porch run`).

## Technical Debt

- None introduced. Two explicitly deferred follow-ups live in the spec's Open
  Questions (viewer banner notice for `authoritative_net`; snapshot-only-mode
  vocabulary uniformity).

## Consultation Feedback

### Specify Phase (Round 1)

#### Gemini
- No concerns raised (APPROVE).

#### Codex
- **Concern**: FR3's warning needed a concrete observable check in acceptance
  criteria.
  - **Addressed**: FR3 pins the stable substring
    `"snapshot fallback: net events only"` and requires integration tests to
    assert it.
- **Concern**: unclear which tests/docs cover the broad failure-status set vs.
  stay `parser_failure_*`-specific.
  - **Addressed**: spec gained an explicit coverage-split preamble (unit matrix =
    broad set; integration repros = `parser_failure_partial` by construction;
    oracle/docs deliberately `parser_failure_*`-scoped).

#### Claude
- **Comment (non-blocking)**: match FR3's warning phrasing to
  `selftest_degraded.py`'s synthetic shape.
  - **Addressed**: adopted that phrasing as the pinned substring.

### Plan Phase (Round 1)

#### Gemini
- No concerns raised (APPROVE).

#### Codex
- **Concern (COMMENT)**: explicitly pin viewer pass-through tolerance for the new
  role string.
  - **Addressed**: added Phase 2 item 5 (viewer session-endpoint pinning test in
    `tests/test_viewer_server.py`), delivered in Phase 2.

#### Claude
- No concerns raised (APPROVE).

### Implement Phase — phase_1 (Round 1)

- No concerns raised — all three consultations approved (gemini / codex / claude,
  KEY_ISSUES: none).

### Implement Phase — phase_2 (Round 1)

- No concerns raised — all three consultations approved.

### Implement Phase — phase_3 (Round 1)

- No concerns raised — all three consultations approved.

### Implement Phase — phase_4 (Round 1)

- No concerns raised — all three consultations approved.

## Flaky Tests

No flaky tests encountered.

## Architecture Updates

- **COLD `codev/resources/arch.md` — "Artifact contract" section extended**: role
  derivation now consults `parser_status`; documented the allow-list of complete
  statuses, the `authoritative_net` / `partial_direct` degraded-promotion roles,
  the pinned warning substring, and the durable invariant (a sidecar recording a
  direct-parser failure never labels any artifact `authoritative_complete`).
- **HOT `arch-critical.md`**: no change. The fact is subsystem-scoped reference
  detail; the hot map's existing "Artifact contract — consult when changing
  session artifacts, sidecar authority, or recovery flows" entry already routes
  readers to the updated cold section. No new top-level cold sections, so the map
  stays accurate; cap untouched.

## Lessons Learned Updates

- **COLD `codev/resources/lessons-learned.md` — new entry** "Keep
  failure-injection fixture paths absolute and inside the watched roots": relative
  syscall args resolve against the session cwd and scope-dropping precedes the
  fail-after counter, so out-of-root fixture paths make injected failures silently
  not fire. Placed adjacent to the existing strace-parsing/path-identity entries,
  which the hot map's "Strace parsing, annotations & path identity" topic already
  covers.
- **HOT `lessons-critical.md`**: no change — this is a test-fixture recipe
  (spec-narrow), which belongs in the cold archive by the tiering rules; cap
  untouched.

## Follow-up Items

- Deferred (spec Open Questions, not scheduled): viewer banner notice keyed on
  `authoritative_net`; snapshot-only mode adopting a net-describing role for
  vocabulary uniformity.
- Builder role docs still reference the removed `porch run` command — worth a
  docs touch-up in a future MAINTAIN pass.
