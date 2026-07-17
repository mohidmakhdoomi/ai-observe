# Plan 38 — Rebuttal to Plan iter 1 review

Verdicts: **Gemini COMMENT**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH
confidence). Every substantive point was accepted; nothing declined.

## Codex (REQUEST_CHANGES) — both points accepted and fixed

1. **M4 unauthenticated-tool branch untested.** *Accepted — fixed.* The plan tested
   `tool not found on PATH` but left the *present-but-unusable* branch (no auth / no
   events / immediate failure) to incidental manual runs. Added to **Phase 2**: a
   deterministic **fake-tool seam** — the runner resolves each tool's command through
   `TOOLS`, and `selftest_runner` injects a stub "tool" on a temp `PATH` that exits
   nonzero or emits no events, so the oracle's `ToolUnusable` → **loud, named `fail`**
   path is implemented *and* self-tested without any real agent. New AC and Test-Plan
   entries cover it.

2. **Requested-but-non-applicable tool/scenario pairs underspecified.** *Accepted —
   fixed.* "Cartesian minus applicability" could have silently omitted `agy`/`codex`
   from claude-only scenarios (`timeline`/`degraded`), conflicting with the spec's
   "fail or exclude with an explicit reason naming the tool." **Phase 2** now specifies
   that each scenario declares `applies_to`, and when a tool is **explicitly named in
   `--tools`** but a selected scenario excludes it, the runner emits an explicit
   `CheckResult(status="excluded", detail="scenario '<s>' does not apply to tool '<t>'
   …")`, surfaced in the summary and `--json`. It is a reasoned, named exclusion — never
   a silent drop — and is self-tested (`--tools claude,codex --scenarios timeline`
   names `codex`). It is distinct from a `fail` (does not by itself force nonzero exit),
   matching "fail **or** exclude."

## Gemini (COMMENT) — both points accepted

3. **Phase 1 AC depended on Phase 2's `__main__.py`.** *Accepted — fixed.* Phase 1's
   self-test now runs via `python -m unittest tests.agent_sessions.selftest.selftest_harness`
   (independent of `--selftest`, which is Phase 2's convenience wrapper over the same
   module). Added `selftest/__init__.py` so the subpackage imports cleanly.

4. **keep-artifacts boundary bypass (`--keep-artifacts .` from repo root).** *Accepted —
   fixed.* `ROOT in path.parents` is `False` when `path == ROOT` (a path is not in its
   own `.parents`). Sealed: the condition is now `path == ROOT or ROOT in path.parents`
   (preferring `path.is_relative_to(ROOT)` where available), with a `.`-from-root case
   and a symlink case added to `selftest_runner`.

## Claude (APPROVE) — minor notes folded in

- **`selftest/` subpackage needs `__init__.py`** and explicit module loading — added the
  `__init__.py` deliverable; `--selftest` uses `loadTestsFromModule`.
- **`selftest_runner` subprocess must inherit repo-root `cwd`/`sys.path`** — stated
  explicitly in Phase 2's Test Plan.
- **Phase 4 codex `--sandbox`-before-`resume` argv pin** — already in the plan; retained.

## Net changes
Phases 1–2 deliverables, Acceptance Criteria, Implementation Details, and Test Plans
updated; Expert Review and Change Log entries added. No phase added/removed; no scope
change. No reviewer point declined.
