# Specification: trace_parser drops events with an annotated `AT_FDCWD` dirfd (claude/agy deletions never reported)

## Summary

`trace_parser._at_path` decides whether a `*at`-family syscall's dirfd needs fd-table
resolution with a literal string comparison against `"AT_FDCWD"`. When strace's
path decoding is active — and **ai-observe itself always activates it** (the strace
backend passes `-yy`, `src/ai_observe/backends/strace.py:61`) — modern strace
annotates the dirfd as `AT_FDCWD</some/dir>`. That form fails the equality check,
gets treated as a real (numeric) dirfd, fails to resolve (the shared
`_FD_ANNOT_RE` requires a numeric fd), and the whole path resolves to `None`.

The observable damage: `unlinkat(AT_FDCWD</dir>, "f", 0)` — the common libc
deletion path used by real Claude Code and agy sessions — produces **no delete
event**. The viewer shows the file as created/modified and never reports its
deletion, misrepresenting the file's final state. The same failure applies to
every `*at` syscall routed through `_at_path` with a relative path, not just
`unlinkat`.

This spec fixes dirfd resolution so the annotated form is handled correctly —
preferring the kernel-reported annotation as the resolution base — and flips the
live-agent oracle's known-bug gate for #32 to a hard regression assertion in the
same change (the gate is rot-proof and fails loudly otherwise).

