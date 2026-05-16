# ai-observe command filesystem observer

`ai-observe` is a Linux-first filesystem mutation observer for commands launched through its wrapper. It runs the real command under `strace -f`, follows the child process tree, and writes filesystem mutation events as JSONL.

Named AI-tool shims are convenience wrappers around the same generic backend. The observer does **not** parse Codex, Claude Code, Gemini CLI, or OpenCode transcripts; it observes local Linux syscalls from the launched process tree.

**Severe sensitive-data risk:** `.trace` and `.jsonl` logs can contain absolute paths, command arguments, and raw syscall text. These may include secrets, prompts, file names, and tool-generated temporary paths. Store `.codev/observe/` carefully. Redaction is not implemented.

## Quick start: named shims

Resolve the real binary before placing this repository's `bin/` directory before it in `PATH`, or set an absolute real-binary environment variable explicitly.

```bash
# Codex
export AI_OBSERVE_REAL_CODEX="/absolute/path/to/real/codex"
export PATH="$PWD/bin:$PATH"
codex "implement feature"

# Claude Code
export AI_OBSERVE_REAL_CLAUDE="/absolute/path/to/real/claude"
export PATH="$PWD/bin:$PATH"
claude -p "summarize this repo"

# Gemini CLI
export AI_OBSERVE_REAL_GEMINI="/absolute/path/to/real/gemini"
export PATH="$PWD/bin:$PATH"
gemini -p "summarize this repo"

# OpenCode
export AI_OBSERVE_REAL_OPENCODE="/absolute/path/to/real/opencode"
export PATH="$PWD/bin:$PATH"
opencode run "implement feature"
```

For Codex compatibility, `CODEV_OBSERVE_REAL_CODEX` still works during the compatibility window:

```bash
export CODEV_OBSERVE_REAL_CODEX="/absolute/path/to/real/codex"
export PATH="$PWD/bin:$PATH"
codex "implement feature"
```

Do not set a real-binary variable with `command -v codex`, `command -v claude`, etc. after this repository's shim already shadows the real tool. Resolve the real binary first, or use a known absolute path.

## Quick start: arbitrary commands

Use the generic entry point when you do not want a named shim:

```bash
bin/ai-observe --session my-run -- python -c 'from pathlib import Path; Path("x").write_text("y")'
bin/ai-observe -- bash -lc 'echo hi > generated.txt'
```

If you need to force the real executable while preserving arguments, use `AI_OBSERVE_REAL_COMMAND`:

```bash
AI_OBSERVE_REAL_COMMAND=/opt/tools/tool-real bin/ai-observe -- tool arg1 arg2
```

The first token after `--` (`tool` above) is still required for usage validation and diagnostics. `AI_OBSERVE_REAL_COMMAND` replaces only `argv[0]`; the traced/recorded command becomes:

```json
["/opt/tools/tool-real", "arg1", "arg2"]
```

## Runtime requirements

- Linux.
- Python 3 standard library.
- `strace` 5.10+ or compatible output.
- ptrace policy allowing the wrapper to trace its child process tree. Normal `kernel.yama.ptrace_scope=1` works for tracing direct children.

The wrapper uses argv arrays, not shell interpolation. Internally it runs:

```bash
strace -f -qq -ttt -s 4096 -yy -o <trace-file> -e trace=%file,%desc,%process <real-command> <args...>
```

## Environment variables

Preferred names are `AI_OBSERVE_*`. Existing `CODEV_OBSERVE_*` aliases remain for backwards compatibility where listed. If both are set, `AI_OBSERVE_*` wins.

