# Review: codex mount-namespace sandbox (`/newroot`) breaks watched-root filtering

## Summary

Under codex's mount-namespace sandbox, one logical file appears in the strace
stream under two spellings (`/tmp/work/.git` and `/newroot/tmp/work/.git`).
The lexical watched-root filter dropped the `/newroot` side, producing dozens
of unpaired `delete` events per codex session (55/57 canonical events were
noise on a trivial task) and silently losing real creates/writes made through
`/newroot`-opened fds.

The fix is a guarded lexical remap in the generic parser core
(`src/ai_observe/trace_parser.py`): `SANDBOX_ROOT_PREFIXES = ("/newroot",)`
plus `_remap_sandbox_paths` at the single event-emission choke point, before
both the scope and artifact filters. Guard rules: in-scope paths are never
rewritten; a prefix is stripped (once, at a component boundary) only when the
stripped path lands inside a watched root; everything else is left for the
scope filter. With no watched roots there is no remap. The live-agent
oracle's `OPEN_BUGS[33]` flag was flipped in the same commit, converting the
known-bug gate into a hard regression assertion.

Delivered in two plan phases: (1) remap + core guard tests + oracle flip;
(2) an 11-row cross-namespace defense matrix, a committed
`newroot_sandbox.strace` end-to-end fixture wired through two test paths, and
doc updates (`docs/observe.md` visibility-boundary note,
`docs/agent-sessions.md` #33 rows marked fixed).

## Spec Compliance

Functional criteria (all MUST):

- [x] 1 Marker pair symmetry — `test_sandbox_prefix_marker_pair_lands_in_canonical_namespace`
- [x] 2 Blast-radius recovery (fd propagation) — `test_sandbox_prefix_fd_propagation_recovers_create_and_write`
- [x] 3 Out-of-scope stays out — `test_sandbox_prefix_outside_watched_roots_still_dropped`; existing scope-filter tests unmodified
- [x] 4 No false remap inside a literal `/newroot` watched root — `test_watched_root_under_literal_newroot_is_not_rewritten`
- [x] 5 Component-boundary safety — `test_sandbox_prefix_requires_component_boundary`
- [x] 6 Rename consistency (mixed spellings canonical; cross-boundary dropped) — `test_rename_fields_remap_independently_across_spellings` + three rename rows in `test_cross_namespace_defense_matrix`
- [x] 7 All three path fields remapped independently at one choke point — same tests; `_parse_line` has exactly one `events.append`
- [x] 8 Oracle flip — `OPEN_BUGS[33].active = False`; `bug33_unpaired_marker_delete()` returns `False`; Spec-38 selftests exercise the hard-assert branch
- [x] 9 No behavior change without watched roots — `test_no_watched_roots_sandbox_spellings_pass_through_unchanged` + the fixture's no-roots registry entry

Non-functional: lexical-only remap (no per-event I/O), no schema change
(`schema_version` stays 2), no new env surface, stdlib-only, remapped events
keep `source: "strace"` / `confidence: "direct"` / original `raw_syscall`, no
codex-specific conditionals. Full suite green with zero skips (255 tests +
56 opt-in selftests); the `chdir`-into-`/newroot` arrival form from
consultation feedback is covered by
`test_chdir_into_sandbox_prefix_remaps_relative_operations`.

## Deviations from Plan

- **Phase 1, additive**: two tests beyond the plan's enumerated list were
  included — annotated `-yy` result-path arrival
  (`test_sandbox_prefix_annotated_result_path_remapped`) and
  remap-before-artifact-filter ordering
  (`test_sandbox_prefix_remap_runs_before_artifact_filter`). Both pin
  behavior the spec asserts in prose.
- **Process, not plan**: phase_1 was implemented twice in parallel due to a
  session collision (see Lessons Learned); the reconciled result matches the
  plan exactly. No scope or design deviation.

## Lessons Learned

### What Went Well

- The spec's behavior matrix (verified against the real parser before
  writing any code) made implementation nearly mechanical — the plan's code
  block landed almost verbatim.
- The Spec-38 rot-proof oracle worked exactly as designed: the fix could not
  land without the one-line flip, and the flip converted marker-noise
  tolerance into a permanent regression gate in the same commit.
- The two-path fixture wiring prescribed after codex's plan review (registry
  entry with no roots + dedicated remap-path test) caught the exact gap it
  was designed for: a registry entry alone would never have exercised the
  remap.

### Challenges Encountered

- **Zombie-session collision**: the pre-resume builder session's wrapper
  auto-respawned after a context reset, leaving two live agents implementing
  phase_1 concurrently in one worktree (files changed and were staged
  mid-edit; the test file briefly held near-duplicate test sets). Resolved
  by: detecting the interference, standing down from driving porch, flagging
  the architect with process evidence, and letting them kill the stale tree.
  Net damage was zero — the tree reconciled into one coherent set — but only
  because both sessions wrote plan-faithful code and one deduplicated.

### What Would Be Done Differently

