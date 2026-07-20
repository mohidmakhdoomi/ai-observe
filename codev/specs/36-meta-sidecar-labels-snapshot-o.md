# Spec 36: Meta sidecar must not label a snapshot-only `.jsonl` `authoritative_complete` after a direct-parser failure

## Metadata

- **ID**: 36
- **Issue**: #36 — "meta sidecar labels snapshot-only .jsonl 'authoritative_complete' after direct-parser failure — role overstates fidelity"
- **Status**: draft
- **Protocol**: SPIR
- **Discovered by**: live-agent testing round 2 (#35), finding F6 in `experiments/FINDINGS-round2.md`; minimal repro `experiments/6_degraded_recovery/degraded.py` (`parse_failure_partial`); architect-verified against source

## Problem Statement

The `.meta.json` sidecar is the artifact contract's trust signal: it records which
event artifact is authoritative and what role each artifact plays, so consumers
(viewer, tests, tooling) never have to re-derive parser state from filenames
(`codev/resources/arch.md`, "Artifact contract"; `codev/resources/lessons-learned.md`,
"Make artifact authority explicit when recovery can yield multiple valid outputs").

After a direct-parser failure, the sidecar contradicts itself:

- `parser.status` honestly records the failure (e.g. `parser_failure_partial`), and
- `artifacts.jsonl.role` says `authoritative_complete` with
  `artifacts.authoritative_event_path` pointing at that `.jsonl`,

while the `.jsonl` in question contains **only net (inferred) snapshot events** — the
direct-layer stream was lost. For create-only tasks the net view looks complete; any
ephemeral/transient operations (create-then-delete, temp files, transient renames)
are **silently missing**. A consumer keying off the role/authoritative path — the
sidecar's documented purpose — treats a degraded session as healthy. The healthy
`.jsonl` and the degraded snapshot-only `.jsonl` carry the same role label, so no
consumer can distinguish them without re-deriving parser state, which is exactly
what the sidecar exists to prevent.

Writing the snapshot events into the empty `.jsonl` as a net fallback is defensible
recovery behavior and is **not** being changed. The bug is the fidelity-overstating
label.

## Root Cause (verified against source)

Three steps conspire (`src/ai_observe/observe.py`, `src/ai_observe/backends/strace.py`):

1. **Failure paths null authority and leave `.jsonl` empty.** The strace backend's
   failure branches set `authoritative_path = None` and either truncate `.jsonl` to
   empty (live paths) or never write it (post-hoc paths). Either way `.jsonl` exists
   and is empty, because `prepare_logs` pre-creates it with `exclusive_touch`
   (`observe.py:1059`).
2. **`merge_snapshot_events` silently promotes the fallback to full authority**
   (`observe.py:530-532`): when `.jsonl` exists and is empty, it writes the snapshot
   (inferred/net) events into it and returns `logs.jsonl_path` as the new
   authoritative path, with no record that this was a degraded promotion.
3. **`build_session_meta` derives the role from path identity alone**
   (`observe.py:580-583`): `authoritative_path == logs.jsonl_path` →
   `jsonl_role = "authoritative_complete"` — `parser_status` is never consulted on
   this branch.

Notably, the role `inferred_or_empty_placeholder` (`observe.py:588`) exists to
describe exactly this kind of inferred-content `.jsonl`, but the authority flip in
step 2 makes that branch unreachable whenever snapshot events exist — i.e., in
precisely the scenario the role was designed for.

### Affected parser statuses

Because `.jsonl` is always pre-created, the promotion in step 2 fires on **every**
direct-parser-failure status whenever the snapshot layer produced events, not just
`parser_failure_partial`:

| `parser.status` | Path | `.jsonl` at merge time | Promoted today? |
|---|---|---|---|
| `parser_failure_partial` | live parser failed (partial saved) or post-hoc parser failed with partial events | empty | yes → mislabeled |
| `parser_failure_empty_partial` | post-hoc parser failed, no partial events | empty | yes → mislabeled |
| `live_error_rebuild_parser_failure` | live error, rebuild parser failed | truncated empty | yes → mislabeled |
| `live_error_rebuild_failed` | live error, rebuild failed outright | truncated empty | yes → mislabeled |
| `live_timeout_rebuild_parser_failure` | live timeout, rebuild parser failed | live partial (usually non-empty) | only if live wrote nothing → mislabeled |
| `live_timeout_rebuild_failed` | live timeout, rebuild failed outright | live partial (usually non-empty) | only if live wrote nothing → mislabeled |

Healthy paths for comparison (must not change):

| `parser.status` | Authoritative artifact | `.jsonl` role today | Correct? |
|---|---|---|---|
| `ok` | `.jsonl` | `authoritative_complete` | yes |
| `live_error_rebuilt` | `.jsonl` (successful post-hoc rebuild) | `authoritative_complete` | yes |
| `live_timeout_rebuilt` | `.jsonl.rebuilt` | `partial_live` (rebuilt gets `authoritative_complete`) | yes |
| `backend_disabled` (snapshot-only mode, `AI_OBSERVE_BACKENDS=snapshot`) | `.jsonl` via the same promotion branch | `authoritative_complete` | yes — see "Snapshot-only mode" below |

### Snapshot-only mode is not part of the bug

With `AI_OBSERVE_BACKENDS=snapshot` the user opted out of the direct layer; the
promoted `.jsonl` holds everything the session promised to observe (net changes),
`parser.status = "backend_disabled"` / `parser.source = "none"` make the mode
discoverable, and no fidelity was lost relative to the product promise. Nothing
was overstated, so this path keeps `authoritative_complete`. (Whether snapshot-only
mode should *also* adopt a net-describing role for vocabulary consistency is noted
under Open Questions as an explicitly deferred follow-up, not part of this fix.)

## Current State vs Desired State

**Current**: on the degraded paths above, `.meta.json` reports
`parser.status = parser_failure_*` (accurate) alongside
`jsonl.role = authoritative_complete` + `authoritative_event_path = <session>.jsonl`
(overstated). Consumers trusting the role see a healthy, complete session.

**Desired**: the sidecar stays internally consistent. The promoted `.jsonl` remains
the best-available authoritative event artifact (`authoritative_event_path` still
names it — recovery behavior unchanged), but its role honestly describes fidelity:
a new role **`authoritative_net`** — "authoritative for this session, net (inferred)
events only; the direct-layer stream was lost; surviving direct evidence is in
`.jsonl.partial`".

## Stakeholders

- **Sidecar consumers** (browser viewer, `tests/agent_sessions` oracle, any
  downstream tooling keying off `authoritative_event_path` + roles): need a truthful
  trust signal without re-deriving parser state.
- **Live-agent testing harness (Spec 38)**: ships a rot-proof known-bug gate for #36
  (`tests/agent_sessions/oracle.py`, `OPEN_BUGS[36]` +
  `expect_authority_not_overstated`). Its selftest's anticipated fixed shape already
  uses `authoritative_net`. The gate *demands* the registry flip land with the fix.
- **Users inspecting degraded sessions**: must be able to tell "complete direct
  record" from "net fallback, transients missing" from the sidecar alone.

## Assumptions and Constraints

- No Baked Decisions section exists on issue #36. The issue pins the fix direction:
  do not label the `.jsonl` `authoritative_complete` when `parser_status` indicates
  a direct-parser failure and the content is snapshot-only; `build_session_meta`
  must consider `parser_status`, not just path identity.
- The net-fallback recovery itself (writing snapshot events into the empty `.jsonl`)
  is explicitly out of scope to change — the issue calls it defensible.
- The meta sidecar's `schema_version` stays `1`: a new role string is an additive
  vocabulary extension; the two in-repo consumers (viewer, oracle) pass roles
  through / match tolerantly, and the role vocabulary is not enumerated as a closed
  set anywhere in docs or code.
- CI fails loud on any unittest skip — no capability-gated skips in new tests.
- Exit-code semantics (`AI_OBSERVE_STRICT_PARSE`, `parse_failed`) are unchanged.

## Solution Exploration

### Option A — new role `authoritative_net`, authority pointer kept (RECOMMENDED)

Keep `merge_snapshot_events`' promotion (the `.jsonl` stays the best-available
authoritative artifact and `authoritative_event_path` still names it), but make
`build_session_meta` role derivation consult `parser_status`: when the authoritative
path is `.jsonl` and the parser status is not one of the healthy statuses for a
`.jsonl`-authoritative session (`ok`, `live_error_rebuilt`, `backend_disabled`),
emit `jsonl.role = "authoritative_net"` and `partial.role = "partial_direct"`.

