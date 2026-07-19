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
