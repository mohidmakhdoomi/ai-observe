# Phase 1 — Rebuttal to impl iter 1 review

Verdicts: **Gemini APPROVE**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH
confidence). Both Codex points accepted and fixed.

## Codex (REQUEST_CHANGES)

1. **`ViewerMonitor.start()` didn't catch `ViewerServer(...)` *construction* failures
   (`harness.py`).** *Accepted — fixed.* `ViewerServer.__init__` binds the listening
   socket, so construction itself can raise (Codex saw a `PermissionError` on their
   restricted host before the `try`, so the self-test errored instead of `start()`
   returning `False`). Moved the `ViewerServer(...)` construction **inside** the `try`
   so any startup failure — construction or serve — normalizes into the boolean API the
   callers and self-tests rely on. (This environment could already bind `127.0.0.1:0`,
   which is why the self-test passed here; the fix makes the contract honest on
   restricted hosts too.)

2. **`collect_events()` hardcoded `127.0.0.1` instead of parsing the host from
   `server.url` (`harness.py`).** *Accepted — fixed.* Now derives both host and port
   from `urlsplit(self.url)` (`parts.hostname` / `parts.port`), with a `127.0.0.1` /
   `self.port` fallback — matching the Phase 1 deliverable's "parsing `server.url` for
   host/port" wording. Behaviorally identical against the current server (which binds
   loopback) but aligned with the approved design and robust if the bound host ever
   changes.

## Gemini / Claude (APPROVE)

No changes requested. Both independently re-ran the self-test (4/4 pass) and confirmed:
ephemeral-port distinctness, checkout-first `resolve_ai_observe`, the F5/Decision-11
sequencing (session first, viewer attaches only once `.jsonl` exists and is non-empty),
`src/`-only `sys.path` scoping, and no hard-coded port constants. Claude's one
non-blocking note (the implicit `self.url + "session"` join relying on the trailing `/`)
is fine as-is; the `urlsplit` change above keeps the events path derivation explicit.

## Net changes
`tests/agent_sessions/harness.py` only: `start()` construction now inside the failure
`try`; `collect_events()` host/port via `urlsplit(self.url)`; added `urlsplit` import.
Self-test re-run: 4/4 green. No reviewer point declined.