| Preferred variable | Legacy alias | Purpose |
| --- | --- | --- |
| `AI_OBSERVE_REAL_CODEX` | `CODEV_OBSERVE_REAL_CODEX` | Real Codex executable for `bin/codex`. |
| `AI_OBSERVE_REAL_CLAUDE` | none | Real Claude Code executable for `bin/claude`. |
| `AI_OBSERVE_REAL_GEMINI` | none | Real Gemini CLI executable for `bin/gemini`. |
| `AI_OBSERVE_REAL_OPENCODE` | none | Real OpenCode executable for `bin/opencode`. |
| `AI_OBSERVE_REAL_COMMAND` | none | Forced executable for generic `bin/ai-observe -- command args...`; replaces only command `argv[0]`. |
| `AI_OBSERVE_DIR` | `CODEV_OBSERVE_DIR` | Log directory. Relative paths resolve from launch cwd. If unset, searches upward for `.codev` and uses `.codev/observe`; otherwise `$PWD/.codev/observe`. |
| `AI_OBSERVE_DISABLE=1` | `CODEV_OBSERVE_DISABLE=1` | Bypass tracing and exec the resolved real command. |
| `AI_OBSERVE_SESSION_ID` | `CODEV_OBSERVE_SESSION_ID` | Requested session id. Unsafe filename chars become `_`; empty, `.` and `..` are rejected. |
| `AI_OBSERVE_STRICT_PARSE=1` | `CODEV_OBSERVE_STRICT_PARSE=1` | Parser failure makes wrapper exit nonzero after the real command exits. |
| `AI_OBSERVE_INCLUDE_LOG_WRITES=1` | `CODEV_OBSERVE_INCLUDE_LOG_WRITES=1` | Include active trace/JSONL artifact paths if the traced command touches them. |
| `AI_OBSERVE_ALLOW_SYMLINK_DIR=1` | `CODEV_OBSERVE_ALLOW_SYMLINK_DIR=1` | Allow symlink final observe dir. |
| `AI_OBSERVE_QUIET=1` | `CODEV_OBSERVE_QUIET=1` | Suppress sensitive-log warning. |
| `AI_OBSERVE_LIVE_PARSE=0` | `CODEV_OBSERVE_LIVE_PARSE=0` | Opt out of live-mode streaming; events still land in `.jsonl` post-hoc. Default is live parsing on. |
| `AI_OBSERVE_LIVE_POLL_MS` | `CODEV_OBSERVE_LIVE_POLL_MS` | Live tailer poll interval in milliseconds when no new trace bytes are available. Default `200`, bounds `[10, 2000]`. |
| `AI_OBSERVE_LIVE_JOIN_TIMEOUT` | `CODEV_OBSERVE_LIVE_JOIN_TIMEOUT` | Seconds to wait for live tailer drain after strace exits. Default `30`, bounds `[0.1, 600]`. |
| `AI_OBSERVE_SIGNAL_GRACE` | `CODEV_OBSERVE_SIGNAL_GRACE` | Seconds to wait between forwarded termination signals before escalation. Default `2`. |

## Real executable lookup

### Named shims

For `codex`, `claude`, `gemini`, and `opencode`, lookup order is:

1. `AI_OBSERVE_REAL_<PROGRAM>` if set and executable, for example `AI_OBSERVE_REAL_CLAUDE`.
2. For Codex only, legacy `CODEV_OBSERVE_REAL_CODEX` if set and executable.
3. First matching `<program>` in `PATH` whose resolved path differs from the current shim.
4. Adjacent `<program>.real` or `<program>.bin` beside the shim.
5. Exit `127` with an actionable error.

### Generic `ai-observe`

Generic mode requires `--` and a command token:

```bash
bin/ai-observe [--session SESSION] -- command [args...]
```

Resolution order is:

1. `AI_OBSERVE_REAL_COMMAND` if set and executable; replaces only `argv[0]`.
2. Explicit command path containing `/`, resolved relative to cwd if needed.
3. First matching command in `PATH` that is not recognized as an ai-observe shim.
4. Exit `127` if not found or only recursive observer shims are found.

## Logs

Default location:

```text
.codev/observe/<session-id>.trace
.codev/observe/<session-id>.jsonl
```

Name collision uses deterministic suffixes: `session-1`, `session-2`, etc. No overwrite. No-mutation sessions still create empty `.jsonl` files.

While the traced command runs, the wrapper tails its `.trace` file from a background thread and appends parsed events to `.jsonl` as they land. Another shell can stream events live:

```bash
tail -F .codev/observe/<session-id>.jsonl
```

Latency is approximately `AI_OBSERVE_LIVE_POLL_MS` (default 200 ms) plus parser cost under normal load. The raw `.trace` remains the durable record. Set `AI_OBSERVE_LIVE_PARSE=0` to disable live streaming and parse post-hoc.

For a browser treemap/table view of the same JSONL stream, see [Browser viewer for observer JSONL](viewer.md).

On any non-`ParserFailure` error from the live tailer, the wrapper prints a stderr warning, rebuilds `.jsonl` from the full `.trace` after the real command exits, and preserves the real command's exit code unless `AI_OBSERVE_STRICT_PARSE=1`. If the tailer thread fails to exit within `AI_OBSERVE_LIVE_JOIN_TIMEOUT`, the wrapper leaves `.jsonl` in its partial state, prints a timeout warning, and applies the same strict-mode rule; no `.jsonl.partial` is written in that branch.

On parser failure:

```text
.codev/observe/<session-id>.jsonl.partial
```

contains parsed events so far, or an empty file.

Permissions are best effort: the wrapper creates observe dirs as `0700` and artifacts as `0600`. Existing dirs are not widened. Non-POSIX filesystems may not enforce these modes.

## JSONL schema

One event per line; `schema_version` remains `1`:

