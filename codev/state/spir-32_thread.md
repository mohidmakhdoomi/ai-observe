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
