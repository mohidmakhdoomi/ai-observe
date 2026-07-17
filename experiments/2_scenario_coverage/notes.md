# Experiment 2: Scenario coverage across claude / agy / codex

**Status**: Complete

**Date**: 2026-07-16

## Goal

Reuse the Experiment 1 harness to exercise ai-observe across varied scenarios
and build a coverage matrix (scenario × tool × what ai-observe got right/wrong).
Each scenario is a prompt engineered to trigger a specific ai-observe behavior;
for every run we compare three surfaces: what the agent actually did, what
ai-observe reported (canonical `.jsonl` + provenance), and what the viewer
served.

Scenarios:
- **subprocess** — agent runs a shell loop creating 3 files via a grandchild
  process (tests process-tree-scoped capture).
- **ephemeral** — agent creates a file then deletes it in-session (tests direct
  vs. inferred provenance and the documented issue #18: create+delete between
  snapshots).
- **modify** — a pre-seeded file is appended to (tests modify vs. create).

## Effort

~2 hours (scenario design + cross-tool runs + root-causing the delete gap).

## Approach

`scenarios.py` imports `run_observed_session` from
`../1_driving_mechanism/harness.py`, so no harness logic is duplicated. Each
scenario supplies a prompt builder, an optional setup, and a `check()` that
verifies the real user path (actual files + content), not just "the harness
ran". Results are written to `data/output/coverage_matrix.json`.

## Environment & Reproduction

```bash
python3 scenarios.py --tools claude,agy,codex --only subprocess,ephemeral,modify
```

Same environment as Experiment 1. codex is slow (~25–60 s/scenario) and floods
the trace, so it was run separately and the matrix merged.

## Code

- [`scenarios.py`](scenarios.py) — scenario definitions + runner (reuses the
  Experiment 1 harness).

## Results

### Coverage matrix (canonical events by operation)

| Scenario   | claude | agy | codex | ai-observe verdict |
|------------|--------|-----|-------|--------------------|
| subprocess | 9 (create 3, modify 6) | 9 (create 3, modify 6) | 15 (create 3, modify 6, **delete 6**) | ✅ **all 3 grandchild-created files captured** for every tool — process-tree scoping works. codex's extra 6 deletes are sandbox marker-dir noise (see finding 3). |
| ephemeral  | 2 (modify 2) | 3 (modify 3) | 57 (modify 2, delete 55) | ⚠️ **claude & agy: the file's deletion is MISSED** (finding 1). Net-vs-direct provenance otherwise behaves as designed (snapshot omits the net-zero file). codex's 55 deletes are marker-dir noise; its real `unlink(abs)` of the file *is* captured. |
| modify     | 1 (modify 1) | 2 (modify 2) | 38 (modify 2, delete 36) | ✅ append reported as `modify`; original content preserved. codex's 36 deletes are, again, marker-dir noise drowning the 2 real events. |

Every scenario's `check` passed on the *agent* side (files present/absent and
content correct) — the divergences are all in what **ai-observe reported**.

### Key Findings

1. **Relative `unlinkat` deletions are silently dropped (confirmed bug).** In
   the ephemeral scenario, claude and agy delete the file with
   `unlinkat(AT_FDCWD<dir>, "ephemeral.txt", 0) = 0`, but ai-observe emits **no
   delete event** — the canonical stream shows only the create/writes, so the
   viewer presents the file as *modified and present* when it was actually
   deleted. Root cause pinned by driving the parser directly
   (`src/ai_observe/trace_parser.py`):

   | trace form | event emitted? |
   |------------|----------------|
   | `unlinkat(AT_FDCWD<dir>, "f", 0)` (strace path-annotated) | **NO** |
   | `unlinkat(AT_FDCWD, "f", 0)` (plain) | delete ✅ |
   | `unlink("<abs>")` | delete ✅ |

   `_at_path` compares `args[dirfd] != "AT_FDCWD"`, but strace's default
   path-decoding annotates the dirfd as `AT_FDCWD<...>`, so it is mistaken for a
   real directory fd; `_dirfd_path` can't resolve it, the path becomes `None`,
   and `_drop_out_of_scope_event` discards the event. This affects the common
   libc deletion path and should be filed against ai-observe.

2. **Process-tree scoping works.** Files created by a grandchild `bash` loop
   (agent → shell → redirection) were all captured for every tool. ai-observe's
   `strace -f` process-tree following holds across the agent's subprocess spawns.

3. **codex's sandbox marker-dir churn floods the delete stream.** On *every*
   scenario codex creates and removes `.git`/`.agents`/`.codex` marker dirs in
   the workspace (its landlock/mount-namespace probing). Because the `mkdir`s go
   through a `/newroot/...` path (dropped) while the `rmdir`s use the canonical
   path (kept), the canonical stream is dominated by unpaired `delete`s: 55 of
   57 events (ephemeral) and 36 of 38 (modify) are this noise, burying the 2
   real events. Same root mechanism as Experiment 1's headline finding.

4. **Net-vs-direct provenance behaves as designed** (the intended half of issue
   #18): for the ephemeral file, the snapshot (inferred/net) layer correctly
   omits a file whose net effect is zero, while the direct (strace) layer *does*
   record its creation — the two layers are meant to disagree here. The *bug* is
   orthogonal: the direct layer should also have recorded the deletion.

### Output Files

- `data/output/coverage_matrix.json` — full matrix incl. per-scenario checks
  (committed; relative paths only, no raw syscalls).
- `data/output/*.jsonl`, `*.trace`, `*.meta.json` — raw artifacts (git-ignored).

## What Worked

- Reusing the harness verbatim across a new experiment — zero duplication.
- Engineering each prompt to isolate one behavior, then triangulating agent /
  canonical / viewer surfaces — this is what surfaced finding 1.
- Driving `trace_parser` directly to pin the delete-drop to the annotated
  `AT_FDCWD<...>` form.

## What Didn't Work

- Running all three tools in one invocation blew the 2-minute command budget
  (codex is slow); split codex out and merged the matrix.

## Next Steps

1. **Immediate**: fold both accuracy findings into the top-level findings
   summary and recommend filing ai-observe issues.
2. **Follow-up experiments**: interrupt/recovery (SIGINT mid-session →
   `.partial`/`.rebuilt` authority), and viewer-opened-mid-session vs. before.
3. **ai-observe fix sketch** for finding 1: treat a dirfd arg of `AT_FDCWD`
   *with or without* a `<...>` annotation as "resolve against cwd," or extract
   the annotation in `_dirfd_path`.

## References

- Issue #31; Experiment 1 (`../1_driving_mechanism/notes.md`).
- `src/ai_observe/trace_parser.py` `_at_path` / `_dirfd_path` / `_drop_out_of_scope_event`.
