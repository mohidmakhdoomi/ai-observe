# Plan: Fix trace_parser's annotated `AT_FDCWD` dirfd resolution (spec 32)

## Metadata
- **ID**: plan-2026-07-19-trace-parser-drops-deletions-v
- **Status**: draft
- **Specification**: [codev/specs/32-trace-parser-drops-deletions-v.md](../specs/32-trace-parser-drops-deletions-v.md)
- **Created**: 2026-07-19

## Executive Summary

Implement the spec's **Approach B** in two dependency-ordered phases:

1. **The atomic behavior change** — teach `_at_path` to recognize a dirfd of
   `AT_FDCWD` with an optional `<path>` annotation (annotation → base, plain or
   empty annotation → tracked cwd, numeric dirfd → unchanged `_dirfd_path`),
   plus the core regression tests, **plus the one-line
   `OPEN_BUGS[32].active = False` flip** in the live-agent oracle. Fix and flip
   are deliberately in the *same phase/commit*: Spec 38's gate is rot-proof in
   both directions, so splitting them leaves a commit where the opt-in
   selftests fail loudly ("fix landed? flip the flag" / "flipped but still
   reproduces").
2. **The blast-radius defense matrix** — enumerated tests across every affected
   `_at_path` call site from the spec's table, edge cases (precedence, empty
   annotation, no-watched-roots pathless-event elimination), and a committed
   end-to-end strace fixture exercising the annotated forms through
   `parse_trace_file`.

The fix is local to `src/ai_observe/trace_parser.py` and introduces a **new**
module-level regex for the `AT_FDCWD<path>` token; `_FD_ANNOT_RE` and its three
callers (`parse_result`, `fd_number`, `fd_path_annotation`) are untouched, per
the spec's rejection of Approach C.

## Success Metrics

Copied from the spec's acceptance criteria, mapped to phases:

- [ ] **S1** annotated `unlinkat(AT_FDCWD</dir>, "f", 0)` emits `delete /dir/f` (Phase 1)
- [ ] **S2** every affected call site resolves the annotated form: unlinkat,
      renameat/renameat2 (both paths), fchmodat, fchownat/utimensat/futimesat,
      mkdirat/mknodat, symlinkat, linkat (Phase 1 fix; Phase 2 enumerated tests)
- [ ] **S3** annotation takes precedence over tracked cwd (Phase 1)
- [ ] **S4** plain `AT_FDCWD` and empty `AT_FDCWD<>` resolve against tracked cwd (Phase 1)
- [ ] **S5** numeric-dirfd behavior byte-for-byte unchanged (Phase 1; existing tests)
- [ ] **S6** `OPEN_BUGS[32].active = False`; `bug32_signature()` returns
      `(False, True)`; oracle selftests pass post-fix branches (Phase 1)
- [ ] **S7** `OPEN_BUGS[33]`/`[36]` untouched, their selftests still pass (Phase 1)
- [ ] **S8** full suite green, zero skips; `python -m tests.agent_sessions --selftest` green (both phases)
- [ ] **S9** stdlib-only; no new dependencies (both phases)
- [ ] **S10** no provenance/schema changes (both phases)

## Phases (Machine Readable)

```json
{
  "phases": [
    {"id": "phase_1", "title": "Annotated-AT_FDCWD resolution fix + core tests + OPEN_BUGS[32] flip"},
    {"id": "phase_2", "title": "Blast-radius defense matrix + end-to-end annotated fixture"}
  ]
}
```

## Phase Breakdown

### Phase 1: Annotated-AT_FDCWD resolution fix + core tests + OPEN_BUGS[32] flip
**Dependencies**: None

#### Objectives
- Stop losing direct events for `*at` syscalls whose dirfd strace annotated as
  `AT_FDCWD</some/dir>` — restoring the product's core promise (deletions by
  claude/agy are reported).
- Convert the live-agent oracle's #32 known-bug gate into a hard regression
  assertion in the same commit.

