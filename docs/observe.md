# Codex filesystem observer

Linux-first wrapper for `codex` that runs real Codex under `strace -f`, then writes filesystem mutation events as JSONL.

## Setup

Put repo shim before real Codex in `PATH`, then point wrapper at real binary:

```bash
export CODEV_OBSERVE_REAL_CODEX="/absolute/path/to/real/codex"
export PATH="$PWD/bin:$PATH"
codex "implement feature"
```

Do not set `CODEV_OBSERVE_REAL_CODEX` with `command -v codex` after shim already shadows real Codex. Resolve real binary before changing `PATH`, or use absolute known path.

**Severe sensitive-data risk:** trace and JSONL logs can contain absolute paths, command arguments, and raw syscall text. These may include secrets. Store `.codev/observe/` carefully. Redaction is not implemented.

## Runtime requirements

- Linux.
- Python 3 standard library.
- `strace` 5.10+ or compatible fixture-defined output.
- ptrace policy allowing parent process to trace child process. Normal `kernel.yama.ptrace_scope=1` works.

Wrapper uses:

```bash
strace -f -qq -ttt -s 4096 -yy -o <trace-file> -e trace=%file,%desc,%process <real-codex> <args...>
```

## Environment variables

- `CODEV_OBSERVE_REAL_CODEX`: executable path to real Codex. Highest priority.
- `CODEV_OBSERVE_DIR`: log directory. If relative, resolved from launch cwd. If unset, wrapper searches upward from launch cwd for `.codev` and uses `.codev/observe`; otherwise `$PWD/.codev/observe`.
- `CODEV_OBSERVE_DISABLE=1`: bypass monitor and exec real Codex.
- `CODEV_OBSERVE_SESSION_ID`: caller session id. Unsafe filename chars become `_`; empty, `.` and `..` rejected.
- `CODEV_OBSERVE_STRICT_PARSE=1`: parser failure makes wrapper exit nonzero after real Codex exits.
- `CODEV_OBSERVE_INCLUDE_LOG_WRITES=1`: include active trace/JSONL artifact paths if Codex touches them.
- `CODEV_OBSERVE_ALLOW_SYMLINK_DIR=1`: allow symlink final observe dir.
- `CODEV_OBSERVE_QUIET=1`: suppress sensitive-log warning.
- `CODEV_OBSERVE_LIVE_PARSE=0`: opt out of live-mode streaming (events still land in `.jsonl` post-hoc). Default is on — events stream live to `.jsonl` while Codex runs.
- `CODEV_OBSERVE_LIVE_POLL_MS`: live tailer poll interval in milliseconds when the trace file shows no new bytes. Default `200`, bounds `[10, 2000]`. Out-of-range or unparseable values fall back to the default.
- `CODEV_OBSERVE_LIVE_JOIN_TIMEOUT`: seconds to wait for the live tailer to drain after strace exits. Default `30`, bounds `[0.1, 600]`. Out-of-range or unparseable values fall back to the default.

## Real Codex lookup

1. `CODEV_OBSERVE_REAL_CODEX` if set and executable.
2. First `codex` in `PATH` whose resolved path differs from wrapper path.
3. `codex.real` or `codex.bin` beside wrapper.
4. Else exit `127`.

Execution uses argv arrays, not shell interpolation.

## Logs

Default location:

```text
.codev/observe/<session-id>.trace
.codev/observe/<session-id>.jsonl
```

Name collision uses deterministic suffix: `session-1`, `session-2`, etc. No overwrite.

No-mutation sessions still create empty `.jsonl`.

### Streaming events

While Codex runs, the wrapper tails its `.trace` file from a background thread and appends parsed events to `.jsonl` as they land. Another shell can stream events live:

```bash
tail -F .codev/observe/<session-id>.jsonl
```

Latency is approximately the value of `CODEV_OBSERVE_LIVE_POLL_MS` (default 200 ms) plus parser cost — under normal load, sub-second. The raw `.trace` is still the durable record on disk. Set `CODEV_OBSERVE_LIVE_PARSE=0` to disable streaming and fall back to the original post-hoc parse.

