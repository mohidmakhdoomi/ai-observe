# Specification: Generalize ai-observe command observer

## Summary

`ai-observe` currently presents itself and its public wrapper code as a Codex-specific shim: `bin/codex`, `ai_observe.codex_observe`, and `CODEV_OBSERVE_REAL_CODEX`. The underlying backend, however, observes a Linux child process tree with `strace -f`; it is not tied to Codex internals. This feature generalizes the product from a Codex wrapper into a command-oriented filesystem observer that can wrap Codex, Claude Code, Gemini CLI, OpenCode, and arbitrary user-provided programs where the Linux/ptrace backend can trace the launched process tree.

The public positioning should become: **a generic Linux filesystem mutation observer for commands launched through the wrapper**. Named AI-tool shims are convenience entry points, not separate integrations.

## Background and current state

### Existing behavior

- `bin/codex` invokes Python code in `ai_observe.codex_observe`.
- The wrapper resolves a real Codex executable, launches it under `strace`, parses the raw trace, and writes `.trace` and `.jsonl` artifacts.
- The parser already records command metadata as the actual executable path and argv used for the traced process.
- Log safety controls, session-id sanitization, live parsing, parser-failure handling, signal forwarding, and viewer support already exist.
- Existing docs call the feature a “Codex filesystem observer” and all public environment variables use the `CODEV_OBSERVE_*` prefix.

### Desired state

- The core wrapper module and user docs are tool-agnostic.
- The repository exposes a concrete `bin/ai-observe` command path that can run any supported command via:

  ```bash
  ai-observe --session my-run -- command arg1 arg2
  AI_OBSERVE_REAL_COMMAND=/absolute/path/to/tool ai-observe -- tool args...
  ```

- Convenience shims exist for:
  - `codex`
  - `claude`
  - `gemini`
  - `opencode`
- New public configuration prefers `AI_OBSERVE_*` names.
- Existing Codex workflows continue to work, including `bin/codex`, `CODEV_OBSERVE_REAL_CODEX`, and backwards-compatible imports/entry points where reasonable.

## External CLI compatibility context

The target tools are command-line programs that can be launched as child processes:

- Claude Code starts interactive sessions with `claude`, accepts an initial prompt, and supports print/query mode via `claude -p "query"` and piped input. Source: https://code.claude.com/docs/en/cli-usage
- Gemini CLI supports interactive and non-interactive/headless usage, including piped input and `gemini -p "query"`. Source: https://google-gemini.github.io/gemini-cli/docs/cli/
- OpenCode starts the TUI with `opencode` and supports commands such as `opencode run "..."`. Source: https://dev.opencode.ai/docs/cli/

These facts support named shims, but the observer must not depend on parsing any of these tools' transcripts or internal session formats.

## Key product question: can this work for any program?

**Answer: mostly yes, when “any program” means a Linux executable launched by `ai-observe` whose relevant filesystem mutations occur in the launched process tree.**

The backend observes syscalls from the traced process and its descendants, so it is naturally program-agnostic. It can cover compiled binaries, scripts, Python/Node tools, AI CLIs, build tools, and shell commands launched under the wrapper.

It is not universal. The first iteration must explicitly document these limits:

- Linux only; no macOS, Windows, or BSD backend in scope.
- Requires `strace` and ptrace/seccomp/Yama policy that allows tracing the child process tree.
- Does not capture edits made by already-running external helper processes outside the traced tree.
- Does not capture filesystem changes performed by remote services or hosted agents unless they occur on this machine through the traced process tree.
- Does not capture editor/IDE extension edits unless that editor/extension process was launched under the wrapper or is otherwise in the traced tree.
- TUI programs, background daemons, sandboxed tools, privilege transitions, containers, and remote-control modes may work partially or require clear caveats.
- Existing parser fidelity limits remain, including mmap writes and some advanced kernel/file-copy mechanisms.

## Goals

1. Reframe `ai-observe` as a generic command observer while preserving existing Codex behavior.
2. Provide named shims for Codex, Claude Code, Gemini CLI, and OpenCode.
3. Provide a truly generic command entry point for arbitrary commands.
4. Generalize real-executable resolution and recursion avoidance.
5. Preserve JSONL schema compatibility for existing consumers.
6. Add tests covering named shim resolution and generic command mode.
7. Update docs with usage, security warnings, and limitations for each supported path.

## Non-goals

