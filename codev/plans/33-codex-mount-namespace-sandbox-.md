# Plan: Guarded sandbox-prefix remap for mount-namespace paths (spec 33)

## Metadata
- **ID**: plan-2026-07-19-codex-mount-namespace-sandbox-
- **Status**: draft
- **Specification**: [codev/specs/33-codex-mount-namespace-sandbox-.md](../specs/33-codex-mount-namespace-sandbox-.md)
- **Created**: 2026-07-19

## Executive Summary

Implement the spec's **Approach B** (guarded lexical sandbox-prefix remap at
the event-emission choke point) in two dependency-ordered phases:

1. **The atomic behavior change** — a `SANDBOX_ROOT_PREFIXES = ("/newroot",)`
   constant plus a remap step in `TraceParser._parse_line` between event
   construction and the scope/artifact filters, applying the spec's three-rule
   guard to `path` / `old_path` / `new_path` independently; core regression
   tests for the guard rules and arrival forms; **plus the one-line
   `OPEN_BUGS[33].active = False` flip** in the live-agent oracle. Fix and flip
   are deliberately in the *same phase/commit*: Spec 38's gate is rot-proof in
   both directions, so splitting them leaves a commit where
   `test_bug33_reproduction_matches_registry` fails loudly ("fix landed? flip
   the flag").
2. **The cross-namespace defense matrix + end-to-end fixture + docs** —
   enumerated remap coverage across the event-op families and arrival forms
   (syscall args, fd-table propagation, `chdir`-derived cwd, `-yy`
   annotations), a committed strace fixture replaying the codex sandbox
   session shape through `parse_trace_file`, and the small user-facing doc
   updates (`docs/observe.md` visibility-boundary note;
   `docs/agent-sessions.md` #33 status).

The fix is local to `src/ai_observe/trace_parser.py`. No schema change, no new
env vars, no changes to `_drop_out_of_scope_event` / `_path_within_watched_roots`
themselves (they receive already-remapped paths), and no state-table changes
(`state.cwd` / `state.fds` keep the raw kernel-view spellings, per spec).

## Success Metrics

Mapped from the spec's acceptance criteria:

- [ ] **S1** marker pair symmetry: `/newroot<root>` mkdir + canonical rmdir → paired `create`+`delete`, both canonical (Phase 1)
- [ ] **S2** blast-radius recovery: `openat` via `/newroot` spelling + `write` through that fd → `create`+`modify`, both canonical (Phase 1)
- [ ] **S3** out-of-scope stays out: `/newroot/<elsewhere>` and plain out-of-scope paths still dropped (Phase 1)
- [ ] **S4** literal `/newroot`-rooted watched root never remapped (guard rule 1) (Phase 1)
- [ ] **S5** component-boundary safety: `/newrootfoo/...` never remapped (Phase 1)
- [ ] **S6** rename consistency: mixed-spelling renames emit both fields canonical; genuine cross-boundary renames still dropped (Phase 1 fix; Phase 2 matrix)
- [ ] **S7** all three path fields remapped independently at one choke point before both filters (Phase 1)
- [ ] **S8** `OPEN_BUGS[33].active = False`; `bug33_unpaired_marker_delete()` returns `False`; oracle selftests pass post-fix branches; `OPEN_BUGS[36]` untouched (Phase 1)
- [ ] **S9** `watched_roots=()` output byte-identical to today (Phase 1)
- [ ] **S10** full suite green, zero skips; `python -m tests.agent_sessions --selftest` green (both phases)
- [ ] **S11** stdlib-only; lexical-only remap (no per-event filesystem I/O); no schema/env/provenance changes; no codex-specific conditionals (both phases)
- [ ] **S12** end-to-end proof through `parse_trace_file` over a committed fixture; user docs updated (Phase 2)

## Phases (Machine Readable)

```json
{
  "phases": [
    {"id": "phase_1", "title": "Sandbox-prefix remap + core guard tests + OPEN_BUGS[33] flip"},
    {"id": "phase_2", "title": "Cross-namespace defense matrix + end-to-end fixture + docs"}
  ]
}
```

## Phase Breakdown

### Phase 1: Sandbox-prefix remap + core guard tests + OPEN_BUGS[33] flip
**Dependencies**: None

#### Objectives
- Stop dropping direct events whose paths arrive in the sandbox-staging
  spelling — restoring pairing symmetry (the unpaired-delete storm) and
  recovering real creates/writes lost through `/newroot`-opened fds.
- Convert the live-agent oracle's #33 known-bug gate into a hard regression
  assertion in the same commit.

#### Deliverables
- [ ] `src/ai_observe/trace_parser.py`: `SANDBOX_ROOT_PREFIXES` constant + remap step at the emission choke point
- [ ] `tests/test_trace_parser.py`: core regression tests (S1–S5, S7, S9 + `chdir` arrival form)
- [ ] `tests/agent_sessions/oracle.py`: `OPEN_BUGS[33].active = False` (one line)
- [ ] Full suite + opt-in selftests green at the commit boundary

#### Implementation Details

**`src/ai_observe/trace_parser.py`** — add a module-level constant near the
regexes (after line 48):

```python
SANDBOX_ROOT_PREFIXES: tuple[str, ...] = ("/newroot",)
```

Insert the remap step in `_parse_line` (currently lines 181–188) between event
construction and the drop filters:

```python
event = self._event_for(pid, ts, name, args, result_value, result_text, body, result_path)
if event is None:
    return
self._remap_sandbox_paths(event)          # ← new: before BOTH drop filters
if self._drop_out_of_scope_event(event):
    return
if self._drop_artifact_event(event):
    return
```

Two new methods on `TraceParser`:

```python
def _remap_sandbox_paths(self, event: dict[str, Any]) -> None:
    if not self.watched_roots:
        return
    for key in ("path", "old_path", "new_path"):
        value = event.get(key)
        if value:
            event[key] = self._remap_sandbox_path(value)

def _remap_sandbox_path(self, path: str) -> str:
    if self._path_within_watched_roots(Path(path)):
        return path                      # guard rule 1: in-scope stays untouched
    for prefix in SANDBOX_ROOT_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            stripped = path[len(prefix):] or "/"
            if self._path_within_watched_roots(Path(stripped)):
                return stripped          # guard rule 2: strip only into a watched root
    return path                          # guard rule 3: leave for the scope filter
```

Decisions encoded here (all from the spec):
- Empty `watched_roots` → no remap attempted, output byte-identical (S9); this
  also makes both guard rules inert exactly as the spec's criterion 9 requires.
- Guard rule 1 before any strip protects a literal `/newroot`-rooted watched
  root (S4).
- `path == prefix or path.startswith(prefix + "/")` gives component-boundary
  matching (S5); the separator is a literal `"/"`, not `os.sep` — strace text
  is POSIX regardless of host, and every path here has passed
  `normalize_abs_path`.
- `path[len(prefix):] or "/"` handles the degenerate `path == "/newroot"`
  case (stripped form `/`, in-scope only if `/` is watched).
- Single strip, no recursion (`/newroot/newroot/...` strips once, then guard
  rule 2 decides); each of the three fields is remapped independently (S6, S7).
- `state.cwd` / `state.fds` untouched — remap at emission covers fd-table and
  cwd-derived arrivals because event paths are drawn from those tables at
  emission time (verified in the spec's behavior matrix).
- `_drop_out_of_scope_event`, `_path_within_watched_roots`,
  `_drop_artifact_event`, `normalize_abs_path`: no edits.

**`tests/test_trace_parser.py`** — core tests (house style: inline strace text
through `self.parse`, ops/path assertions; annotated `-yy` forms where real
sessions produce them):

- **S1**: `mkdir("/newroot/tmp/work/.git", 0755) = 0` +
  `rmdir("/tmp/work/.git") = 0` with `watched_roots=["/tmp/work"]` →
  `[("create", "/tmp/work/.git"), ("delete", "/tmp/work/.git")]` — the exact
  oracle-probe shape.
- **S2**: `openat(AT_FDCWD, "/newroot/tmp/work/f.txt", O_WRONLY|O_CREAT|O_EXCL, 0600) = 3`
  + `write(3, "x", 1) = 1` → `create` + `modify`, both `/tmp/work/f.txt`
  (fd-table propagation through the remap).
- **S3**: `mkdir("/newroot/etc/x", 0755) = 0` → no events (stripped path
  outside roots); existing
  `test_watched_roots_drop_outside_and_cross_boundary_direct_events` passes
  unmodified.
- **S4**: `watched_roots=["/newroot/data"]`, `creat("/newroot/data/f", 0600)`
  → `create /newroot/data/f` (path unchanged — guard rule 1).
- **S5**: `creat("/newrootfoo/tmp/work/f", 0600)` with root `/tmp/work` → no
  events (no component-boundary match, scope filter drops).
- **chdir arrival form** (consultation feedback): `chdir("/newroot/tmp/work") = 0`
  then `mkdir("d", 0755) = 0` → `create /tmp/work/d` (tracked cwd keeps the
  raw spelling; the resolved event path is remapped at emission).
- **S9**: the S1 line pair with `watched_roots=()` → unchanged current output
  (`create /newroot/tmp/work/.git` + `delete /tmp/work/.git`), pinning the
  no-roots configuration byte-for-byte.
- No existing test may be edited to accommodate the fix.

**`tests/agent_sessions/oracle.py`** — flip line 58:

```python
33: KnownBug(33, "codex /newroot mount-namespace marker-noise: unpaired delete events", active=False),
```

(#36 entry untouched; #32 already `active=False`.)

#### Acceptance Criteria
- [ ] S1–S5, S7, S8, S9 demonstrably pass
- [ ] `cd tests && python -m unittest -v $(ls test_*.py | grep -v '^test_packaging_smoke\.py$' | sed 's/\.py$//')` — green, zero skips
- [ ] `python -m tests.agent_sessions --selftest` (repo root) — green
- [ ] No diff outside the three listed files

#### Test Plan
- **Unit**: the new tests above; all existing watched-roots and annotated-dirfd
  tests pass unchanged.
- **Oracle probe**: `bug33_unpaired_marker_delete()` returns `False`;
  `selftest_oracle.test_bug33_reproduction_matches_registry` and
  `test_marker_noise_gate_tracks_registry` exercise their post-fix branches.
- **Manual**: re-drive the spec's verified-behavior matrix (the three-row
  table) and confirm the two broken rows now emit paired canonical events.

#### Rollback Strategy
Single revert of the phase commit restores prior behavior *and* re-arms the
known-bug gate (fix and flip travel together — reverting one without the other
is what the rot-proof gate exists to catch).

#### Risks
- **Risk**: remap accidentally rewrites paths that should stay raw (scope
  creep into legitimate `/newroot`-named host dirs).
  - **Mitigation**: guard rule 1 (in-scope wins) + rule 2 (strip only into a
    watched root) bound every rewrite; S4/S5 tests pin both guards.
- **Risk**: choke-point insertion misses an emission path.
  - **Mitigation**: `_parse_line` has exactly one `self.events.append` (line
    188); the remap sits immediately before the only two drop filters.
- **Risk**: another in-flight branch edits `oracle.py` (e.g. a #36 fix).
  - **Mitigation**: the flip is one line in a dict literal; a merge conflict is
    trivial and loud, not silent.

---

### Phase 2: Cross-namespace defense matrix + end-to-end fixture + docs
**Dependencies**: Phase 1

#### Objectives
- Pin the remap across event-op families and arrival forms so a future
  refactor of any dispatch arm (or of the remap guard) cannot silently
  re-introduce a namespace split for that family.
- Prove the end-to-end file path (`parse_trace_file` over a committed fixture
  replaying the codex sandbox session shape), not just in-memory line feeding.
- Land the two small user-facing doc updates.

#### Deliverables
- [ ] `tests/test_trace_parser.py`: enumerated cross-namespace matrix (S6 rename rows included) + fixture registration
- [ ] `tests/fixtures/strace/newroot_sandbox.strace`: committed fixture with the marker-probe + real-write session shape
- [ ] `docs/observe.md`: visibility-boundary note about sandbox-staging remap
- [ ] `docs/agent-sessions.md`: #33 rows updated to fixed/hard-assertion status

#### Implementation Details

**Matrix test** (one test method, `subTest` per row; watched root `/tmp/work`,
all `/newroot` spellings resolving into it):

| line fed | expected op | expected path(s) |
|---|---|---|
| `mkdir("/newroot/tmp/work/d", 0755)` | create | `/tmp/work/d` |
| `unlink("/newroot/tmp/work/f")` | delete | `/tmp/work/f` |
| `truncate("/newroot/tmp/work/f", 0)` | modify | `/tmp/work/f` |
| `chmod("/newroot/tmp/work/f", 0600)` | chmod | `/tmp/work/f` |
| `chown("/newroot/tmp/work/f", 1000, 1000)` | metadata | `/tmp/work/f` |
| `rename("/newroot/tmp/work/a", "/tmp/work/b")` | rename | old `/tmp/work/a`, new `/tmp/work/b` (mixed spelling, S6) |
| `rename("/tmp/work/a", "/newroot/tmp/work/b")` | rename | old `/tmp/work/a`, new `/tmp/work/b` (mixed, other side) |
| `rename("/newroot/tmp/work/a", "/newroot/tmp/work/b")` | rename | both canonical (fully staged spelling) |
| `mkdirat(AT_FDCWD</newroot/tmp/work>, "d2", 0755)` | create | `/tmp/work/d2` (`-yy` annotated dirfd arrival) |
| `symlinkat("t", AT_FDCWD</newroot/tmp/work>, "l")` | create | `/tmp/work/l` |
| `rename("/newroot/tmp/work/in", "/newroot/etc/out")` | *(dropped)* | genuine cross-boundary rename stays dropped (S3/S6 guard) |

**Fixture** `tests/fixtures/strace/newroot_sandbox.strace`: first line must
contain the string `strace` (existing fixture-header assertion), then the
codex session shape distilled from FINDINGS F1: a marker
`mkdir("/newroot/tmp/work/.git")` → canonical `rmdir("/tmp/work/.git")` pair,
plus a real-file sequence (`openat` create via the `/newroot` spelling with a
`-yy` result annotation, `write` through that fd, canonical `unlink`).
Registered in `test_committed_fixture_files_parse` (flat name→ops dict, one
entry) with expected ops `["create", "delete", "create", "modify", "delete"]`
(exact order finalized against the fixture when written). Note: the shared
fixture harness parses with `watched_roots=["/tmp/work"]` only if the existing
`self.parse` helper is used with that default — verify the committed-fixtures
test's parse configuration and thread watched roots through it the way the
existing entries do (adjusting only the new entry's expectations, never the
existing ones).

**`docs/observe.md`** — one short paragraph in the visibility-boundary section
(near lines 105–110): direct events whose paths arrive under a known
sandbox-staging prefix (`/newroot/...`, the pivot_root staging convention used
by mount-namespace sandboxes such as codex's) are remapped to the canonical
spelling when the stripped path falls inside a watched root, so both sides of
a sandbox create/delete pair land in the same namespace.

**`docs/agent-sessions.md`** — update the two #33 rows (scenario table line
64, known-bug table line 102) to fixed/past-tense status consistent with the
flip, leaving the registry-mechanism prose and the #32/#36 rows untouched.

#### Acceptance Criteria
- [ ] Matrix fully enumerated and green; S10 holds (full suite + selftests, zero skips)
- [ ] Fixture parses via the standard `parse_trace_file` path with JSONL output
- [ ] No product-code changes in this phase (tests + fixture + docs only) — if
      the matrix exposes a gap, the gap is fixed in a Phase-1-style code commit
      *first*, then this phase's tests land (plan deviation documented)

#### Test Plan
- **Unit/Integration**: as above — the matrix is integration-grade (real
  parser, real dispatch, no mocks; house style uses no mocking in this file).
- **Manual**: none needed beyond the suite; optional live evidence run is a
  validation checkpoint, not a phase gate.

#### Rollback Strategy
Tests-fixture-docs-only commit; revert is a pure coverage/docs reduction with
no behavior change.

#### Risks
- **Risk**: fixture drifts from real strace output shapes.
  - **Mitigation**: forms are copied from the spec's verified behavior matrix
    and the oracle probe (both derived from the real codex `-yy` session in
    FINDINGS F1).
- **Risk**: doc wording overpromises (e.g. implies arbitrary sandbox support).
  - **Mitigation**: phrase as "known staging prefixes, currently `/newroot`",
    mirroring the spec's non-goals.

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
- **Snapshot dedup / viewer layers**: consume the now-complete canonical
  direct stream through existing pipelines; no changes needed (spec:
  remapping makes dedup evidence whole without touching
  `deduplicate_snapshot_events`).

## Risk Analysis

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Remap rewrites a path it shouldn't | L | H | two-rule guard; S4/S5 pin both; scope filter unchanged downstream |
| Fix lands without flip (or vice versa) | L | M | same-commit rule in Phase 1; rot-proof gate fails loudly either way |
| Fixture/matrix forms don't match real strace | L | M | forms taken from verified F1 repro and spec behavior matrix |
| Perf regression in hot parse loop | L | L | remap is O(prefixes × roots) string ops, prefix tuple length 1; no I/O |

## Validation Checkpoints
1. **After Phase 1**: full suite + `--selftest` green;
   `bug33_unpaired_marker_delete() == False`; spec behavior-matrix rows flip.
2. **After Phase 2**: matrix green; fixture in committed-fixtures test; docs
   rendered sensibly.
3. **Before PR**: spec acceptance criteria 1–9 + non-functional checklist
   walked end-to-end; optional live evidence
   (`python -m tests.agent_sessions --scenarios single_write,ephemeral --tools codex`)
   if codex is authenticated in the environment — evidence, not a gate.

## Documentation Updates Required
- [ ] `docs/observe.md` visibility-boundary note (Phase 2)
- [ ] `docs/agent-sessions.md` #33 status rows (Phase 2)
- [ ] Review-phase routing per protocol: candidate lessons-learned entry
      (namespace-split path identity) — decided during Review, not here.

## Post-Implementation Tasks
- [ ] Review phase: `codev/reviews/33-codex-mount-namespace-sandbox-.md`
- [ ] Optional live-agent evidence run (see checkpoint 3)

## Expert Review

*(pending — populated after 3-way review)*

## Approval
- [ ] Expert AI consultation complete (porch 3-way)
- [ ] Human `plan-approval` gate

## Change Log
| Date | Change | Reason | Author |
|------|--------|--------|--------|
| 2026-07-19 | Initial plan | — | builder spir-33 |

## Notes
- Line numbers referenced (trace_parser.py:48, 181–188; oracle.py:58;
  docs/agent-sessions.md:64,102; docs/observe.md:105–110) verified on this
  branch at plan time; re-verify at implementation if other work lands first.
- Porch requires ≥2 phases; the natural atomic unit (fix+tests+flip) is
  Phase 1, and the defense matrix + fixture + docs are genuinely separable,
  independently valuable work — not padding (mirrors the accepted plan-32
  split).
