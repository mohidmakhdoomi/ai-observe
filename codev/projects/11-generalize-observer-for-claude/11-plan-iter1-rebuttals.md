# Rebuttal: Plan iteration 1

## Review summary

- Codex requested changes because the plan did not explicitly protect the live-trace compatibility surface in `tests/test_live_trace.py`.
- Claude approved the plan and provided non-blocking suggestions about preserving default observe-dir discovery and clarifying parameterization.

## Responses to REQUEST_CHANGES

### 1. Existing `tests/test_live_trace.py` compatibility was underrepresented

Accepted. I inspected `tests/test_live_trace.py` and confirmed it directly depends on `ai_observe.codex_observe` internals including `LiveTracer`, `_live_enabled`, `_live_poll_seconds`, `_live_join_timeout`, `safe_open_trace_read`, `safe_append_jsonl_handle`, `safe_write_jsonl`, and monkeypatchable module attributes.

I updated Phase 1 success criteria to require the `codex_observe` compatibility facade to preserve or deliberately re-export the full current test-facing API, including the live tracer class, safe file helpers, resolver helpers, env knob helpers, and process-management helpers.

### 2. Phase 1 should explicitly require full facade preservation

Accepted. The Phase 1 success criteria now list the minimum compatibility API that must remain available from `ai_observe.codex_observe`, rather than only naming `main/run` and a partial helper list.

### 3. Test plan should explicitly run `tests.test_live_trace`

Accepted. I added `python3 -m unittest tests.test_live_trace` to Phase 1 and Phase 3 test commands. I also added Phase 3 success criteria requiring live-mode compatibility for the facade, including monkeypatchable behavior used by current tests.

### 4. Live-parse-related aliases should be called out specifically

Accepted. Phase 3 already named live parse, live poll/join settings, strict parse, and related shared variables; I strengthened it with a dedicated live-mode compatibility success criterion. This makes live-parse alias and behavior regressions a primary target rather than something only covered by full discovery.

## Responses to non-blocking Claude feedback

### Preserve `.codev/observe` ancestor-search semantics

Accepted. I added a Phase 1 success criterion that default observe-dir discovery remains unchanged and tool-agnostic: when no env var overrides the directory, the wrapper searches upward for `.codev` and uses `.codev/observe`.

### Clarify parameterization strategy

Partially accepted. The existing plan intentionally leaves implementation choice flexible, but the expanded facade and resolver criteria should be sufficient for a builder to select a program-name parameter, callback, or equivalent strategy while preserving required behavior. No further plan change was needed.

## Result

The plan has been updated to address all requested changes. No disagreements.