- Non-Linux tracing backends.
- Remote hosted-agent or service-side filesystem observation.
- Deep parsing of Codex, Claude Code, Gemini CLI, or OpenCode session/transcript formats.
- Redaction, anonymization, or safe telemetry export.
- Replacing `strace` with inotify, fanotify, eBPF, or auditd in this iteration.
- Behavioral guarantees for arbitrary programs that intentionally evade tracing, drop privileges in incompatible ways, or offload all edits to untraced services.

## Stakeholders and needs

- **Developers using AI coding CLIs** need a consistent way to observe filesystem mutations regardless of which CLI they use.
- **Codev / ai-observe maintainers** need one core wrapper implementation rather than tool-specific copies.
- **Viewer and JSONL consumers** need schema stability so existing workflows do not break.
- **Security-conscious users** need prominent warnings that traces and JSONL can contain sensitive data.
- **Test maintainers** need resolver behavior that can be exercised without real third-party AI CLIs installed.

## Functional requirements

### MUST

- Expose a generic observer module/CLI whose naming is not Codex-specific.
- Keep `bin/codex` working as a compatibility shim.
- Add executable shims:
  - `bin/claude`
  - `bin/gemini`
  - `bin/opencode`
- Add a generic `bin/ai-observe` command path, available from the repository checkout, that accepts a command after `--`.
- Generic `ai-observe` invocation without `--` and without a command must print usage/help and exit nonzero without running `strace` or a child command.
- For named shims, resolve the real executable without recursion using this priority:
  1. `AI_OBSERVE_REAL_<PROGRAM>` if set and executable.
  2. Legacy `CODEV_OBSERVE_REAL_CODEX` for the Codex shim.
  3. First matching `<program>` in `PATH` whose resolved path is not the wrapper/shim path.
  4. Adjacent `<program>.real` or `<program>.bin` beside the shim.
  5. Exit `127` with an actionable error if no real executable is found.
- For generic command mode, support direct commands after `--` and avoid accidentally resolving to the wrapper itself or to one of the observer shims.
- Support `AI_OBSERVE_REAL_COMMAND` for generic mode when users want to force the real executable path while preserving command arguments. In this mode the first token after `--` is still required and is treated as the display/requested command name; `AI_OBSERVE_REAL_COMMAND` replaces only `argv[0]` for execution and JSONL `command`, while tokens after that first command token remain arguments.
- Prefer `AI_OBSERVE_*` environment variables in docs and new code paths.
- Keep compatible aliases for existing `CODEV_OBSERVE_*` variables for at least one release.
- Preserve existing observe-dir/session/log behavior, including collision suffixes, safe artifact creation, live parsing, parser-failure partial output, and signal forwarding semantics unless a deliberate compatibility note is documented.
- Preserve JSONL `schema_version: 1` compatibility. Existing fields, types, and meanings must remain valid.
- Record the traced real executable and argv in the existing `command` field.
- If additional wrapper metadata is added, it must either fit compatibly in schema version 1 without breaking consumers or be deferred until deliberate schema versioning is planned.
- Keep severe sensitive-data warning prominent in docs and stderr unless quiet mode is set.
- Document process-tree scope and ptrace/sandbox requirements.
- Add resolver tests for `codex`, `claude`, `gemini`, `opencode`, and generic arbitrary command mode.
- Existing tests for Codex observation and viewer behavior must continue to pass.

### SHOULD

- Keep backwards-compatible import path `ai_observe.codex_observe` as a thin adapter to the generalized implementation.
- Use error prefixes/messages that are generic (`ai-observe:`) for new code while avoiding unnecessary churn in compatibility tests where feasible.
- Offer `AI_OBSERVE_DISABLE`, `AI_OBSERVE_DIR`, `AI_OBSERVE_SESSION_ID`, `AI_OBSERVE_STRICT_PARSE`, `AI_OBSERVE_INCLUDE_LOG_WRITES`, `AI_OBSERVE_ALLOW_SYMLINK_DIR`, `AI_OBSERVE_QUIET`, `AI_OBSERVE_LIVE_PARSE`, `AI_OBSERVE_LIVE_POLL_MS`, and `AI_OBSERVE_LIVE_JOIN_TIMEOUT` as preferred names.
- Keep `CODEV_OBSERVE_*` aliases behaviorally equivalent during the compatibility window, with explicit tests for preferred-vs-legacy precedence on shared variables such as disable, observe directory, session id, and quiet mode.
- Make command resolution testable as pure functions without requiring real AI tools.
- Keep docs examples copy-pasteable and avoid shell interpolation hazards.

### COULD

