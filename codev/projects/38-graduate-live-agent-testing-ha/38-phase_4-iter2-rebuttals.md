# Phase 4 — Rebuttal to impl iter 2 review

Verdicts: **Gemini APPROVE**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH
confidence). Codex's single point accepted and fixed.

## Codex (REQUEST_CHANGES)

1. **Timeline probe drops wrapper stderr → the promised `--keep-artifacts` inspection
   path doesn't work for S6 (`probes.py:49-51`, `__main__.py:111-115`).**
   *Accepted — fixed.* Real gap: `sample_timeline` uses a non-blocking `Popen` (it must
   sample the viewer *while* the run proceeds), so it can't reuse the blocking core's
   `capture_output` the way `run_observed_command` does — and it was sending
   `stdout/stderr` to `DEVNULL`. When claude is unauthenticated/errors on a timeline-only
   selection, `check_timeline` raises `ToolUnusable` and the runner tells the user to
   "rerun with `--keep-artifacts` to inspect stderr" — but nothing was persisted, so even
   with `--keep-artifacts` there was no stderr to read.

   **Change (`probes.py`):** the wrapper's `stdout`/`stderr` are now redirected to
   `<session>.stdout.log` / `<session>.stderr.log` **inside the scenario `outdir`** —
   which is exactly the subtree `--keep-artifacts` preserves, sitting next to the
   `.jsonl`. Handles are opened before `Popen`, and closed in a `finally` after
   `proc.wait()`/`kill()` so the stderr is flushed and readable. The report dict now
   carries `stdout_log`, `stderr_log`, and a bounded `stderr_tail` (last 800 chars). On
   the default auto-cleaning temp dir these logs vanish with the rest of the artifacts
   (Decision 7); under `--keep-artifacts` they survive for inspection — the runner's
   message is now truthful.

   **Change (`check_timeline.py`):** the M4 gate now wraps `ensure_tool_usable` and, on
   `ToolUnusable`, appends the persisted `stderr_tail` (last 400 chars) to the failure
   `detail`. So the *reason* a timeline run failed is visible **inline** in the JSON/
   summary output — not only on disk under `--keep-artifacts`. Both halves of the
   loud-fail / debuggability contract (Decision 4) are now honored.

   No live test was added for this (the fix is a live-tier behavior; the `--selftest`
   tier is deliberately tool-free and runnable without any agent installed, mirroring
   how the blocking core's `stderr_tail` is also validated only in the live tier). The
   change is import-clean and `--selftest` stays 44/44.

## Gemini / Claude (APPROVE)
No changes requested. Both confirmed the Exp-4 multi-turn chained driver (with the codex
`--sandbox`-before-`resume` argv pin), the Exp-9 timeline probe, the deterministic #33
flip-home, and the tool-free self-tests.

## Net changes
`probes.py`: wrapper streams persisted to `outdir` logs (not `DEVNULL`); report adds
`stdout_log`/`stderr_log`/`stderr_tail`. `check_timeline.py`: `ToolUnusable` failure now
carries the stderr tail inline. `--selftest` 44/44 green. No reviewer point declined.
