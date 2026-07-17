# experiment-31 thread — Harness for testing ai-observe against real agent sessions

Driving issue: #31 (EXPERIMENT protocol, soft mode). Gate: `experiment-complete`.

## Context established (hypothesis phase)
- `ai-observe` shim at `bin/ai-observe`; wraps `-- <cmd...>`, only flag is `--session`.
- Config via env: `AI_OBSERVE_DIR` (artifact dir), `AI_OBSERVE_ROOTS` (watched roots, default cwd),
  `AI_OBSERVE_BACKENDS` (`strace`/`snapshot`), `AI_OBSERVE_DISABLE`.
- Artifacts: `<session>.trace`, `.jsonl` (canonical), `.meta.json` sidecar; `.partial`/`.rebuilt` in degraded runs.
- Events carry provenance: `source` (strace|snapshot) + `confidence` (direct|inferred).
- Viewer: `ai-observe-viewer <jsonl>` / `python -m ai_observe.viewer`, `--port` (default 7878), `--no-browser`.
  Loopback-only. HTTP: `GET /session` (sanitized JSON), `GET /events` (SSE, sanitized). Strips argv/raw_syscall/pids.
- Tools present on this machine: claude, agy, codex, tmux, python3, node. strace present. ptrace_scope=1 (descendant tracing OK).
- Smoke test PASSED: observed a bash write+rm inside watched root → direct (strace) + inferred (snapshot) events emitted.

## Plan
- Exp 1: establish the driving mechanism (feasibility). Compare non-interactive `-p`/`exec` vs tmux send-keys;
  viewer monitoring via HTTP-poll of /session+/events vs headless browser. Build minimal reusable harness.
- Exp 2+: scenario coverage reusing harness; cross-tool (claude/agy/codex) comparison; coverage matrix + recommendation.

## Log
- hypothesis: environment verified, ai-observe end-to-end smoke passed. Proceeding to build Exp 1 harness.
- Exp 1 COMPLETE (commit f70f8d7). Non-interactive driving works for all 3 tools. Reusable harness.py built
  (run_observed_session + raw-socket SSE ViewerMonitor). HEADLINE FINDING: codex runs under a mount-namespace
  sandbox; its mkdir uses /newroot/... paths while rmdir uses canonical → ai-observe drops the mkdirs and
  reports 54 deletes / 0 creates for a task whose net effect was one file created. Snapshot layer stayed correct;
  layer disagreement = the diagnostic. Cross-tool volume: claude 4, agy 4, codex 59 events for the same task.
  Raw artifacts (.trace/.jsonl/.meta.json) gitignored (large+sensitive); curated summary committed.
- Exp 2 COMPLETE (commit d68c8f3): coverage matrix subprocess/ephemeral/modify × 3 tools. FINDING F2 (bug):
  relative unlinkat(AT_FDCWD<dir>, ...) deletions silently dropped → claude/agy file deletions vanish from
  the report. Root-caused in trace_parser _at_path (annotated AT_FDCWD<...> mistaken for real dirfd). Process-
  tree scoping works (grandchild writes captured). codex marker-dir delete noise on every scenario.
- Exp 3 COMPLETE (commit 2b132e2): interrupt/recovery. Mid-session SIGINT → accurate partial capture, clean
  authoritative .jsonl; .partial/.rebuilt NOT triggered by clean signal. Early-interrupt startup race can leave
  empty/absent record. This is the positive counterpoint (F4).
- FINDINGS.md written (coverage matrix + F1/F2/F3/F4 + graduation rec: graduate harness to maintained test
  module, keep live runs opt-in/out-of-default-CI, file F1+F2 as ai-observe bugs).
- EXPERIMENT COMPLETE. Reaching experiment-complete gate → notifying architect. Two real ai-observe bugs found
  (F1 codex /newroot path-filter asymmetry; F2 unlinkat delete drop) + F4 recovery validated positive.