Discovered by the live-agent test harness (#31, FINDINGS F2); root-caused with a
minimal repro in `experiments/2_scenario_coverage/`; re-verified against this
branch's parser during spec drafting.

## Background and current state

### Root cause chain (verified on this branch)

All in `src/ai_observe/trace_parser.py`:

1. `_at_path` (line 475) resolves the path argument of `*at` syscalls. For a
   relative path it picks a base directory: tracked per-process cwd by default,
   or the dirfd's path when the dirfd arg is not `AT_FDCWD`:

   ```python
   if dirfd_index is not None and len(args) > dirfd_index and args[dirfd_index].strip() != "AT_FDCWD":
       base = self._dirfd_path(pid, args[dirfd_index])
   ```

   The annotated form `AT_FDCWD</some/dir>` is `!= "AT_FDCWD"`, so it falls into
   the dirfd-resolution branch.

2. `_dirfd_path` (line 490) tries `fd_path_annotation` then `fd_number`. Both
   use `_FD_ANNOT_RE` (line 47): `^(?P<fd>-?\d+)(?:<(?P<path>[^>]*)>)?$` — the fd
   part **must be numeric**. `AT_FDCWD</some/dir>` matches neither, so
   `_dirfd_path` returns `None`.

3. `base` is `None` → `_at_path` returns `None` → the event either:
   - **is dropped entirely** when `watched_roots` is set (`_drop_out_of_scope_event`
     line 444 drops events with no resolvable path) — this is the real-session
     configuration, or
   - **is emitted pathless** (`path: null`) when `watched_roots` is empty — junk
     that downstream consumers (viewer, snapshot suppression) cannot attribute.

### Verified behavior matrix (driven directly against this branch's parser)

| trace line (watched root `/tmp/w`, cwd `/tmp/w`) | events emitted |
|---|---|
| `unlinkat(AT_FDCWD</tmp/w>, "f.txt", 0) = 0` (annotated — what real tools produce under `-yy`) | ❌ none |
| `unlinkat(AT_FDCWD, "f.txt", 0) = 0` (plain) | ✅ `delete /tmp/w/f.txt` |
| `unlink("/tmp/w/f.txt") = 0` | ✅ `delete /tmp/w/f.txt` |
| `mkdirat(AT_FDCWD</tmp/w>, "d", 0755) = 0` | ❌ none |
| `renameat2(AT_FDCWD</tmp/w>, "a", AT_FDCWD</tmp/w>, "b", 0) = 0` | ❌ none |
| `openat(AT_FDCWD</tmp/w>, "n.txt", O_CREAT\|O_EXCL, …) = 3</tmp/w/n.txt>` | ✅ `create` (rescued — see below) |

### Blast radius: every `_at_path` call site with a dirfd

Affected and **silently dropped** (these syscalls return `0`, so there is no
return-value annotation to rescue the path):

| syscall(s) | op | call site |
|---|---|---|
| `unlinkat` | delete | `trace_parser.py:344` |
| `renameat`, `renameat2` | rename (old and new path both) | `:351-352` |
| `fchmodat` | chmod | `:360` |
| `fchownat`, `utimensat`, `futimesat` | metadata | `:369` |
| `mkdirat`, `mknodat` | create | `:390` |
| `symlinkat` | create | `:395` |
| `linkat` | create | `:398` |

Affected but **rescued in practice**: `openat`/`openat2` (`:462-464`) also fail
`_at_path` resolution, but both event emission (`:289-290`) and fd-table tracking
(`:240-241`) prefer `result_path` — the annotation strace `-yy` puts on the
*returned* fd (`= 3</tmp/w/n.txt>`). Since the dirfd annotation and the return
annotation come from the same strace option, the configurations that trigger the
bug also provide the rescue. The fix still covers them (robustness — e.g. an
`openat` line whose return annotation is absent or truncated), but they are not
the observable damage.

### Why the annotated form is the norm, and why the plain form must keep working

ai-observe's strace backend hardcodes `-yy` (`backends/strace.py:61`), and modern
strace annotates `AT_FDCWD` with the process cwd under fd decoding. So annotated
dirfds are what every real session produces on current strace. Older strace
versions (and the existing committed fixtures/tests) emit the plain `AT_FDCWD`
form — both forms MUST parse identically apart from the base-directory source.

### The live-agent oracle is already armed for this fix (Spec 38)

`tests/agent_sessions/oracle.py` tracks this bug as `OPEN_BUGS[32]` with a
**rot-proof** gate driven by a deterministic parser probe (`bug32_signature`,
line 210 — it feeds the exact annotated/plain forms through the real
`TraceParser`):

- While `active=True`: the gate asserts the bug *still reproduces*. Landing the
  fix without flipping the flag makes `expect_deletion_captured` and
  `selftest_oracle.test_bug32_reproduction_matches_registry` **fail loudly**
  ("fix landed? flip the flag").
- Flipping `OPEN_BUGS[32].active = False` (a one-line change) turns the gate into
  a hard regression assertion for the fixed behavior.

Therefore the flip is a **mandatory part of this change**, in the same PR as the
parser fix. (The live suite is opt-in, `python -m tests.agent_sessions`, and is
excluded from the default CI matrix by construction — but its selftests must stay
green for anyone who runs them, and shipping fix and flip together is exactly the
workflow Spec 38 designed for.)

### Current test coverage of this area

- `tests/test_trace_parser.py` exercises plain `AT_FDCWD` heavily and numeric
  dirfds (`renameat(99, …)`, `unlinkat(99, …)` with an *unknown* fd → dropped;
  annotated numeric fds like `4</tmp/work/dir>` resolve via `_FD_ANNOT_RE`).
- **No existing unit test uses the annotated `AT_FDCWD<path>` form** — that is the
  coverage hole this bug lived in.

## Constraints

- The issue's fix direction (not a formal Baked Decisions section, but the
  architect's stated direction): treat a dirfd of `AT_FDCWD` **with or without**
  a `<...>` annotation as "resolve against cwd" — *or better*, extract the
  annotation and use it as the base directory, since it is the kernel-reported
  dirfd path and more authoritative than tracked cwd. This spec adopts the
  stronger variant (see Solution exploration).
- `trace_parser.py` is stdlib-only and must stay that way.
- CI fails loud on ANY unittest skip — new tests must not introduce
  capability-gated skips (none are needed; everything here is pure parsing).
- Provenance model (arch.md §Provenance): recovered events are direct strace
  evidence — `source: "strace"`, `confidence: "direct"`. Nothing about the fix
  changes provenance fields; the fix only stops valid direct events from being
  lost.
- Scope: parser-side dirfd resolution only. Sibling bugs #33 (codex `/newroot`
  marker noise) and #36 (sidecar authority overstatement) are separate issues
  with their own gates — do not touch their `OPEN_BUGS` entries.

