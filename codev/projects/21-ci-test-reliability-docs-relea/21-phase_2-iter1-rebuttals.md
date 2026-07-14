# Rebuttal — Phase 2 (CI workflow), iteration 1

Verdicts: Gemini APPROVE (HIGH), Claude APPROVE, Codex REQUEST_CHANGES (HIGH).
Both Codex issues are accepted and fixed in the updated `.github/workflows/ci.yml`
(staged in this iteration). A third defect was found while verifying the fixes and
is also fixed (see point 3).

## Codex issue 1 — `build` installed but not `setuptools>=77`

**Accepted, fixed.** `pyproject.toml` requires `setuptools>=77` (PEP 639 SPDX
license expression), and `tests/test_packaging_smoke.py` invokes the PEP 517
backend (`setuptools.build_meta`) directly against the HOST interpreter with
no build isolation — `setUpModule` skips (now: fails loudly, see issue 2's
mechanism) when setuptools is absent. Modern CPython environments do not
bundle setuptools, so `build` alone did not guarantee the harness could build.

Fix: the "Provision build tooling" step now runs
`python -m pip install --upgrade pip build "setuptools>=77"`, with a comment
explaining why the explicit backend pin is required.

## Codex issue 2 — main suite can go green while strace tests self-skip

**Accepted, fixed.** `tests/test_codex_observe.py` self-skips on ptrace denial
(`self.skipTest("ptrace denied")`), and other gates (Node presence, strace
presence) also skip rather than fail. Previously only the packaging-smoke step
had a fail-loud-on-skip check, so a matrix leg could lose strace-backed
coverage silently.

Fix: the main-suite step now uses the same mechanism — verbose unittest output
is teed to `$RUNNER_TEMP/suite-output.txt` and any reported skip fails the job
with an explanatory `::error::` annotation. Every skip gate in the main suite
corresponds to a capability this workflow explicitly provisions (Node 20,
strace, ptrace_scope=0), so any skip in CI is by definition lost coverage.

## 3. Additional defect found while verifying: grep false positive on test names

The naive `grep -E "skipped"` (plan mechanism (a), as originally written)
false-positives on test **names** that legitimately contain the word —
`test_malformed_line_skipped_with_warning` and
`test_unsupported_future_schema_version_skipped` in `test_viewer_tailer.py`
appear in verbose output as `... ok` lines but still match the bare word. With
the check added to the main suite (issue 2), every matrix leg would have
failed despite zero actual skips.

Fix: both grep checks (main suite and smoke) now anchor on unittest's own skip
markers: `grep -E "\.\.\. skipped|skipped=[0-9]"` — the `... skipped 'reason'`
result line (also emitted for module-level `SkipTest` from `setUpModule`) and
the `OK (skipped=N)` summary. Test-name lines end in `... ok` and cannot match.

## Verification (local, umask 022)

- YAML parses (`yaml.safe_load`).
- Main suite exactly as the CI step runs it (module list from `tests/` cwd,
  excluding `test_packaging_smoke`): **213/213 OK**, anchored grep finds no
  match (the bare-word grep DID false-positive on the two test names above).
- Packaging smoke exactly as the CI step runs it: **21/21 OK**, zero skips,
  grep clean.
- Anchored grep positively matches both skip forms (per-test
  `... skipped 'reason'` and module-level `setUpModule (...) ... skipped`),
  verified against generated fixtures on this interpreter.