```json
{
  "schema_version": 1,
  "timestamp": "2026-05-05T18:00:00.000000Z",
  "session_id": "20260505T180000Z-12345-abcd",
  "invocation_id": "20260505T180000Z-12345-abcd",
  "pid": 12346,
  "process": { "pid": 12346, "ppid": 12345, "comm": null },
  "operation": "modify",
  "path": "/abs/path/file.txt",
  "old_path": null,
  "new_path": null,
  "command": ["/real/path/claude", "-p", "edit file"],
  "raw_syscall": "write(3</abs/path/file.txt>, \"x\", 1) = 1",
  "result": 1
}
```

**Severe sensitive-data risk:** `command` and `raw_syscall` can contain secrets. JSONL is audit output, not safe telemetry. The browser viewer intentionally does not send `command`, `raw_syscall`, process details, or PID fields to the page.

`path`, `old_path`, and `new_path` are absolute strings when resolved, else `null`. No sentinel strings.

Operations:

- `create`: strong creation evidence like `creat`, `O_CREAT|O_EXCL`, `mkdir*`, `mknod*`, `symlink*`, `link*` destination.
- `modify`: positive-byte writes/splices to known writable fd, `O_TRUNC`, `truncate*`, `ftruncate`, parsed `fallocate`.
- `delete`: `unlink*`, `rmdir`.
- `rename`: `rename*`; partial old/new resolution allowed.
- `chmod`: `chmod*`, `fchmod*`.
- `metadata`: `chown*`, `utime*`, `utimensat`, `futimesat`.

Failed syscalls emit no event. Zero-byte writes emit no event. Events are not coalesced.

## Backend tradeoffs

Chosen backend: `strace`.

Why:

- No root required for tracing own child on normal Linux systems.
- `-f` follows the launched process tree.
- Captures syscall PID and raw path/fd details.
- Works in the current repo/test setup with Python stdlib.

Rejected for initial scope:

- `inotify`: low overhead, but cannot attribute events to a launched process tree.
- `fanotify`: stronger watching, but permission and attribution complexity too high.
- `eBPF`: powerful, but kernel/capability/version complexity too high.
- `auditd`: privileged system service and host-state changes.

## Scope and fidelity limits

The observer is mostly generic for Linux programs launched as children of the wrapper, but it is not universal.

Process-tree and environment limits:

- Linux only; no macOS, Windows, or BSD backend in this version.
- Requires `strace` plus ptrace/seccomp/Yama policy allowing child tracing.
- Does not capture edits made by already-running external helper processes outside the traced tree.
- Does not capture filesystem changes performed by remote services or hosted agents unless those changes occur locally through the traced process tree.
- Does not capture editor/IDE extension edits unless the editor/extension process is in the traced tree.
- TUI programs, background daemons, sandboxed tools, privilege transitions, containers, and remote-control modes may work only partially.
- `strace` adds overhead and can perturb timing-sensitive sessions.

Filesystem/parser limits:

- `mmap` writes may be missed.
- Plain `open(..., O_CREAT)` without `O_EXCL` is not logged as `create`; later writes become `modify`.
- Atomic-save patterns appear as temp create/modify plus rename.
- Symlink paths are logged as used by the process, not guaranteed real targets.
- Hardlink writes log only the path used by the syscall.
- Deleted-open files may resolve as `null` if fd path is unavailable.
- `fchdir`, inherited fds, fd reuse, and cwd races are best effort.
- `io_uring`, `copy_file_range`, and `sendfile` may be missed or under-attributed. `splice` is counted when the destination is a known writable file descriptor.
- Wrapper runs traced commands in a separate process group, forwards SIGINT/SIGTERM/SIGQUIT plus interactive terminal signals SIGWINCH/SIGTSTP/SIGCONT where available, and escalates termination after a short grace period.

## Troubleshooting

- **Recursion / wrong binary**: set `AI_OBSERVE_REAL_<PROGRAM>` to an absolute real executable path before prepending `bin/` to `PATH`. For Codex, legacy `CODEV_OBSERVE_REAL_CODEX` also works.
- **Generic wrapper runs another shim**: set `AI_OBSERVE_REAL_COMMAND=/absolute/path/to/tool`, or call the real executable by absolute path.
- **Missing `strace`**: install `strace`, or set `AI_OBSERVE_DISABLE=1` to bypass tracing (`CODEV_OBSERVE_DISABLE=1` remains a legacy alias).
- **Ptrace denied**: check sandbox/seccomp/Yama policy.
- **Unwritable observe dir**: set `AI_OBSERVE_DIR` to a writable local path.
- **Symlink observe dir rejected**: use a real directory or set `AI_OBSERVE_ALLOW_SYMLINK_DIR=1` knowingly.
- **Parser partial output**: inspect `.trace` and `.jsonl.partial`; retry with current code or set `AI_OBSERVE_STRICT_PARSE=1` if parser failure must block.
- **Generic CLI usage error**: use `bin/ai-observe [--session SESSION] -- command [args...]`; the `--` separator is required.
