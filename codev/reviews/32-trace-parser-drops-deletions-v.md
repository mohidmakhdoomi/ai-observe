# Review: trace_parser drops deletions via annotated AT_FDCWD dirfd (spec 32)

## Summary

Fixed the silent loss of `*at`-family syscall events whose dirfd strace annotated
as `AT_FDCWD</some/dir>` — the form every real claude/agy session produces under
ai-observe's own `-yy` invocation. The root cause was a literal string comparison
in `_at_path` (`!= "AT_FDCWD"`) that misclassified the annotated token as a real
fd, sent it down `_dirfd_path` (numeric-only regex → `None` base), and dropped
the event. The observable symptom: files deleted by claude/agy were shown as
still existing — silent data loss in the product's core promise.

The fix (spec Approach B): a new anchored module-level regex `_AT_FDCWD_RE`
recognizes `AT_FDCWD` with an optional `<path>` annotation in `_at_path`. A
non-empty annotation becomes the base directory (it is the kernel-reported cwd
at syscall time, more authoritative than tracked cwd); plain `AT_FDCWD` and the
degenerate `AT_FDCWD<>` keep tracked-cwd behavior; anything else routes through
the untouched `_dirfd_path`. In the same commit, the live-agent oracle's
`OPEN_BUGS[32]` gate flipped to `active=False`, converting Spec 38's known-bug
annotation into a hard regression assertion.

Total product diff: 12 lines in `src/ai_observe/trace_parser.py`, 1 line in
`tests/agent_sessions/oracle.py`. Defense: 7 new test methods (including a
12-row blast-radius matrix) and a committed end-to-end fixture.

## Spec Compliance

All ten acceptance criteria met, none with deviation:

- [x] **S1** — `unlinkat(AT_FDCWD</dir>, "f", 0)` emits `delete` at `/dir/f`
  under a watched root (`test_unlinkat_annotated_at_fdcwd_emits_delete`).
- [x] **S2** — every affected call site resolves the annotated form: 12-row
  `subTest` matrix (`test_annotated_at_fdcwd_blast_radius_matrix`) covering
  `unlinkat`, `renameat`, `renameat2` (old+new sides), `fchmodat`, `fchownat`,
  `utimensat`, `futimesat`, `mkdirat`, `mknodat`, `symlinkat`, `linkat`, and the
  `openat` robustness row (result `= 3` with no path annotation, so the dirfd is
  the only resolution source). All green on first run — no product-code gap.
- [x] **S3** — annotation beats tracked cwd
  (`test_annotated_at_fdcwd_wins_over_tracked_cwd`; plus the mixed-form
  `renameat2` guard proving each side resolves independently).
- [x] **S4** — plain `AT_FDCWD` unchanged (existing tests, unmodified);
  `AT_FDCWD<>` falls back to tracked cwd
  (`test_empty_at_fdcwd_annotation_falls_back_to_tracked_cwd`).
- [x] **S5** — numeric-dirfd behavior unchanged: existing annotated-fd,
  known-fd, and unknown-fd (`unlinkat(99, …)` → no path) tests pass unmodified.
- [x] **S6** — `OPEN_BUGS[32].active=False` in the same commit as the fix;
  `bug32_signature()` returns `(dropped=False, plain_captured=True)`; both
  registry selftests exercise their post-fix branches.
- [x] **S7** — `OPEN_BUGS[33]`/`[36]` untouched (oracle diff is exactly one
  line); their selftests green.
- [x] **S8** — full suite green, zero skips (222 tests); opt-in selftests green
  (56 tests); porch `tests` check (unittest discover) green at both phase
  boundaries.
- [x] **S9** — `trace_parser.py` remains stdlib-only; no new imports.
- [x] **S10** — no provenance/schema changes; recovered events verified to carry
  `source: "strace"`, `confidence: "direct"` (manual matrix run during phase 1).

Verification scenarios: the spec's verified-behavior matrix was re-driven
manually after the fix — the two previously-dropped rows (annotated `unlinkat`)
now emit correct deletes; the no-watched-roots configuration produces fully
pathed events (`test_no_watched_roots_annotated_events_are_fully_pathed`); the
end-to-end file path is pinned by the committed fixture
`tests/fixtures/strace/annotated_at_fdcwd.strace` (create → modify → delete)
registered in `test_committed_fixture_files_parse`.

## Deviations from Plan

None. Both phases landed exactly as planned; the phase-2 acceptance criterion
"no product-code changes unless the matrix exposes a gap" held (no gap).

## Lessons Learned

### What Went Well

- **Spec precision paid off**: the plan embedded the exact regex, the exact
  `_at_path` rework, and the exact test rows; implementation was mechanical and
  every consultation round (4 rounds × 3 models) returned unanimous APPROVE
  with zero issues on the first iteration.
- **The Spec 38 oracle integration worked as designed**: the known-bug gate had
  a purpose-built flip-home (`OPEN_BUGS[32].active=False`), and its rot-proof
  selftests validated the fix from the outside (`bug32_signature()` flipping to
  `(False, True)`) without any live-agent run.
- **Fix+flip atomicity**: keeping the parser fix and the gate flip in one commit
  means a single revert restores both the old behavior and the re-armed gate.

### Challenges Encountered

- None of substance. The one environmental wrinkle: `python -m
  tests.agent_sessions --selftest` must run from the repo root (module
  resolution), which briefly looked like a failure when run from `tests/`.