- Add an optional, schema-compatible field such as `observed_program` only if implementation explicitly confirms downstream viewer/parser compatibility.
- Add a migration note for eventual removal of `CODEV_OBSERVE_*` aliases.
- Add package console scripts if packaging metadata exists or is introduced cleanly.

## User experience

### Named shims

```bash
# Resolve the real binary before putting ai-observe shims first in PATH.
export AI_OBSERVE_REAL_CLAUDE=/absolute/path/to/real/claude
export PATH="$PWD/bin:$PATH"
claude "implement feature"

export AI_OBSERVE_REAL_GEMINI=/absolute/path/to/real/gemini
gemini -p "summarize this repo"

export AI_OBSERVE_REAL_OPENCODE=/absolute/path/to/real/opencode
opencode run "implement feature"

export AI_OBSERVE_REAL_CODEX=/absolute/path/to/real/codex
codex "implement feature"
```

### Generic command mode

```bash
ai-observe --session my-run -- python -c 'from pathlib import Path; Path("x").write_text("y")'
AI_OBSERVE_REAL_COMMAND=/absolute/path/to/tool ai-observe -- tool args...
ai-observe -- bash -lc 'echo hi > generated.txt'
```

With `AI_OBSERVE_REAL_COMMAND=/opt/tools/tool-real` and `ai-observe -- tool args...`, the executed and recorded command is:

```json
["/opt/tools/tool-real", "args..."]
```

The `tool` token is still required so the CLI has a requested command name for usage validation and diagnostics, but the forced real executable path replaces that token for the actual traced process.

### Backwards-compatible Codex mode

```bash
export CODEV_OBSERVE_REAL_CODEX=/absolute/path/to/real/codex
export PATH="$PWD/bin:$PATH"
codex "implement feature"
```

Docs should identify this as supported but legacy-preferred-to-migrate, not the primary UX.

## Environment variable compatibility

Preferred `AI_OBSERVE_*` variables should map to existing semantics:

| Preferred variable | Legacy alias | Purpose |
| --- | --- | --- |
| `AI_OBSERVE_REAL_CODEX` | `CODEV_OBSERVE_REAL_CODEX` | Real Codex executable |
| `AI_OBSERVE_REAL_CLAUDE` | none | Real Claude Code executable |
| `AI_OBSERVE_REAL_GEMINI` | none | Real Gemini CLI executable |
| `AI_OBSERVE_REAL_OPENCODE` | none | Real OpenCode executable |
| `AI_OBSERVE_REAL_COMMAND` | none | Forced executable for generic command mode |
| `AI_OBSERVE_DIR` | `CODEV_OBSERVE_DIR` | Observe output directory |
| `AI_OBSERVE_DISABLE` | `CODEV_OBSERVE_DISABLE` | Bypass observer and exec real command |
| `AI_OBSERVE_SESSION_ID` | `CODEV_OBSERVE_SESSION_ID` | Requested session id |
| `AI_OBSERVE_STRICT_PARSE` | `CODEV_OBSERVE_STRICT_PARSE` | Parser failure controls wrapper exit |
| `AI_OBSERVE_INCLUDE_LOG_WRITES` | `CODEV_OBSERVE_INCLUDE_LOG_WRITES` | Include observer log writes |
| `AI_OBSERVE_ALLOW_SYMLINK_DIR` | `CODEV_OBSERVE_ALLOW_SYMLINK_DIR` | Allow symlink observe dir |
| `AI_OBSERVE_QUIET` | `CODEV_OBSERVE_QUIET` | Suppress warning |
| `AI_OBSERVE_LIVE_PARSE` | `CODEV_OBSERVE_LIVE_PARSE` | Enable/disable live parsing |
| `AI_OBSERVE_LIVE_POLL_MS` | `CODEV_OBSERVE_LIVE_POLL_MS` | Live poll interval |
| `AI_OBSERVE_LIVE_JOIN_TIMEOUT` | `CODEV_OBSERVE_LIVE_JOIN_TIMEOUT` | Live parser drain timeout |
| `AI_OBSERVE_SIGNAL_GRACE` | `CODEV_OBSERVE_SIGNAL_GRACE` | Signal escalation grace |

If both preferred and legacy variables are set, the preferred `AI_OBSERVE_*` value should win. Codex real-executable lookup is the special case where both `AI_OBSERVE_REAL_CODEX` and `CODEV_OBSERVE_REAL_CODEX` should be recognized.

Internal test-only controls such as parser failure injection may remain undocumented user-facing behavior, but existing tests should continue to have a deterministic way to exercise those branches.

## JSONL schema compatibility