#### Deliverables
- [ ] `src/ai_observe/trace_parser.py`: `_at_path` handles the annotated form
- [ ] `tests/test_trace_parser.py`: core regression tests (S1, S3, S4, S5)
- [ ] `tests/agent_sessions/oracle.py`: `OPEN_BUGS[32].active = False` (one line)
- [ ] Full suite + opt-in selftests green at the commit boundary

#### Implementation Details

**`src/ai_observe/trace_parser.py`** — add one module-level regex next to
`_FD_ANNOT_RE` (line 47):

```python
_AT_FDCWD_RE = re.compile(r"^AT_FDCWD(?:<(?P<path>[^>]*)>)?$")
```

Rework the base-directory selection in `_at_path` (currently lines 483-485):

```python
base: str | None = self._state(pid).cwd
if dirfd_index is not None and len(args) > dirfd_index:
    dirfd_token = args[dirfd_index].strip()
    at_fdcwd = _AT_FDCWD_RE.match(dirfd_token)
    if at_fdcwd:
        base = at_fdcwd.group("path") or base   # kernel-reported cwd wins; empty → tracked cwd
    else:
        base = self._dirfd_path(pid, dirfd_token)
```

Decisions encoded here (all from the spec):
- Annotation is authoritative over tracked cwd when present and non-empty (S3).
- Plain `AT_FDCWD` and the degenerate `AT_FDCWD<>` keep today's tracked-cwd
  behavior (S4) — `or base` covers both `None` (no annotation group) and `""`.
- Numeric dirfds still go through `_dirfd_path` untouched (S5).
- `_FD_ANNOT_RE`, `parse_result`, `fd_number`, `fd_path_annotation`: no edits.
- `_dirfd_path`: no edits (it never sees `AT_FDCWD*` tokens after this change).

**`tests/test_trace_parser.py`** — core tests (house style: inline strace text
through `self.parse`, ops/path assertions):

- `unlinkat(AT_FDCWD</tmp/work>, "f.txt", 0) = 0` → `["delete"]`,
  path `/tmp/work/f.txt` — the FINDINGS-F2 form (S1).
- Precedence: tracked cwd `/tmp/work` (default `initial_cwd`) but dirfd
  `AT_FDCWD</elsewhere>` → path `/elsewhere/f.txt` (S3).
- `AT_FDCWD<>` (empty annotation) → falls back to tracked cwd (S4).
- Existing plain-`AT_FDCWD` and numeric-dirfd tests continue to pass unmodified
  (S4/S5 — no existing test may be edited to accommodate the fix).
- Absolute path argument ignores the dirfd entirely (guard: annotated dirfd +
  absolute path arg still resolves to the absolute path).

**`tests/agent_sessions/oracle.py`** — flip line 57:

```python
32: KnownBug(32, "annotated AT_FDCWD deletion dropped (claude/agy delete never reported)", active=False),
```

(or the equivalent `active=False` spelling matching local style; #33/#36
entries untouched.)

#### Acceptance Criteria
- [ ] S1, S3, S4, S5, S6, S7 demonstrably pass
- [ ] `cd tests && python -m unittest -v $(ls test_*.py | grep -v '^test_packaging_smoke\.py$' | sed 's/\.py$//')` — green, zero skips
- [ ] `python -m tests.agent_sessions --selftest` (repo root) — green
- [ ] No diff outside the three listed files

#### Test Plan
- **Unit**: the new tests above; the pre-existing `unlinkat(99, "unknown", 0)`
  and `renameat(99, "old", AT_FDCWD, "new")` expectations must pass unchanged.
- **Oracle probe**: `bug32_signature()` returns `(dropped=False, plain_captured=True)`;
  `selftest_oracle.test_bug32_reproduction_matches_registry` and
  `test_deletion_gate_tracks_registry` exercise their post-fix branches.
- **Manual**: drive `TraceParser` with the spec's verified-behavior matrix and
  confirm the two ❌ rows flip to ✅ (repro script from spec drafting).

#### Rollback Strategy
Single revert of the phase commit restores prior behavior *and* re-arms the
known-bug gate (fix and flip travel together — reverting one without the other
is what the rot-proof gate exists to catch).