## Stakeholders

- **ai-observe users** (primary): currently shown a final file state that is
  wrong — deleted files appear to still exist. This is silent data loss in the
  product's core promise (report what the agent did).
- **Snapshot/net provenance layer**: uses direct events to suppress inferred
  ones; missing deletes can mislead net-effect reporting depending on timing.
- **Live-agent test suite maintainers**: the ephemeral scenario currently
  annotates rather than asserts; the flip makes it a hard assertion.

## Solution exploration

### Approach A — equality fix only: treat `AT_FDCWD` (annotated or not) as cwd

Change the check so any dirfd token that **is** `AT_FDCWD`, with or without an
annotation, keeps `base = tracked cwd`.

- **Pros**: one-line, minimal blast radius, restores parity with the plain form.
- **Cons**: discards the annotation — the kernel-reported cwd of the traced
  process at syscall time. Tracked cwd is a best-effort reconstruction (initial
  cwd + observed `chdir`/`fchdir`; `fchdir` through an untracked fd silently
  loses sync). When tracked cwd is stale, resolution is wrong even though the
  correct answer is sitting in the trace line.
- **Complexity/risk**: trivial / lowest.

### Approach B (recommended) — use the annotation as the base; fall back to tracked cwd

In `_at_path`, recognize the dirfd token `AT_FDCWD` with an optional
`<path>` annotation:

- annotation present and non-empty → `base = annotation` (kernel-reported,
  authoritative);
- plain `AT_FDCWD` (or empty annotation) → `base = tracked cwd` (current
  behavior);
- numeric dirfd → existing `_dirfd_path` behavior, unchanged.

- **Pros**: implements the issue's "or better" direction; self-corrects when
  tracked cwd is stale; strictly more information used, no behavior change for
  any currently-working input.
- **Cons**: slightly more code than A; introduces a second source of truth for
  the base directory (annotation vs tracked cwd) — mitigated by a clear
  precedence rule (annotation wins when present).
- **Complexity/risk**: low. The parsing is a small, local token match; `split_args`
  already keeps `<...>` groups intact (angle-depth tracking, line 521-524).

### Approach C — generalize `_FD_ANNOT_RE` to accept symbolic fd names

Widen the shared regex so `AT_FDCWD</dir>` matches with a symbolic fd group, and
let `_dirfd_path` handle it.

- **Pros**: fixes annotation extraction at the lowest level.
- **Cons**: `_FD_ANNOT_RE` is shared by `parse_result`, `fd_number`, and
  `fd_path_annotation` — `parse_result` must keep numeric semantics (`int(...)`
  on the fd group would raise), so the widening needs per-caller guards. Broadest
  blast radius for the same behavior; easy to regress return-value parsing.
- **Complexity/risk**: medium — highest regression risk for no additional user
  value over B.

**Recommendation: Approach B**, implemented locally in `_at_path` (a small helper
for "is this dirfd AT_FDCWD, and what's its annotation" is acceptable; the exact
shape is a Plan decision). `_FD_ANNOT_RE` and its three callers stay untouched.

## Open questions

- **Important — none blocking.**
- **Nice-to-know**: strace can, in principle, annotate paths containing `>`
  (the annotation regex `[^>]*` stops at the first `>`). This pre-exists for
  numeric fd annotations and is not made worse by this fix; explicitly out of
  scope.
- **Nice-to-know**: whether `openat` should prefer the *dirfd-derived* argument
  path over `result_path` after the fix. No — `result_path` is the resolved path
  of the actual opened file and remains the better source; no change.

## Success criteria (acceptance)

### Functional (MUST)

1. `unlinkat(AT_FDCWD</dir>, "f", 0) = 0` emits `delete` with path `/dir/f`
   under a watched root covering `/dir` — the exact FINDINGS-F2 form.
2. Every dropped-in-practice call site resolves the annotated form correctly:
   `unlinkat`, `renameat`/`renameat2` (both old and new path), `fchmodat`,
   `fchownat`/`utimensat`/`futimesat`, `mkdirat`/`mknodat`, `symlinkat`,
   `linkat`. (Parameterized/enumerated unit tests in `tests/test_trace_parser.py`.)
