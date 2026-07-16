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