#### Risks
- **Risk**: regression in numeric-dirfd resolution from restructuring `_at_path`.
  - **Mitigation**: new regex is anchored to the literal `AT_FDCWD` prefix; the
    numeric path routes through the untouched `_dirfd_path`; existing tests pin it.
- **Risk**: another in-flight branch edits `oracle.py` (e.g. a #33 fix).
  - **Mitigation**: the flip is one line in a dict literal; merge conflict is
    trivial and loud, not silent.

---

### Phase 2: Blast-radius defense matrix + end-to-end annotated fixture
**Dependencies**: Phase 1

#### Objectives
- Pin the fix across the *entire* affected call-site table from the spec so a
  future refactor of any single dispatch arm cannot silently re-introduce the
  drop for that syscall family.
- Prove the end-to-end file path (`parse_trace_file` over a committed fixture),
  not just in-memory line feeding.

#### Deliverables
- [ ] `tests/test_trace_parser.py`: enumerated annotated-dirfd matrix (S2) +
      no-watched-roots pathless-event assertions
- [ ] `tests/fixtures/strace/annotated_at_fdcwd.strace`: committed fixture with
      annotated create/rename/delete forms
- [ ] Fixture registered in `test_committed_fixture_files_parse`

#### Implementation Details

**Matrix test** (one test method, `subTest` per syscall, mirroring the spec's
blast-radius table):

| line fed (all with dirfd `AT_FDCWD</tmp/work>`) | expected op | expected path(s) |
|---|---|---|
| `unlinkat(…, "f", 0)` | delete | `/tmp/work/f` |
| `renameat(…, "a", …, "b")` | rename | old `/tmp/work/a`, new `/tmp/work/b` |
| `renameat2(…, "a", …, "b", 0)` | rename | old `/tmp/work/a`, new `/tmp/work/b` |
| `fchmodat(…, "f", 0600)` | chmod | `/tmp/work/f` |
| `fchownat(…, "f", 1000, 1000, 0)` | metadata | `/tmp/work/f` |
| `utimensat(…, "f", NULL, 0)` | metadata | `/tmp/work/f` |
| `futimesat(…, "f", NULL)` | metadata | `/tmp/work/f` |
| `mkdirat(…, "d", 0755)` | create | `/tmp/work/d` |
| `mknodat(…, "n", S_IFREG\|0644, 0)` | create | `/tmp/work/n` |
| `symlinkat("t", …, "l")` | create | `/tmp/work/l` |
| `linkat(…, "src", …, "dst", 0)` | create | `/tmp/work/dst` |
| `openat(…, "n", O_WRONLY\|O_CREAT\|O_EXCL, 0600) = 3` (**no** result annotation) | create | `/tmp/work/n` — the robustness case the return-value rescue can't cover |

Mixed-form rename guard: `renameat2(AT_FDCWD</a>, "x", AT_FDCWD, "y", 0)` with
tracked cwd `/b` → old `/a/x`, new `/b/y` (each side resolves independently).

**Pathless-event elimination**: with `watched_roots=()` (no roots), the
annotated `unlinkat`/`renameat2` lines must now produce fully-pathed events —
asserting the spec's "no pathless junk" clause, and locking the no-roots
configuration too.

**Fixture** `tests/fixtures/strace/annotated_at_fdcwd.strace`: first line must
contain the string `strace` (existing fixture-header assertion), then an
annotated create → modify → delete sequence for one file under `/tmp/work`,
using the exact real-session forms (`openat` with both dirfd and return
annotations, `write` via annotated fd, `unlinkat(AT_FDCWD</tmp/work>, …)`).
Registered in `test_committed_fixture_files_parse` with expected ops
`["create", "modify", "delete"]`.

#### Acceptance Criteria
- [ ] S2 fully enumerated and green; S8 holds (full suite + selftests, zero skips)
- [ ] Fixture parses via the standard `parse_trace_file` path with JSONL output
- [ ] No product-code changes in this phase (tests + fixture only) — if the
      matrix exposes a gap, the gap is fixed in a Phase-1-style code commit
      *first*, then this phase's tests land (plan deviation documented)

#### Test Plan
- **Unit/Integration**: as above — the matrix is integration-grade (real parser,
  real dispatch, no mocks; house style uses no mocking anywhere in this file).
- **Manual**: none needed beyond the suite.

#### Rollback Strategy
Tests-and-fixture-only commit; revert is a pure coverage reduction with no
behavior change.

#### Risks
- **Risk**: fixture drifts from real strace output shapes.
  - **Mitigation**: forms are copied from the spec's verified matrix and the
    oracle probe (both derived from real `-yy` sessions in FINDINGS F2).

## Dependency Map
```
Phase 1 ──→ Phase 2
```

## Resource Requirements
None beyond the existing dev environment (Python stdlib, unittest). No
infrastructure, configuration, or monitoring changes.

## Integration Points
- **Live-agent suite (`tests/agent_sessions/`)**: Phase 1's flip is the
  designed integration (Spec 38 flip-home). No other systems touched.
- **Snapshot/viewer layers**: consume the recovered events through existing
  pipelines; no changes needed (spec non-goal).

## Risk Analysis

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Numeric-dirfd regression via `_at_path` restructure | L | H | anchored regex; untouched `_dirfd_path`; existing pinned tests |
| Fix lands without flip (or vice versa) | L | M | same-commit rule in Phase 1; rot-proof gate fails loudly either way |
| Annotated forms in fixture don't match real strace | L | M | forms taken from verified real-session repro (FINDINGS F2) |

## Validation Checkpoints
1. **After Phase 1**: full suite + `--selftest` green; `bug32_signature() == (False, True)`.
2. **After Phase 2**: blast-radius matrix green; fixture in committed-fixtures test.
3. **Before PR**: spec acceptance S1–S10 checklist walked end-to-end; optional
   live evidence (`python -m tests.agent_sessions --scenarios ephemeral --tools
   claude,agy`) if tools are authenticated in the environment — evidence, not a gate.

## Documentation Updates Required
- [ ] None in product docs (parser-internal fix; no user-facing surface change).
- [ ] Review-phase routing per protocol: candidate lessons-learned entry
      (annotated-token coverage hole) — decided during Review, not here.

## Post-Implementation Tasks
- [ ] Review phase: `codev/reviews/32-trace-parser-drops-deletions-v.md`
- [ ] Optional live-agent evidence run (see checkpoint 3)

## Expert Review

### Plan, iteration 1 (gemini / codex / claude) — unanimous APPROVE, high confidence

- **codex**: APPROVE, no issues. Two minor suggestions, both already encoded in
  the plan: keep the Phase 2 matrix granular via `subTest` (specified), and keep
  `test_committed_fixture_files_parse` easy to extend (the fixture registry is a
  flat name→ops dict; one entry added).
- **gemini**: APPROVE, no issues. Independently confirmed the `or base` fallback
  covers plain/empty-annotation cases and that the same-commit fix+flip rule is
  the correct navigation of the rot-proof gate.
- **claude**: APPROVE, no issues. Re-verified every line reference and all eight
  `_at_path` call-site rows (including `symlinkat`/`linkat` argument ordering)
  against source; confirmed no existing test requires modification. Two
  non-blocking notes for the builder, acknowledged: (1) the Phase 2 `openat`
  robustness row must craft a result *without* a path annotation (`= 3`, no
  `</path>`) to bypass the rescue; (2) after the fix, `_dirfd_path` never sees
  `AT_FDCWD*` tokens — that invariant is why it needs no edit.

**Plan Adjustments**: none required by any reviewer.

## Approval
- [ ] Expert AI consultation complete (porch 3-way)
- [ ] Human `plan-approval` gate

## Change Log
| Date | Change | Reason | Author |
|------|--------|--------|--------|
| 2026-07-19 | Initial plan | — | builder spir-32 |

## Notes
- Line numbers referenced (47, 483-485, oracle.py:57) verified on this branch at
  plan time; re-verify at implementation if other work lands first.
- Porch requires ≥2 phases; the natural atomic unit (fix+tests+flip) is Phase 1,
  and the exhaustive defense matrix is genuinely separable, independently
  valuable work — not padding.