3. The annotation takes precedence over tracked cwd: with tracked cwd = `/a` and
   dirfd `AT_FDCWD</b>`, a relative path resolves under `/b`.
4. Plain `AT_FDCWD` continues to resolve against tracked cwd; empty annotation
   (`AT_FDCWD<>`) falls back to tracked cwd rather than producing a broken base.
5. Numeric-dirfd behavior is unchanged: annotated numeric fds resolve via the
   annotation, known fds via the fd table, unknown numeric fds still yield no
   event (existing `unlinkat(99, "unknown", 0)` expectation preserved).
6. `OPEN_BUGS[32].active` flipped to `False` in `tests/agent_sessions/oracle.py`
   in the same change; `bug32_signature()` then returns
   `(dropped=False, plain_captured=True)` and both
   `selftest_oracle.test_bug32_reproduction_matches_registry` and
   `test_deletion_gate_tracks_registry` pass in their post-fix branches.
7. `OPEN_BUGS[33]` and `OPEN_BUGS[36]` untouched and their selftests still pass.

### Non-functional (MUST)

8. Full existing suite green (`python -m unittest -v` over `tests/test_*.py`
   modules), zero skips, plus the opt-in suite's tool-free selftests green
   (`python -m tests.agent_sessions --selftest`).
9. `trace_parser.py` remains stdlib-only; no new dependencies.
10. No provenance/schema changes — recovered events carry the same
    `source: "strace"`, `confidence: "direct"` fields as every direct event.

### Verification scenarios

- **Unit (deterministic, CI)**: the matrix above driven through `TraceParser`
  directly, both watched-roots and no-watched-roots configurations; the
  no-watched-roots configuration must no longer emit pathless events for
  annotated-`AT_FDCWD` syscalls (they now resolve).
- **Oracle probe (deterministic, opt-in selftest)**: `bug32_signature()` — the
  authoritative "is #32 fixed" signal, already written by Spec 38.
- **Live (optional, not CI)**: `python -m tests.agent_sessions --scenarios
  ephemeral --tools claude,agy` on a machine with authenticated tools — the
  create-then-delete scenario's canonical view flips from `known-bug:#32` to a
  hard `pass`. Nice-to-have evidence, not a gate (agent nondeterminism is exactly
  why Spec 38 made the bug gate a parser probe).

## Non-goals

- Fixing #33 (mount-namespace path canonicalization) or #36 (sidecar authority
  labeling) — tracked separately.
- Changing the strace invocation (`-yy` stays).
- Improving cwd tracking generally (e.g. `fchdir` through untracked fds) beyond
  what the annotation-as-base rule already buys.
- Widening `_FD_ANNOT_RE` or touching `parse_result`/`fd_number`/
  `fd_path_annotation` semantics.
- Snapshot-layer or viewer changes: correct delete events flowing through the
  existing pipeline is the whole fix.

## Consultation Log

### Specify, iteration 1 (gemini / codex / claude) — unanimous APPROVE, high confidence

- **codex**: APPROVE, no issues. "Well-scoped, technically sound, and specific
  enough for implementation and verification."
- **gemini**: APPROVE, no issues. Confirmed root cause, blast radius, and that
  Approach B "safely isolates the fix without breaking existing fd resolution."
  (Its one minor note — a suspected markdown typo in Success Criterion 3 — was a
  false positive; `AT_FDCWD</b>` is the intended literal form.)
- **claude**: APPROVE, no issues. Independently verified every code claim
  (regex, `_at_path` check, `_dirfd_path` chain, out-of-scope drop, `-yy` flag,
  `OPEN_BUGS[32]` gate and selftests) against source. Two non-blocking notes,
  both acknowledged: (1) a relative annotation path (`AT_FDCWD<rel/dir>`) does
  not occur in practice — strace reports absolute annotation paths — and the
  join-based fix is correct-by-construction even if it did; (2) line-number
  references may drift if other work lands first — the builder re-verifies at
  implementation time.

No spec changes were required by any reviewer.