### What Would Be Done Differently

- The coverage hole existed because every fixture and inline test used the
  plain `AT_FDCWD` form while the product's own strace invocation (`-yy`)
  guarantees the annotated form in real sessions. Test inputs should be sourced
  from real tool output early — the live-agent harness (#31) is what finally
  surfaced this.

### Methodology Improvements

- None proposed for SPIR itself. The porch strict-mode loop (build → 3-way
  verify → commit sweep → advance) fit this two-phase bugfix cleanly.

## Technical Debt

- None added. Pre-existing (out of scope, documented in spec Open Questions):
  strace annotations containing `>` truncate at the first `>` (`[^>]*`) — this
  pre-dates the fix and applies equally to numeric-fd annotations.

## Consultation Feedback

All four consultation rounds were unanimous APPROVE with `KEY_ISSUES: None`.
Non-blocking notes and how they were handled:

### Specify Phase (Round 1)

#### Gemini
- APPROVE, high confidence. One suspected markdown typo in Success Criterion 3.
  - **Rebutted**: `AT_FDCWD</b>` is the intended literal form, not a typo.

#### Codex
- APPROVE, high confidence. No concerns.

#### Claude
- APPROVE, high confidence. Two non-blocking notes:
  - Relative annotation paths (`AT_FDCWD<rel/dir>`) don't occur in practice.
    - **N/A**: join-based resolution is correct-by-construction even if they did.
  - Line-number references may drift before implementation.
    - **Addressed**: re-verified all references at implementation time (none drifted).

### Plan Phase (Round 1)

#### Gemini
- APPROVE, high confidence. Independently confirmed the `or base` fallback and
  the same-commit fix+flip rule. No concerns.

#### Codex
- APPROVE, high confidence. Two suggestions already encoded in the plan
  (granular `subTest` matrix; flat fixture registry).
  - **N/A**: no change required.

#### Claude
- APPROVE, high confidence. Two builder notes:
  - The `openat` robustness row must use a result *without* a path annotation.
    - **Addressed**: matrix row uses `= 3` with no annotation.
  - Post-fix, `_dirfd_path` never sees `AT_FDCWD*` tokens.
    - **Addressed**: `_dirfd_path` left untouched, as planned.

### Implement Phase — phase_1 (Round 1)

#### Gemini / Codex / Claude
- All APPROVE, high confidence, no concerns. Claude's reviewer independently
  verified the three-case `or base` semantics (plain / empty / annotated) and
  that `_FD_ANNOT_RE` and its three callers are untouched.

### Implement Phase — phase_2 (Round 1)

#### Gemini / Codex / Claude
- All APPROVE (Gemini/Claude high confidence, Codex medium), no concerns.
  Claude's reviewer confirmed all 12 matrix rows, the mixed-form guard, the
  no-watched-roots test, and that no product code changed in the phase.

### Review Phase — PR (Round 1)

#### Gemini
- APPROVE, high confidence. No concerns.

#### Codex
- REQUEST_CHANGES, high confidence. Two issues:
  - 12 untracked `32-*-iter1-*.txt` consultation artifacts left the branch
    not PR-clean.
    - **Addressed**: repo convention (project 1 on `main`) is that consultation
      artifacts are committed; all project-32 consult outputs staged and
      committed.
  - Builder commits use `[Spec 32] ...` without a `[Phase: ...]` suffix.
    - **Rebutted**: under porch strict mode the phase commits are porch's
      `chore(porch)` sweeps; the plain `[Spec ####] <stage>: <description>`
      form is the documented format for document/stage commits (spec, plan,
      review, thread), which is all the builder commits here are.
  - (Codex also noted it could not reproduce full-suite/selftest runs in its
    sandbox — environment-only, "not evidence against this patch".)

#### Claude
- APPROVE, high confidence. No concerns. (First run aborted on an external
  session limit and was re-run — noted for completeness, not a review finding.)

## Architecture Updates

No architecture updates needed. The fix is parser-internal: it changes no
observer-layer promise, no backend selection or ordering, no artifact contract,
no provenance fields (S10 explicitly pins them unchanged), and no packaging/CI
surface. `arch.md`'s existing sections already describe the system shape this
fix restores rather than alters.

## Lessons Learned Updates

Routed one durable lesson to the **cold** tier (`codev/resources/lessons-learned.md`,
new section "Match strace tokens with annotations in mind"): literal comparisons
against strace tokens must account for `-y`/`-yy` path annotations (`TOKEN<path>`),
and parser test inputs must include the annotated forms real sessions produce —
the plain-form-only coverage hole is exactly where this bug lived. Cold tier
because it is a trace-parsing-specific recipe, not a cross-cutting behavior
changer; the hot file's map gained a matching topic line (within its ≤12 cap).

## Flaky Tests

No flaky tests encountered.

## Follow-up Items

- Optional live-agent evidence (not a gate): `python -m tests.agent_sessions
  --scenarios ephemeral --tools claude,agy` on a machine with authenticated
  tools — the ephemeral scenario's canonical view should flip from
  `known-bug:#32` to a hard pass. Deterministic equivalents already gate in CI.
- Sibling bugs #33 (codex `/newroot` marker noise) and #36 (sidecar authority
  overstatement) remain open with their own `OPEN_BUGS` gates.