For a browser treemap/table view of the same JSONL stream, see [Browser viewer for observer JSONL](viewer.md).

On any non-`ParserFailure` error from the live tailer, the wrapper prints a stderr warning, rebuilds `.jsonl` from the full `.trace` after Codex exits, and preserves Codex's exit code (unless `CODEV_OBSERVE_STRICT_PARSE=1`, in which case the wrapper exits `1` after first printing the original Codex exit code). If the tailer thread fails to exit within `CODEV_OBSERVE_LIVE_JOIN_TIMEOUT`, the wrapper leaves `.jsonl` in its partial state, prints a timeout warning, and applies the same strict-mode rule; no `.jsonl.partial` is written in that branch.

On parser failure:

```text
.codev/observe/<session-id>.jsonl.partial
```

contains parsed events so far, or empty file.

Permissions are best effort: wrapper creates observe dir as `0700` and artifacts as `0600`. Existing dirs are not widened. Non-POSIX filesystems may not enforce these modes.

## JSONL schema

One event per line:

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
  "command": ["/real/path/codex", "implement feature"],
  "raw_syscall": "write(3</abs/path/file.txt>, \"x\", 1) = 1",
  "result": 1
}
```

**Severe sensitive-data risk:** `command` and `raw_syscall` can contain secrets. JSONL is audit output, not safe telemetry.

`path`, `old_path`, and `new_path` are absolute strings when resolved, else `null`. No sentinel strings.

Operations:

- `create`: strong creation evidence like `creat`, `O_CREAT|O_EXCL`, `mkdir*`, `mknod*`, `symlink*`, `link*` destination.
- `modify`: positive-byte writes to known writable fd, `O_TRUNC`, `truncate*`, `ftruncate`, parsed `fallocate`.
- `delete`: `unlink*`, `rmdir`.
- `rename`: `rename*`; partial old/new resolution allowed.
- `chmod`: `chmod*`, `fchmod*`.
- `metadata`: `chown*`, `utime*`, `utimensat`, `futimesat`.

Failed syscalls emit no event. Zero-byte writes emit no event. Events are not coalesced.

## Backend tradeoffs

Chosen backend: `strace`.

Why:

- No root required for tracing own child on normal Linux systems.
- `-f` follows process tree.
- Captures syscall PID and raw path/fd details.
- Works in small repo/test setup with Python stdlib.

Rejected for initial scope:

- `inotify`: low overhead, but cannot attribute events to Codex process tree.
- `fanotify`: stronger watching, but permission and attribution complexity too high.
- `eBPF`: powerful, but kernel/capability/version complexity too high.
- `auditd`: privileged system service and host-state changes.

## Fidelity limits

- `mmap` writes may be missed.
- Plain `open(..., O_CREAT)` without `O_EXCL` is not logged as `create`; later writes become `modify`.
- Atomic-save patterns appear as temp create/modify plus rename.
- Symlink paths are logged as used by process, not guaranteed real target.
- Hardlink writes log only path used by syscall.
- Deleted-open files may resolve as `null` if fd path unavailable.
- `fchdir`, inherited fds, fd reuse, and cwd races are best effort.
- `io_uring`, `copy_file_range`, `sendfile`, and `splice` may be missed or under-attributed.
- `strace` adds overhead and can perturb timing-sensitive sessions.
- Wrapper runs traced process in separate process group, forwards SIGINT/SIGTERM/SIGQUIT plus interactive terminal signals SIGWINCH/SIGTSTP/SIGCONT where available, and escalates termination after a short grace period.

## Troubleshooting

- Recursion / wrong binary: set `CODEV_OBSERVE_REAL_CODEX` to absolute real Codex path.
- Missing `strace`: install `strace`, or set `CODEV_OBSERVE_DISABLE=1` to bypass.
- Ptrace denied: check sandbox/seccomp/Yama policy.
- Unwritable observe dir: set `CODEV_OBSERVE_DIR` to writable local path.
- Symlink observe dir rejected: use real directory or set `CODEV_OBSERVE_ALLOW_SYMLINK_DIR=1` knowingly.
- Parser partial output: inspect `.trace` and `.jsonl.partial`; retry with current code or strict parse mode if failure must block.