The current schema must remain valid. In particular:

- `schema_version` remains `1`.
- `command` remains the argv actually passed to `strace` after real executable resolution, beginning with the resolved real executable path.
- Existing fields remain present with existing types.
- Viewer sanitization should not begin exposing sensitive command/raw syscall fields to the browser page.
- Generic wrapper metadata is not required for acceptance. If added, it must not break consumers that ignore unknown fields.

Example event remains conceptually:

```json
{
  "schema_version": 1,
  "session_id": "my-run",
  "invocation_id": "my-run",
  "operation": "modify",
  "path": "/repo/file.txt",
  "command": ["/absolute/path/to/real/claude", "-p", "edit file"],
  "raw_syscall": "write(3</repo/file.txt>, \"x\", 1) = 1"
}
```

## Security requirements

- Docs must retain a prominent severe warning that `.trace` and `.jsonl` artifacts can include secrets in:
  - absolute and relative paths,
  - command arguments,
  - raw syscall text,
  - environment-influenced file names or tool-generated temporary paths.
- The observer must continue using argv arrays rather than shell interpolation for traced command execution.
- Existing log path hardening must be preserved: safe observe dir handling, symlink protections unless explicitly allowed, exclusive artifact creation, and best-effort restrictive permissions.
- The browser viewer must continue to avoid exposing sensitive fields such as `command`, `raw_syscall`, `pid`, and process metadata.

## Non-functional requirements

- **Compatibility:** Existing Codex tests and user workflows continue to pass.
- **Maintainability:** Core tracing logic should live in a generic module to avoid copy/paste wrappers per CLI.
- **Portability declaration:** Linux-only behavior is explicit; unsupported platforms fail clearly.
- **Performance:** Generalization should not materially add overhead beyond existing `strace` overhead and Python parsing.
- **Reliability:** Exit code preservation, signal forwarding, live-parse fallback, and parser strict mode should match current behavior.
- **Testability:** Resolver and generic CLI behavior should be testable with small fake executables and fake `strace`, without network or real AI CLI dependencies.

## Solution approaches considered

### Approach A: Copy Codex wrapper for each tool

Create separate `claude_observe.py`, `gemini_observe.py`, and `opencode_observe.py` modules by copying the existing Codex wrapper and changing resolver names.

Pros:
- Fastest apparent path for named shims.
- Minimal changes to existing Codex code.

Cons:
- Duplicates safety-critical tracing and log code.
- Increases bug-fix surface area.
- Does not naturally support arbitrary commands.
- Keeps product architecture tool-specific.

Risk: high maintainability risk.

### Approach B: Generic wrapper core plus compatibility adapter

Refactor command resolution and wrapper execution into generic code. Named shims pass an observed program name; `ai_observe.codex_observe` remains as a compatibility adapter. Generic `ai-observe` passes an explicit command argv.

Pros:
- Aligns architecture with strace backend reality.
- Supports named shims and arbitrary command mode.
- Allows one implementation for log safety, parser behavior, signal forwarding, and docs.
- Enables broad resolver tests.

Cons:
- Requires careful compatibility handling for existing env vars and imports.
- Existing tests may need updates to generic names without losing coverage.

Risk: moderate refactor risk, manageable with tests.

### Approach C: Generic observer only, no named shims

Provide only `ai-observe -- command args...` and document users should call tools explicitly.

Pros:
- Simplest public API surface.
- Least recursion risk.

Cons:
- Does not meet acceptance criteria for convenient `codex`, `claude`, `gemini`, and `opencode` shims.
- Worse UX for users accustomed to PATH shims.

Risk: fails requirements.

### Preferred approach

Approach B: implement a generic wrapper core, preserve Codex compatibility through adapters and aliases, and add named shims as thin launchers.

## Open questions

### Critical

- None known. The issue text provides sufficient scope and constraints for the first iteration.

### Important

- Should the package also expose `ai-observe` through packaging console-scripts if packaging metadata exists or is added? Acceptance requires `bin/ai-observe`; package entry points are optional.
- What exact deprecation timeline should apply to `CODEV_OBSERVE_*` aliases? This spec requires at least one-release compatibility but does not set a removal release.
- Should new stderr prefixes change from `codex-observe:` to `ai-observe:` everywhere, or should compatibility tests tolerate either during transition?

### Nice-to-know

- Whether users want per-tool default session-id prefixes such as `claude-...` or `gemini-...`. Not required for acceptance.
- Whether future schemas should add explicit `wrapper_program`, `shim_path`, or `observed_program` fields. Not required for this iteration.

