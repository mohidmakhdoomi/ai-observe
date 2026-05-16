# Rebuttal: Specify iteration 1

## Review summary

- Codex requested changes focused on tightening generic command semantics, recursion avoidance, concrete delivery artifacts, and compatibility tests.
- Claude approved the spec and echoed two minor clarifications: `bin/ai-observe` should be the concrete first-iteration entry point, and invocation without `--` should have defined usage behavior.

## Responses to REQUEST_CHANGES

### 1. `AI_OBSERVE_REAL_COMMAND` semantics were ambiguous

Accepted. I updated the spec to state that:

- the first token after `--` is still required;
- that token is treated as the requested/display command name for validation and diagnostics;
- `AI_OBSERVE_REAL_COMMAND` replaces only `argv[0]` for execution and for the JSONL `command` array;
- all remaining tokens after the requested command token are preserved as arguments.

I also added a concrete example showing that:

```bash
AI_OBSERVE_REAL_COMMAND=/opt/tools/tool-real ai-observe -- tool args...
```

is executed and recorded as:

```json
["/opt/tools/tool-real", "args..."]
```

### 2. Generic recursion avoidance was underspecified

Accepted. I updated the spec to require generic mode to avoid accidentally resolving to `bin/ai-observe` itself or observer-provided named shims such as `bin/codex`, unless the user explicitly points at a distinct real executable. I also added a test scenario for rejecting or skipping recursive generic targets.

### 3. Generic entry point delivery was too loose

Accepted. I changed the requirement from “repository checkout and/or package entry point” to a concrete first-iteration requirement: `bin/ai-observe` must exist and work from the repository checkout. Optional packaging console scripts remain a COULD/open question, not an acceptance requirement.

### 4. Backward-compatibility scope needed more testable precedence requirements

Accepted. I updated the spec to require explicit preferred-vs-legacy precedence tests for shared variables including disable, observe directory, session id, and quiet mode. I also kept the Codex-specific real-binary compatibility requirement.

## Responses to non-blocking Claude feedback

### Invocation without `--`

Accepted. I added a MUST requirement and test scenario that `bin/ai-observe` invoked without `--` or without a command prints usage/help, exits nonzero, and does not run `strace` or a child command.

### Internal test-only env vars

Acknowledged. I added a short compatibility note that internal test-only controls may remain undocumented user-facing behavior, but tests should retain a deterministic way to exercise parser-failure branches.

## Result

The spec has been updated to resolve all requested changes. No disagreements.
