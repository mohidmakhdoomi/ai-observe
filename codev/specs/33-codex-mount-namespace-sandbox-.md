# Specification: codex mount-namespace sandbox (`/newroot`) breaks watched-root filtering (dozens of unpaired delete events)

## Summary

Under codex's mount-namespace sandbox, the *same* logical file appears in the
strace stream under two path spellings: the canonical host path
(`/tmp/work/.git`) and the sandbox-staging spelling with the mount-namespace
prefix prepended (`/newroot/tmp/work/.git`). ai-observe's watched-root scope
filter compares paths lexically against the resolved watched roots, so the
`/newroot/...` spelling never matches: those events are silently dropped while
their canonical-spelling twins pass. codex probes its workspace by repeatedly
creating (`mkdir` via `/newroot/...`) and removing (`rmdir` via canonical path)
`.git`/`.agents`/`.codex` marker directories, so every codex session emits an
asymmetric stream of dozens of unpaired `delete` events implying destruction
whose net effect was nothing — 54 unpaired deletes on a single-file task, 55 of
57 canonical events being this noise (issue #33, harness findings F1).

The fix: recognize the sandbox prefix and remap `/newroot/<path>` events back
into the canonical namespace *before* watched-root filtering, guarded so the
remap only fires when the stripped path lands inside a watched root. Both sides
of every marker pair then land in the same namespace: the creates appear, the
deletes pair up, and the direct layer tells the truth.

This is an accuracy fix in the generic parser core (`trace_parser.py`), not a
codex-specific hack: `/newroot` is the pivot_root staging convention used by
bubblewrap-style sandboxes generally.

## Background and current state

### Root cause chain (verified on this branch)

1. **Watched roots** come from `parse_roots(AI_OBSERVE_ROOTS, cwd)`
   (`src/ai_observe/snapshot.py:134`): absolute, symlink-resolved
   (`Path.resolve(strict=True)`), defaulting to the initial cwd. The snapshot
   backend's `prepare` publishes them onto the session
   (`src/ai_observe/backends/snapshot.py:42`), and the strace backend passes
   them into every `TraceParser` construction — live parser, post-hoc parse,
   and both rebuild paths (`src/ai_observe/backends/strace.py:81,131,180,205`).
   A parser-level fix therefore covers all parse entry points at once.

2. **The scope filter is lexical.** `TraceParser._drop_out_of_scope_event`
   (`src/ai_observe/trace_parser.py:445`) collects the event's `path`,
   `old_path`, `new_path`, and drops the event if any of them is not
   `is_relative_to` a watched root. Paths reach it via `normalize_abs_path`
   (`os.path.normpath` — purely lexical; no realpath, no namespace awareness).

3. **codex's sandbox splits one directory across two spellings.** During
   sandbox setup, syscalls travel through the staging mount: strace records
   `mkdir("/newroot/tmp/work/.git", 0755) = 0`. After pivot, the removals use
   the canonical spelling: `rmdir("/tmp/work/.git") = 0`. Verified directly
   against this branch's parser with `watched_roots=("/tmp/work",)`:

   | trace lines | events emitted |
   |---|---|
   | `mkdir("/newroot/tmp/work/.git")` + `rmdir("/tmp/work/.git")` | **only** `delete /tmp/work/.git` (create dropped) |
   | `openat(AT_FDCWD, "/newroot/tmp/work/f.txt", O_WRONLY\|O_CREAT\|O_EXCL) = 3` + `write(3, …) = 1` | **nothing** (create dropped; the write's path comes from the fd table as `/newroot/...` and is dropped too) |
   | same mkdir/rmdir pair, `watched_roots=()` | `create /newroot/tmp/work/.git` + `delete /tmp/work/.git` — both kept but in different namespaces, so consumers can't pair them |

   The second row is a blast-radius finding beyond the issue text: it is not
   only marker-noise `mkdir`s that vanish. *Any* event whose path arrives in
   the `/newroot` spelling is dropped — including real file creates and every
   subsequent write through an fd that was opened via the `/newroot` spelling,
   because the fd table faithfully records the spelling the open used.

4. **The snapshot (net/inferred) layer stays correct** — marker dirs have no
   net effect, so no snapshot events exist for them. The direct-vs-snapshot
   disagreement is the diagnostic signature, and the snapshot layer is the
   oracle that proves the deletes are phantom.

### Where the impact lands

- **Viewer / canonical `.jsonl` consumers**: every codex session's direct
  layer is dominated by phantom deletes (55/57, 36/38 in harness runs),
  burying real events and implying destruction that never happened.
- **Snapshot dedup** (`deduplicate_snapshot_events`,
  `src/ai_observe/snapshot.py:293`) matches direct vs inferred events by
  normalized path. Dropped or wrong-namespace direct events can't cover their
  inferred twins; canonical-namespace remapping makes dedup evidence whole.

### The live-agent oracle is already armed for this fix (Spec 38)

`tests/agent_sessions/oracle.py` maintains a rot-proof known-bug registry:

- `OPEN_BUGS[33]` is **active**; `bug33_unpaired_marker_delete()` reproduces
  this exact signature deterministically through the real `TraceParser`
  (a `/newroot` mkdir + canonical rmdir yielding deletes > creates).
- While active, the gate asserts the bug *still reproduces*; the selftest
  (`tests/agent_sessions/selftest/selftest_oracle.py:83-91`) fails loudly with
  "fix landed? flip the flag" if a fix merges without flipping.
- Flipping `OPEN_BUGS[33].active = False` is a required part of this fix and
  converts the probe into a hard regression assertion (as already done for
  #32).

### Current test coverage of this area

`tests/test_trace_parser.py` covers the scope filter's canonical-namespace
behavior (`test_watched_roots_drop_outside_and_cross_boundary_direct_events`,
`test_no_watched_roots_annotated_events_are_fully_pathed`) and the annotated
dirfd matrix from #32. Nothing exercises a mount-namespace prefix; the only
`/newroot` coverage in the repo is the Spec-38 oracle probe asserting the bug
*exists*.

## Constraints

- No Baked Decisions section exists on issue #33; decisions below were made by
  the builder and are open to review.
- Parser philosophy: "favors safe false negatives over false positives"
  (`trace_parser.py` module docstring). The remap must be guarded/conservative
  so it cannot mis-relabel genuinely out-of-scope activity as in-scope.
- The parser and its tests are stdlib-only; the remap must be lexical (no
  filesystem I/O per event — `/newroot/...` does not exist in the observer's
  own namespace anyway, and event paths may already be deleted).
- Provenance contract (schema v2: `schema_version` / `source` / `confidence`)
  stays stable; remapped events remain `source: "strace"`,
  `confidence: "direct"` — the operation was kernel-observed; only the path
  spelling is projected across the namespace boundary. `raw_syscall` already
  preserves the original `/newroot/...` text as evidence.
- Generic-core discipline: no codex-specific branching. The mechanism is
  "known sandbox root prefixes", of which `/newroot` (the bubblewrap-style
  pivot_root staging convention) is the first entry.
- CI fails loud on ANY unittest skip; all new tests must be assertion paths
  (the Spec-38 known-bug gate pattern already respects this).
- Keep worktree/PR scope to this fix; F2 (#32) is already fixed and #36 is a
  separate open bug.

## Stakeholders

- **codex-session users** (primary): the direct layer currently misleads for a
  supported tool on every session.
- **Viewer consumers**: event stream feeding the browser viewer becomes
  truthful without viewer changes.
- **Snapshot dedup / provenance layers**: gain complete direct evidence.
- **Live-agent harness maintainers**: `OPEN_BUGS[33]` flip converts noise
  tolerance into a regression assertion.

## Solution exploration

### Approach A — realpath resolution before filtering

Resolve every event path with `os.path.realpath` (or compare inode identity)
before the scope check.

- **Pros**: general; would also handle symlinked spellings.
- **Cons**: `/newroot/...` exists only *inside the sandbox's mount namespace*;
  in the observer's namespace `realpath` is purely lexical for nonexistent
  paths and returns the `/newroot/...` spelling unchanged — it fixes nothing
  here. Adds per-event filesystem I/O and races against deletion for paths
  that do exist. The issue's "compare by resolved real path" wording is a
  dead end for this specific failure.
- **Verdict**: rejected.

### Approach B (recommended) — guarded lexical sandbox-prefix remap at event emission

In `TraceParser`, define a module-level constant for known sandbox staging
prefixes (initially `("/newroot",)`). At a single choke point — after an
event's `path` / `old_path` / `new_path` are assembled, before
`_drop_out_of_scope_event` and `_drop_artifact_event` — remap each absolute
path `p` independently:

1. If `p` is already within a watched root → leave unchanged. (Protects the
   corner where a watched root itself lives under a literal `/newroot`
   directory: in-scope events there are never rewritten.)
2. Else if `p` equals a known prefix or starts with `prefix + "/"`, and the
   stripped remainder (`"/" + p[len(prefix)+1:]`) **is within a watched
   root** → rewrite `p` to the stripped spelling.
3. Else → leave unchanged (the scope filter drops it exactly as today).

Prefix matching is component-wise (`/newrootfoo/...` never matches); a single
strip only (no recursion into `/newroot/newroot/...`). Process-state tables
(cwd, fd table) keep the raw spellings they observed; remapping only at event
emission still covers fd-propagated paths because event paths are drawn from
those tables at emission time (verified: the `openat`+`write` case above).
When `watched_roots` is empty (strace-only backend selection) there is no
validation basis, so no remap occurs — raw truth is preserved, documented as a
limitation.

- **Pros**: fixes every arrival form (mkdir args, fd-table propagation,
  dirfd/fd `-yy` annotations, result-path annotations) at one choke point;
  lexical and allocation-cheap; guarded by watched-root membership so false
  positives require an adversarial path that lexically lands inside a watched
  root — at which point observing it is arguably correct; covers live,
  post-hoc, and rebuild parses identically; no schema or env-surface change.
- **Cons**: `/newroot` is a convention, not a contract — another sandbox could
  use a different staging prefix (extending the constant is a one-line
  change); a genuinely distinct host directory named `/newroot/<watched-root>`
  would be conflated (implausible layout, and the guard bounds the damage to
  paths already inside the watched tree's mirror).
- **Verdict**: recommended.

### Approach C — pairing-symmetry post-processing

Detect unpaired `delete`s (or `create`/`delete` pairs across namespaces) in a
post-parse pass and suppress or re-pair them.

- **Pros**: no assumptions about specific prefixes.
- **Cons**: heuristic and order-dependent; suppressing events hides real
  activity (the creates *did* happen — the truthful stream contains both
  sides, paired); doesn't fix the dropped real creates/writes from the blast
  radius; complex to make safe. Pairing symmetry is the right *test oracle*
  (the Spec-38 probe already uses it), not the right mechanism.
- **Verdict**: rejected as mechanism; retained as oracle.

### Approach D — mirror watched roots into the sandbox namespace

For each watched root `R`, also watch `/newroot + R`, emitting events under
whatever spelling arrived.

- **Pros**: smallest diff (roots list only).
- **Cons**: stops the drop but keeps two spellings in the stream — the viewer,
  dedup, and any path-keyed consumer still see the create and delete as
  different files, so the unpaired-delete *presentation* survives. Fails the
  actual goal (path identity), not just the filter.
- **Verdict**: rejected.

## Open questions

- **Important — prefix configurability**: should the prefix list get an
  `AI_OBSERVE_*` env knob now? Recommendation: no — each public env var
  carries a compatibility-alias and matrix-test cost (lessons-learned:
  "Turn broad compatibility promises into explicit matrix tests"); a
  module-level constant serves until a second real-world convention shows up.
- **Important — empty-watched-roots behavior**: with `AI_OBSERVE_BACKENDS=strace`
  (no snapshot backend), `watched_roots` is empty and no remap occurs, so
  strace-only sessions keep the two-spelling stream. Recommendation: accept
  and document; remapping without a validation root would be an unguarded
  rewrite.
- **Nice-to-know — remap annotation**: should remapped events carry an
  explicit marker field (e.g. an optional sandbox-remap note) for consumers?
  Recommendation: no new schema surface; `raw_syscall` already preserves the
  original spelling, and a diff against `path` reveals the remap.

## Success criteria (acceptance)

### Functional (MUST)

1. **Marker pair symmetry**: parsing `mkdir("/newroot<root>/.git")` followed by
   `rmdir("<root>/.git")` with `<root>` watched yields **both** a `create` and
   a `delete`, each with `path = <root>/.git` (canonical spelling).
2. **Blast-radius recovery**: `openat(AT_FDCWD, "/newroot<root>/f.txt",
   O_WRONLY|O_CREAT|O_EXCL, 0600) = 3` followed by `write(3, …)` yields a
   `create` and a `modify`, both at `<root>/f.txt`.
3. **Out-of-scope stays out**: `/newroot/<elsewhere>/...` where the stripped
   path is outside every watched root is still dropped; plain out-of-scope
   canonical paths are still dropped (existing
   `test_watched_roots_drop_outside_and_cross_boundary_direct_events`
   behavior unchanged).
4. **No false remap inside a literal `/newroot` watched root**: with a watched
   root such as `/newroot/data`, events at `/newroot/data/f` keep their path
   unchanged (guard rule 1 fires before any strip).
5. **Component-boundary safety**: `/newrootfoo/<root-suffix>` paths are never
   remapped.
6. **Rename consistency**: a rename whose two ends arrive in different
   spellings of the same watched tree (e.g. old under `/newroot<root>`, new
   under `<root>`) emits with both `old_path` and `new_path` in canonical
   spelling; cross-boundary renames (either end genuinely outside) are still
   dropped.
7. **All three path fields** (`path`, `old_path`, `new_path`) are remapped
   independently at one choke point that runs before both the scope filter and
   the artifact filter.
8. **Oracle flip**: `OPEN_BUGS[33].active` flipped to `False` in
   `tests/agent_sessions/oracle.py`; `bug33_unpaired_marker_delete()` now
   returns `False` and the Spec-38 selftests
   (`test_marker_noise_gate_tracks_registry`, the flip-direction assertions)
   pass as hard regression gates.
9. **No behavior change without watched roots**: with `watched_roots=()`, the
   parser's output is byte-identical to today (no remap attempted).

### Non-functional (MUST)

- Remap is lexical only: no filesystem calls per event; per-event cost bounded
  by O(prefixes × watched roots) string operations.
- No event-schema change (`schema_version` stays 2; no new fields); no new
  env-var surface; no new dependencies (stdlib-only).
- Remapped events keep `source: "strace"`, `confidence: "direct"`, and their
  original `raw_syscall` text.
- Full unit suite passes with zero skips (CI fail-loud rule); no reduction in
  coverage of the scope-filter area.
- Fix lives in the generic parser core; no codex-specific conditionals.

### Verification scenarios

- **Unit (CI-gating)**: new `tests/test_trace_parser.py` cases for criteria
  1–7 and 9, driven through fixture trace lines in the annotated (`-yy`-style)
  forms real sessions produce (lessons-learned: "Match strace tokens with
  annotations in mind").
- **Oracle selftest (CI-gating)**: Spec-38 selftest suite green with the flag
  flipped — this is the deterministic end-to-end regression gate for the exact
  #33 signature.
- **Live harness (opt-in, non-gating)**: a codex `single_write` /
  `ephemeral` scenario run via the graduated agent-sessions harness should
  show marker creates and deletes paired (net zero) instead of dozens of
  unpaired deletes, with the real file events no longer buried; recorded as
  evidence, not CI.

## Non-goals

- Suppressing or de-noising the (now-paired) marker create/delete churn in the
  viewer — a possible future viewer-level presentation feature, explicitly out
  of scope here.
- Symlink-spelling canonicalization of event paths (pre-existing behavior,
  orthogonal to mount-namespace prefixes).
- Handling sandbox conventions other than the `/newroot` staging prefix, or
  making the prefix list user-configurable.
- Remapping when no watched roots are configured (strace-only backend mode).
- Fixes for the other open harness findings (#36 sidecar authority; #32 is
  already fixed and flipped).

## Consultation Log

*(pending — populated after 3-way review)*