- On any resumed session, verify sole ownership of the worktree (check for
  sibling builder processes) *before* the first edit, not after noticing
  interference.

### Methodology Improvements

- The builder resume path should kill or fence the prior session's wrapper
  before spawning a fresh one (tooling fix, flagged to the architect; the
  root cause was the wrapper's auto-respawn after a context reset).

## Technical Debt

- `/newroot` is a convention, not a contract. A second sandbox staging
  convention would need a one-line extension of `SANDBOX_ROOT_PREFIXES`
  (deliberately not user-configurable per the spec's open-questions
  decision).
- Strace-only sessions (`AI_OBSERVE_BACKENDS=strace`, empty watched roots)
  keep the two-spelling stream — accepted and documented as a limitation
  (spec decision: remapping without a validation root would be an unguarded
  rewrite).

## Consultation Feedback

### Specify Phase (Round 1)

#### Gemini
- No concerns raised (APPROVE, HIGH). Confirmed the choke point works with
  existing state tracking and `_drop_artifact_event` safely receives
  canonical paths.

#### Codex
- No concerns raised (APPROVE, HIGH).

#### Claude
- **Observation (non-blocking)**: make the `chdir`-into-`/newroot` arrival
  form an explicit unit-test scenario.
  - **Addressed**: added to the spec's verification scenarios and implemented
    as `test_chdir_into_sandbox_prefix_remaps_relative_operations`.
- **Observation (non-blocking)**: confirm `result_path` annotations
  (`= 3</newroot/...>`) are covered by emission-time remap.
  - **N/A**: confirmed covered by design (remap runs over fd-derived paths at
    emission); additionally pinned by
    `test_sandbox_prefix_annotated_result_path_remapped`.

### Plan Phase (Round 1)

#### Gemini
- No concerns raised (APPROVE, HIGH).

#### Codex
- **Concern (COMMENT, HIGH)**: the committed-fixture registry parses with no
  `watched_roots`, so a registry entry alone would never exercise the remap.
  - **Addressed**: plan rewritten to prescribe two-path wiring — the registry
    entry pins the no-roots parse; a dedicated
    `test_newroot_sandbox_fixture_remaps_to_canonical` feeds the same fixture
    through `self.parse(..., watched_roots=["/tmp/work"])`. Implemented as
    prescribed in phase_2.

#### Claude
- Flagged the same fixture-wiring detail (non-blocking); otherwise APPROVE
  (HIGH) with independent re-verification of every line reference.
  - **Addressed**: as above.

### Implement Phase — phase_1 (Round 1)

- **Gemini**: APPROVE (HIGH), no concerns.
- **Codex**: APPROVE (MEDIUM), no concerns.
- **Claude**: APPROVE (HIGH), no concerns; walked all acceptance criteria
  against the diff and confirmed changes were confined to the three
  deliverable files.

### Implement Phase — phase_2 (Round 1)

- **Gemini**: APPROVE (HIGH), no concerns.
- **Codex**: APPROVE (HIGH), no concerns.
- **Claude**: APPROVE (HIGH), no concerns.

## Flaky Tests

No flaky tests encountered.

## Architecture Updates

- **`codev/resources/arch.md` (COLD)**: added a "Sandbox path
  canonicalization" subsection under "Layered observer architecture"
  describing the guarded `/newroot` remap at the emission choke point, its
  guard rules, the no-watched-roots limitation, and `raw_syscall` evidence
  preservation. Subsection sits under an existing top-level section, so the
  hot file's cold-doc map stays accurate unchanged.
- **`codev/resources/arch-critical.md` (HOT)**: no change — the remap is
  subsystem reference detail, not a cross-cutting invariant a future builder
  must know before deciding anything; the existing map topics ("Layered
  observer architecture", "Provenance model") already route consults here.

## Lessons Learned Updates

- **`codev/resources/lessons-learned.md` (COLD)**: added two entries —
  "Canonicalize cross-namespace path spellings at one guarded choke point"
  (namespace-split path identity: single guarded choke point as mechanism,
  pairing symmetry as oracle only, realpath dead end) and "Treat a resumed
  worktree as possibly still owned by a live predecessor" (zombie-session
  collision protocol: stop editing, verify sole ownership, escalate).
- **`codev/resources/lessons-critical.md` (HOT)**: no new lesson (both are
  situational rather than every-project behavior-changers; hot cap space is
  reserved for universal rules). One map line broadened — "Strace
  token/annotation matching" → "Strace parsing, annotations & path identity"
  — so the new namespace lesson is routable without exceeding the 12-topic
  cap.

## Follow-up Items

- Optional live-agent evidence run
  (`python -m tests.agent_sessions --scenarios single_write,ephemeral --tools codex`)
  when a logged-in codex is available — validation evidence, not a CI gate.
- Viewer-level presentation de-noising of the now-paired marker create/delete
  churn (explicit spec non-goal; future feature candidate).
- Tooling: fix the builder wrapper auto-respawn after context reset that
  caused the session collision (architect aware; upstream of this repo).
