# Builder thread — spir-32 (issue #32: annotated AT_FDCWD dirfd drops events)

## 2026-07-19 — Specify phase

Spawned in strict mode; porch phase = specify. No pre-existing spec, but issue
#32 arrived fully root-caused (live-agent harness FINDINGS F2), so no clarifying
questions were needed — drafted directly from the issue + code reading.

Investigation notes beyond the issue text (all re-verified on this branch by
driving `TraceParser` directly):

- ai-observe's own strace backend hardcodes `-yy` (`backends/strace.py:61`), so
  the annotated `AT_FDCWD</dir>` form is what every real session produces on
  modern strace. The bug is triggered by our own default configuration.
- Blast radius is wider than `unlinkat`: every `_at_path` call site with a dirfd
  drops the event when the syscall returns `0` (renameat/renameat2, fchmodat,
  fchownat/utimensat/futimesat, mkdirat/mknodat, symlinkat, linkat).
  `openat`/`openat2` are rescued in practice by the return-value annotation
  (`result_path`), which is why creates/modifies never looked broken.
- Without watched roots the parser emits *pathless* events (path: null) instead
  of dropping — junk either way; the spec covers both configurations.
- Spec 38's oracle already has a rot-proof gate armed for this fix:
  `OPEN_BUGS[32]` + deterministic probe `bug32_signature()`. The flip to
  `active=False` MUST ship in the same PR or the opt-in selftests fail loudly
  ("fix landed? flip the flag"). Spec records this as a hard requirement.

Spec recommends Approach B: use the `AT_FDCWD<annotation>` as the resolution
base (kernel-reported, beats tracked cwd), plain/empty-annotation falls back to
tracked cwd, numeric-dirfd path untouched. `_FD_ANNOT_RE` and its three callers
deliberately not widened (parse_result must stay numeric — regression risk for
zero user value).

Spec drafted at `codev/specs/32-trace-parser-drops-deletions-v.md`; signaling
`porch done 32` for the 3-way verify cycle.

### Consultation result (specify, iter 1)

Unanimous APPROVE (gemini/codex/claude, all high confidence, zero issues).
Claude's reviewer independently re-verified every code claim against source.
Consultation log recorded in the spec. Now at the spec-approval gate — notified
architect, waiting for human approval.

## Plan phase

spec-approval gate approved by architect (verified root-cause claims against
source themselves). Plan drafted: two phases — (1) the atomic unit: `_at_path`
fix via a new `_AT_FDCWD_RE` regex + core regression tests + the
`OPEN_BUGS[32].active=False` flip (same commit, because Spec 38's gate is
rot-proof in BOTH directions — splitting fix and flip leaves a loudly-failing
selftest commit either way); (2) the blast-radius defense matrix across all
affected call sites + a committed end-to-end annotated strace fixture.

### Consultation result (plan, iter 1)

Unanimous APPROVE (gemini/codex/claude, high confidence, zero issues). Claude's
reviewer re-verified all eight call-site rows and arg orderings against source.
Two builder notes captured in the plan's Expert Review (openat robustness row
needs a result WITHOUT path annotation; _dirfd_path never sees AT_FDCWD tokens
post-fix). At plan-approval gate — notified architect, waiting.

## Implement phase — phase_1 (resumed session, fresh context)

plan-approval gate approved. Implemented phase_1 exactly per plan:

- `trace_parser.py`: added `_AT_FDCWD_RE` next to `_FD_ANNOT_RE`; `_at_path`
  now matches the dirfd token against it — non-empty annotation becomes the
  base (kernel-reported cwd wins), plain `AT_FDCWD`/`AT_FDCWD<>` keep tracked
  cwd, everything else routes through the untouched `_dirfd_path`.
- `test_trace_parser.py`: 4 new tests — S1 (annotated unlinkat under watched
  root), S3 (annotation beats tracked cwd), S4 (empty annotation falls back),
  and the absolute-path-ignores-dirfd guard. No existing test edited.
- `oracle.py`: `OPEN_BUGS[32].active=False` (same commit — fix+flip atomic).

Verification: full suite 219 tests green zero skips; `--selftest` 56 green;
manual matrix — annotated/plain/unlink all emit `delete /tmp/work/f.txt`
(source=strace, confidence=direct); `bug32_signature()` → (False, True).
Diff confined to the three planned files. Signaling porch done.

## Implement phase — phase_2

phase_1 consultation was unanimous APPROVE (3× high confidence, zero issues);
porch committed and advanced. phase_2 delivered as planned, tests+fixture only:

- Blast-radius matrix (one test, subTest per row): all 12 spec call-site rows
  green on first run — including the openat robustness row (result `= 3` with
  no path annotation, so the dirfd is the only resolution source).
- Mixed-form rename guard: `renameat2(AT_FDCWD</a>, "x", AT_FDCWD, "y", 0)`
  with tracked cwd /b → old /a/x, new /b/y (sides resolve independently).
- No-watched-roots config: annotated renameat2+unlinkat now fully pathed.
- Committed fixture `annotated_at_fdcwd.strace` (create→modify→delete via
  annotated forms) registered in test_committed_fixture_files_parse.

No product-code gap surfaced (acceptance criterion: no non-test changes).
Full suite 222 green zero skips; --selftest 56 green. Signaling porch done.

## Review phase

phase_2 consultation unanimous APPROVE (gemini/claude high, codex medium
confidence; zero issues across the board). Porch advanced to review.

Review doc written with full S1–S10 compliance walk (all met, no deviations).
Governance routing: one cold-tier lesson added to lessons-learned.md ("Match
strace tokens with annotations in mind") with a matching map line in
lessons-critical.md (12/12 topics, 27 lines — within cap); no arch updates
(parser-internal fix, no system-shape change). Plan status → completed.
Opening PR next (Fixes #32), then porch done → PR consultation.

### PR consultation (round 1)

PR #40 opened (Fixes #32) and recorded in pr_history. Verdicts: gemini APPROVE,
claude APPROVE (after a session-limit re-run), codex REQUEST_CHANGES on branch
hygiene only — untracked consult artifacts. Repo convention (project 1 on main)
is to commit them, so all 15 project-32 consult outputs staged+committed;
codex's [Phase:] commit-format note rebutted (porch owns phase commits in
strict mode). Review doc's consultation log updated. Code itself: no findings.