## Acceptance criteria

- [ ] A generic observer module/entry path exists and is not Codex-named.
- [ ] `bin/codex` continues to work.
- [ ] `bin/claude`, `bin/gemini`, and `bin/opencode` exist and invoke the generic observer for their program names.
- [ ] A generic `ai-observe` command path supports arbitrary commands after `--`.
- [ ] Real executable lookup implements the required priority and avoids recursion for all named shims.
- [ ] `AI_OBSERVE_REAL_<PROGRAM>` works for Codex, Claude, Gemini, and OpenCode.
- [ ] `CODEV_OBSERVE_REAL_CODEX` remains supported for Codex.
- [ ] `AI_OBSERVE_REAL_COMMAND` works for generic command mode.
- [ ] Existing `CODEV_OBSERVE_*` behavior remains available as backwards-compatible aliases, with `AI_OBSERVE_*` preferred in docs.
- [ ] JSONL schema version remains compatible; existing consumers still parse emitted events.
- [ ] Command metadata records the resolved real executable path and argv.
- [ ] Existing Codex observe tests continue to pass.
- [ ] Tests cover resolver behavior for `codex`, `claude`, `gemini`, `opencode`, and generic command mode.
- [ ] Docs describe the observer generically, with sections for Codex, Claude Code, Gemini CLI, OpenCode, and arbitrary commands.
- [ ] Docs retain prominent security warning.
- [ ] Docs explicitly state ptrace/sandbox requirements and process-tree scope limitations.

## Test scenarios

- Named resolver uses `AI_OBSERVE_REAL_CLAUDE` when set to an executable path.
- Codex resolver prefers `AI_OBSERVE_REAL_CODEX` over `CODEV_OBSERVE_REAL_CODEX` when both are set.
- Codex resolver accepts legacy `CODEV_OBSERVE_REAL_CODEX` when preferred variable is unset.
- Resolver rejects explicit real path if it resolves to the shim itself.
- Resolver skips the shim path in `PATH` and finds the next executable of the same name.
- Generic resolver rejects or skips recursive targets, including `bin/ai-observe` itself and observer-provided named shims such as `bin/codex`, unless the user explicitly points at a distinct real executable.
- Resolver finds adjacent `<program>.real` and `<program>.bin`.
- Generic mode runs command after `--`, preserves argv, writes logs, and records the resolved command in JSONL.
- Generic mode with `AI_OBSERVE_REAL_COMMAND` runs that executable, replaces only the command token, preserves remaining supplied arguments, and records the resulting real command argv in JSONL.
- `AI_OBSERVE_DISABLE=1` bypasses tracing and execs the real command.
- Legacy `CODEV_OBSERVE_DISABLE=1` still works for Codex compatibility.
- Preferred `AI_OBSERVE_*` shared variables override legacy `CODEV_OBSERVE_*` aliases for disable, observe directory, session id, and quiet mode.
- `bin/ai-observe` with no `--` or no command prints usage/help and exits nonzero without running `strace`.
- Existing fake-strace process-tree tests pass for Codex and at least one non-Codex shim or generic mode.
- Missing `strace` returns `127` before running the child command.
- Ptrace-denied empty trace still reports an actionable warning.
- Viewer sanitization remains unchanged for sensitive fields.

## Documentation requirements

`docs/observe.md` should be reframed from “Codex filesystem observer” to “ai-observe command filesystem observer” or equivalent. It must include:

- Quick start for named shims.
- Quick start for generic command mode.
- Environment variable table with preferred and legacy names.
- Real executable lookup order.
- Security warning near the top and near JSONL schema discussion.
- Runtime requirements: Linux, Python, `strace`, ptrace policy.
- Limitations and fidelity boundaries, including process-tree scope, remote services, editor/IDE edits, sandbox/privilege issues, and parser fidelity limits.
- Tool-specific examples for Codex, Claude Code, Gemini CLI, and OpenCode.
- Troubleshooting for recursion, missing `strace`, ptrace denial, unwritable observe dir, symlink observe dir, and parser partial output.

## Migration notes

- Existing users can continue using `bin/codex` and `CODEV_OBSERVE_REAL_CODEX` during the compatibility window.
- New docs should recommend `AI_OBSERVE_REAL_CODEX` and other `AI_OBSERVE_*` variables.
- Existing JSONL consumers do not need migration if they already tolerate unknown fields and rely on schema version 1.
- Users who previously thought the wrapper was Codex-specific should be told it traces the launched process tree, not AI-tool internals.