- **Pros**: sidecar stays self-consistent *and* keeps pointing at the best artifact;
  viewer artifact selection (keyed off `authoritative_event_path`) is unchanged;
  matches the harness's anticipated fixed shape (`selftest_degraded.py`'s
  `FIXED_SHAPE = _meta("authoritative_net")`), so the ecosystem converges with zero
  friction; smallest behavioral delta (labels only).
- **Cons**: introduces a new role string consumers may not know (mitigated: both
  in-repo consumers are tolerant; docs updated).
- **Complexity**: low. **Risk**: low.

### Option B — keep `authoritative_path = None` through the merge

Change `merge_snapshot_events` to write the snapshot events into the empty `.jsonl`
*without* returning it as authoritative. `build_session_meta`'s existing else-branch
then labels it `inferred_or_empty_placeholder` — the role written for this case.

- **Pros**: no new vocabulary; reactivates a designed-but-unreachable branch.
- **Cons**: the sidecar stops pointing at any authoritative artifact even though a
  useful best-available one exists — consumers lose the pointer the sidecar exists
  to provide; `inferred_or_empty_placeholder` is ambiguous ("inferred OR empty" —
  the reader can't tell which without opening the file); diverges from the
  harness's anticipated fixed shape; viewer's `authoritative_artifact` becomes null
  for these sessions.
- **Complexity**: low. **Risk**: medium (semantics regression for consumers that
  use the pointer as "best artifact to read").

### Option C — broaden `authoritative_net` to all snapshot-only content (including snapshot-only mode)

Like A, but key the role on content provenance rather than failure: any promotion
of a snapshot-only `.jsonl` (including `AI_OBSERVE_BACKENDS=snapshot` sessions)
gets `authoritative_net`.

- **Pros**: role vocabulary describes content uniformly ("net" = inferred-only).
- **Cons**: relabels healthy, documented snapshot-only sessions where nothing was
  overstated — a behavior change outside the bug's scope with wider doc/test
  ripple; "complete relative to the configured backends" is already accurate there.
- **Complexity**: low-medium. **Risk**: medium (touches non-degraded workflows).

**Recommendation: Option A.** It fixes exactly the contract violation (the label),
preserves the recovery and the pointer, and lands where the testing harness already
expects the fix to land. Option C is deferred (see Open Questions).

## Proposed Solution (Option A, detailed)

### Functional requirements

1. **FR1 — honest role on degraded promotion (MUST)**: when the final
   `authoritative_path` is `logs.jsonl_path` and `parser_status` is not in the
   healthy set for a `.jsonl`-authoritative session — healthy set:
   `{"ok", "live_error_rebuilt", "backend_disabled"}` — `build_session_meta` emits:
   - `artifacts.jsonl.role = "authoritative_net"`,
   - `artifacts.partial.role = "partial_direct"` (the surviving direct evidence),
   - `artifacts.rebuilt.role = "absent"` (unchanged from today's failure branches),
   - `artifacts.authoritative_event_path` still names the `.jsonl` (unchanged).

   The healthy set is expressed as an **allow-list of success statuses**, not a
   deny-list of failure statuses, so an unanticipated future status degrades
   toward *understating* (`authoritative_net`) rather than overstating fidelity.

2. **FR2 — healthy paths unchanged (MUST)**: sessions with `parser_status` in
   `{"ok", "live_error_rebuilt"}` (`.jsonl` authoritative), `live_timeout_rebuilt`
   (`.jsonl.rebuilt` authoritative), and snapshot-only mode (`backend_disabled`)
   keep today's roles, including `authoritative_complete`. Failure paths where no
   promotion occurred (`authoritative_path is None`) keep today's else-branch roles
   (`partial_live` / `inferred_or_empty_placeholder`).

3. **FR3 — degraded promotion is recorded in warnings (SHOULD)**: when the
   promotion happens while the direct parser failed, append a warning to the
   sidecar's `warnings` so the viewer's warning count and banner surface the
   degradation. The warning text must contain the stable substring
   `"snapshot fallback: net events only"` (matching the phrasing the harness's
   synthetic fixed shape already uses in `selftest_degraded.py`); wording around
   it may elaborate (e.g. "…; direct-layer detail was lost"). If FR3 is
   implemented, the promoted-failure integration tests MUST assert the presence
   of that substring in `meta["warnings"]`. Mechanism (a return-flag from
   `merge_snapshot_events`, a state field, or derivation in the meta builder) is a
   plan-phase decision.

4. **FR4 — known-bug gate flip lands with the fix (MUST)**: in the same change,
   flip `OPEN_BUGS[36].active = False` in `tests/agent_sessions/oracle.py` and
   update the two default-registry #36 selftests in
   `tests/agent_sessions/selftest/selftest_oracle.py` to the flipped expectations
   (buggy shape → FAIL "regressed"; healthy shape → PASS), mirroring how #32/#33
   were flipped. `selftest_degraded.py` pins both registry states explicitly and
   needs no change. (The harness's rot-proof design makes shipping the fix without
   the flip a loud FAIL in any live harness run.)

5. **FR5 — docs reflect the new contract (SHOULD)**:
   - `docs/observe.md`: document the `authoritative_net` role in the artifact/
     degraded-recovery discussion (what it means, when it appears, where the
     surviving direct evidence lives).
   - `docs/agent-sessions.md`: mark the #36 row in the known-bugs table **fixed**
     (matching #33's phrasing) and adjust the `degraded` scenario description.

### Non-functional requirements

- **NFR1 (MUST)**: no change to event-artifact *content*, exit codes,
  `STRICT_PARSE` behavior, or which files are written on any path.
- **NFR2 (MUST)**: meta `schema_version` stays `1` (additive role vocabulary).
- **NFR3 (MUST)**: full unittest suite passes with zero skips (CI fail-loud rule).
- **NFR4 (MUST NOT)**: no viewer behavior change is required; the viewer passes
  role strings through and selects artifacts via `authoritative_event_path`, which
  is unchanged. (A viewer banner notice for `authoritative_net` is explicitly out
  of scope — see below.)

### Behavior after the fix (delta summary)

| Scenario | `parser.status` | `authoritative_event_path` | `.jsonl` role before → after |
|---|---|---|---|
| Direct parse failure + snapshot events (live or post-hoc) | `parser_failure_partial` / `parser_failure_empty_partial` | `<session>.jsonl` | `authoritative_complete` → **`authoritative_net`** |
| Live error, rebuild failed, snapshot events | `live_error_rebuild_parser_failure` / `live_error_rebuild_failed` | `<session>.jsonl` | `authoritative_complete` → **`authoritative_net`** |
| Live timeout, rebuild failed, live layer wrote nothing, snapshot events | `live_timeout_rebuild_parser_failure` / `live_timeout_rebuild_failed` | `<session>.jsonl` | `authoritative_complete` → **`authoritative_net`** |
| Same failures, no snapshot events (no promotion) | (same) | `null` | unchanged (`partial_live` / `inferred_or_empty_placeholder`) |
| Healthy / rebuilt / snapshot-only | `ok`, `live_error_rebuilt`, `live_timeout_rebuilt`, `backend_disabled` | unchanged | unchanged |

Additionally on the promoted-failure rows: `partial` role
`absent_or_parser_failure_partial` → **`partial_direct`**.

## Scope

**In scope**
- Role derivation in `build_session_meta` (and whatever minimal plumbing FR3's
  warning needs, e.g. a promotion signal from `merge_snapshot_events`).
- Tests: unit coverage of the new role logic; integration coverage of the repro
  path; pinning tests for healthy paths (including snapshot-only mode's current
  labels, per the backend-scope test-pinning lesson).
- Harness registry flip + selftest updates (FR4).
- Docs updates (FR5).

**Out of scope**
- Changing the net-fallback recovery itself (what gets written where).
- Relabeling snapshot-only mode (`backend_disabled`) — deferred (Open Questions).
- Viewer UI changes (banner notice for `authoritative_net`); the role reaches the
  banner's button model already, and `parser_status` notices already fire.
- Broadening the oracle's `authority_overstated` predicate beyond
  `parser_failure*` statuses — after this fix no failure status can produce
  `authoritative_complete`, so the existing predicate remains sufficient for its
  scenario.
- F7 (#35's observer-SIGKILL finding) — informational, separate.
- The historical `experiments/` records — they document the pre-fix state and are
  not updated.

## Success Criteria / Acceptance

1. **Repro flips**: the issue's repro state (forced parse failure via
   `AI_OBSERVE_TEST_FAIL_AFTER` with snapshot events present) yields a `.meta.json`
   with `parser.status = "parser_failure_partial"`,
   `authoritative_event_path = <session>.jsonl`,
   `artifacts.jsonl.role = "authoritative_net"`,
   `artifacts.partial.role = "partial_direct"` — asserted by an integration test.
2. **Oracle agreement**: `authority_overstated(meta)` is `False` for real fixed
   sidecars; `expect_authority_not_overstated` returns PASS with the flipped
   registry (`selftest_degraded.py`'s `FIXED_SHAPE` matches real output shape).
3. **No healthy-path drift**: tests pin `ok` → `authoritative_complete`,
   `live_timeout_rebuilt` → rebuilt `authoritative_complete` (existing tests), and
   snapshot-only mode's meta labels (new pinning assertion).
4. **Suite green, zero skips**: full `unittest` suite passes; CI skip-gate stays
   silent.
5. **Sidecar self-consistency invariant** (the durable statement of this fix): a
   `.meta.json` whose `parser.status` records a direct-parser failure never labels
   any artifact `authoritative_complete`.

### Test scenarios

Coverage split — where the broad failure set is exercised vs. where
`parser_failure_partial` specificity is deliberate:

- The **unit role-matrix test is the home of the broad failure set**: all six
  affected statuses from the table above, the allow-list boundary statuses, and
  the unknown-status case are asserted there.
- The **integration repro tests are `parser_failure_partial`-specific by
  construction** (the in-tree `AI_OBSERVE_TEST_FAIL_AFTER` hook can only produce
  that status); they exist to prove the end-to-end path, not status breadth.
- The **oracle predicate and docs known-bug wording stay `parser_failure_*`-scoped
  deliberately** (see Out of scope): after FR1 no failure status can produce
  `authoritative_complete`, so the gate's original signature remains sufficient
  for its scenario.

Scenarios:

- **Unit — role matrix**: `build_session_meta` called directly across the status ×
  authoritative-path matrix above; every before→after cell asserted — all six
  affected failure statuses, the allow-list boundary statuses (`ok`,
  `live_error_rebuilt`, `backend_disabled`), and a hypothetical unknown status
  with `.jsonl` authority (must yield `authoritative_net`, proving the allow-list
  direction).
- **Integration — post-hoc repro**: extend
  `test_snapshot_parser_failure_keeps_partial_direct_and_writes_inferred_jsonl`
  (`tests/test_observe_cli.py`) with `.meta.json` assertions (criterion 1),
  including the FR3 warning substring if FR3 is implemented.
- **Integration — live repro**: the live-parser-failure equivalent (fake strace +
  `TEST_FAIL_AFTER` with live parse enabled, snapshot events present) asserting the
  same meta shape (and FR3 warning substring), covering the truncate-then-promote
  path.
- **Integration — no-promotion failure**: parse failure with no snapshot events →
  `authoritative_event_path` null, roles unchanged (guards against over-reach).
- **Pinning — snapshot-only mode**: `AI_OBSERVE_BACKENDS=snapshot` session meta
  keeps `backend_disabled` + `.jsonl` `authoritative_complete`.
- **Harness selftests**: updated `selftest_oracle.py` #36 expectations pass with
  the flipped registry; `selftest_degraded.py` passes unchanged.

## Open Questions

- **Important — none blocking.**
- **Nice-to-know / deferred**: should snapshot-only mode (`backend_disabled`)
  eventually adopt `authoritative_net` (or similar) so role vocabulary describes
  content provenance uniformly? Deferred here because nothing is overstated on
  that path and relabeling healthy documented workflows widens the blast radius;
  if wanted, it is a small follow-up once this vocabulary exists.
- **Nice-to-know / deferred**: a viewer banner notice keyed on
  `authoritative_net` ("events are net/inferred only; transient operations not
  captured") — natural follow-up, out of scope per NFR4.

## Risks

- **Unknown downstream consumers of the role string**: anything outside this repo
  matching `role == "authoritative_complete"` to mean "best artifact" will now see
  `authoritative_net` on degraded sessions. This is the *point* of the fix (those
  consumers were being misled), the pointer they should use
  (`authoritative_event_path`) is unchanged, and the vocabulary was never a
  documented closed set. Mitigation: docs update (FR5).
- **Status-set drift**: a future parser status could be added without revisiting
  the role logic. Mitigated by the allow-list direction (FR1) — drift understates
  rather than overstates — plus the unit-matrix test making the allow-list explicit.

## References

- Issue #36 (this spec's source; includes architect-verified root cause)
- `experiments/FINDINGS-round2.md` — F6 (discovery), F7 (related, out of scope)
- `experiments/6_degraded_recovery/degraded.py` — minimal repro
- `src/ai_observe/observe.py` — `merge_snapshot_events`, `build_session_meta`, `prepare_logs`
- `src/ai_observe/backends/strace.py` — failure-status inventory
- `tests/agent_sessions/oracle.py` — `OPEN_BUGS`, `authority_overstated`, `known_bug_gate`
- `tests/agent_sessions/selftest/selftest_degraded.py` — anticipated fixed shape (`authoritative_net`)
- `codev/resources/arch.md` — Artifact contract; `codev/resources/lessons-learned.md` — artifact authority & recovery, backend-scope test pinning

## Consultation Log

### Specify iteration 1 (Gemini / Codex / Claude)

- **Gemini — APPROVE (high confidence)**: no issues. Endorsed the allow-list
  direction, the `partial_direct` pairing with `authoritative_net`, and keeping
  the oracle predicate unbroadened.
- **Claude — APPROVE (high confidence)**: independently re-verified the root
  cause, the `inferred_or_empty_placeholder` unreachability, the allow-list
  membership, and the FR4 selftest impact against source. Non-blocking: suggested
  matching FR3's warning phrasing to `selftest_degraded.py`'s synthetic shape.
- **Codex — COMMENT (high confidence)**: (1) FR3's warning needed a concrete
  observable check in the acceptance criteria; (2) requested explicit wording on
  which tests/docs cover the broad failure-status set vs. remain
  `parser_failure_*`-specific.

**Changes made in response**: FR3 now pins a stable warning substring
(`"snapshot fallback: net events only"`, per Claude's phrasing suggestion) and
requires integration tests to assert it when FR3 is implemented; the Test
scenarios section gained an explicit coverage-split preamble (unit matrix = broad
failure set; integration repros = `parser_failure_partial` by construction;
oracle/docs deliberately `parser_failure_*`-scoped).
