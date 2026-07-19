# spir-33 thread — codex mount-namespace sandbox breaks watched-root filtering

## 2026-07-19 — Specify phase

Spawned in strict mode on issue #33. No existing spec, no Baked Decisions in
the issue body.

Research findings that shaped the spec:

- Verified the repro directly against this branch's parser: `mkdir("/newroot<root>/.git")`
  is dropped by `_drop_out_of_scope_event` (trace_parser.py:445) while the
  canonical `rmdir` passes → unpaired delete. Matches issue/harness F1 exactly.
- **Blast radius is wider than the issue text**: an `openat` that arrives in the
  `/newroot` spelling loses not only its create — every subsequent `write`
  through that fd is dropped too, because the fd table records the `/newroot`
  spelling and the write event inherits it. Real file writes can vanish, not
  just marker noise. Documented in the spec's behavior matrix.
- Watched roots flow from `parse_roots` (snapshot.py) via the snapshot
  backend's prepare into every TraceParser construction (live, post-hoc, both
  rebuilds) — so a parser-level fix covers all entry points. With
  `AI_OBSERVE_BACKENDS=strace` only, watched_roots is empty → no filtering and
  (per spec decision) no remap.
- Spec 38's oracle is already armed for this fix: `OPEN_BUGS[33]` active with a
  deterministic parser-level probe, and selftests that demand the one-line
  `active = False` flip when the fix lands. The flip is a MUST criterion.

Spec decision: guarded lexical sandbox-prefix remap (`/newroot` constant) at a
single event-emission choke point, remap only when the stripped path lands
inside a watched root; rejected realpath (can't resolve another namespace's
paths), pairing heuristics (oracle, not mechanism), and root-mirroring (keeps
two spellings). No new env surface, no schema change.

Spec drafted at codev/specs/33-codex-mount-namespace-sandbox-.md → signaling
porch for 3-way review.

**3-way spec review, iteration 1: unanimous APPROVE, HIGH confidence, zero key
issues** (gemini / codex / claude). Claude's reviewer independently re-verified
the root-cause line references and guard logic against source. Folded in its
one suggestion (explicit `chdir`-into-`/newroot` unit-test scenario) and
populated the Consultation Log. Gate `spec-approval` reached — notified
architect, waiting for human approval.

## 2026-07-19 — Plan phase

Architect approved the spec (verified scope-filter and oracle claims against
source; endorsed Approach B, the realpath rejection, and no env knob).

Plan drafted mirroring the accepted spec-32 split: Phase 1 = remap constant +
choke-point step + core guard tests + `OPEN_BUGS[33]` flip in ONE commit (the
rot-proof gate fails loudly if fix and flip are split); Phase 2 = 11-row
cross-namespace defense matrix + committed `newroot_sandbox.strace` fixture +
docs (observe.md visibility note, agent-sessions.md #33 rows).

**3-way plan review, iteration 1: gemini APPROVE, claude APPROVE, codex
COMMENT (all HIGH).** Codex's one actionable: the committed-fixture registry
parses with no watched_roots, so the fixture would never exercise the remap —
plan now prescribes two-path wiring explicitly (registry entry pins the
no-roots parse; dedicated `test_newroot_sandbox_fixture_remaps_to_canonical`
method feeds the fixture through `self.parse(..., watched_roots=["/tmp/work"])`).
Gate `plan-approval` reached — notified architect, waiting.

## Implement phase_1: build complete

Plan-approval gate was approved; porch moved to implement / phase_1.

Implemented the guarded sandbox-prefix remap exactly per plan:
- `SANDBOX_ROOT_PREFIXES = ("/newroot",)` + `_remap_sandbox_paths` /
  `_remap_sandbox_path` on `TraceParser`, inserted at the single emission
  choke point in `_parse_line` before BOTH drop filters. Guard rules 1-3 as
  specified (in-scope untouched; component-boundary single strip only into a
  watched root; else untouched for the scope filter).
- `OPEN_BUGS[33].active = False` — probe `bug33_unpaired_marker_delete()`
  verified returning False; Spec-38 selftests now exercise the hard-assert
  branch.
- Core guard tests in `tests/test_trace_parser.py`: S1-S5, S6/S7 rename,
  chdir arrival, S9 no-roots pass-through, plus annotated `-yy` result-path
  arrival and remap-before-artifact-filter ordering.

Note: mid-build an external edit (architect pass?) landed a second,
near-duplicate sandbox test set in `test_trace_parser.py`; kept that set as
canonical and deduped mine down to the two scenarios it lacked (annotated
result-path arrival, artifact-filter ordering) — 9 new tests total, one set.

Verification: `python3 -m unittest discover -s tests` 253 OK zero skips;
`python3 -m tests.agent_sessions --selftest` 56 OK; plan's acceptance command
(suite minus packaging smoke, from tests/) 232 OK. Signaling `porch done 33`.

## 2026-07-19 — Session collision (resolved) + phase_2

Mid-phase_1 the worktree briefly had TWO live builder sessions: the pre-resume
session's .builder-start.sh wrapper auto-respawned claude after the
plan-approval context reset, so the "stale" session kept building while the
architect intentionally resumed a fresh one. Both implemented phase_1
concurrently (the near-duplicate test set the previous entry attributed to an
"architect pass" was in fact the resumed session — me). The fresh session
detected the collision via files changing/staging underneath it, stood down
from driving porch, and flagged the architect, who killed the stale tree.
Net damage: none — the tree reconciled into one coherent set and the stale
session's `porch done 33` committed it (fd4dbdf). Lesson candidate for
Review: a worktree can host a zombie builder after a context-reset respawn;
check for sibling processes when resumed.

Phase_1 3-way review: unanimous APPROVE (gemini HIGH, codex MEDIUM, claude
HIGH), zero key issues. Porch advanced to phase_2.

Phase_2 (defense matrix + fixture + docs, no product code): 11-row
cross-namespace matrix incl. the three S6 rename rows and annotated-dirfd
arrivals; `newroot_sandbox.strace` fixture wired through both prescribed
paths (no-roots registry entry + `test_newroot_sandbox_fixture_remaps_to_canonical`);
observe.md visibility-boundary paragraph; agent-sessions.md #33 rows marked
fixed. Suite 255 OK zero skips, selftests 56 OK, acceptance command 234 OK.
Signaling `porch done 33`.
