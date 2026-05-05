# Spec 1: Monitor filesystem modifications from wrapped Codex processes

## Summary

Build Linux-first `codex` wrapper plus observer that launches real Codex command unchanged, traces filesystem-mutating syscalls from that process tree, and writes JSONL audit logs under `.codev/observe/` by default.

## Goals

- Drop-in `codex` shim usable from shell alias or earlier `PATH` entry.
- Preserve real Codex argv, stdio, interactive behavior, signals, and exit status.
- Track writes from full wrapped process tree, including child shells/tools spawned by Codex.
- Record create, modify, delete, rename, chmod, and metadata-change events when distinguishable.
- Avoid unrelated system writes by monitoring only wrapped process descendants.
- Avoid Gemini/Claude dependency for implementation, review, tests, or runtime.
- Keep implementation small enough for this repo and CI-like local test environment.

## Non-goals

- Perfect kernel-level file integrity auditing.
- Cross-platform support beyond Linux.
- Monitoring already-running Codex sessions.
- Capturing file content diffs.
- Replacing auditd/eBPF/fanotify for privileged production forensics.
- Guaranteed detection for every mmap or hardlink/symlink edge case.

## User experience

User installs shim earlier in `PATH`, or aliases `codex` to wrapper:

```bash
export PATH="$PWD/bin:$PATH"
codex "implement feature"
```

Wrapper resolves real Codex binary, starts monitored session, and writes deterministic logs:

```text
.codev/observe/<session-id>.jsonl
.codev/observe/<session-id>.trace
```

Config knobs:

- `CODEV_OBSERVE_REAL_CODEX`: absolute path to real Codex binary. Highest priority.
- `CODEV_OBSERVE_DIR`: log directory. If set, skip ancestor search completely; relative values resolve against wrapper launch cwd, then normalize to absolute real path. If unset, search upward from wrapper launch cwd for nearest ancestor containing `.codev`, then use that `.codev/observe`; if none, use `$PWD/.codev/observe`. Symlink/path-safety rules apply after normalization.
- `CODEV_OBSERVE_DISABLE=1`: bypass monitor and exec real Codex after normal real-binary lookup. Shim still must avoid self-recursion.
- `CODEV_OBSERVE_SESSION_ID`: caller-supplied invocation id. Default generated timestamp + PID + random suffix.

`session_id` and `invocation_id` are identical in v1. Both fields exist so future grouping can put multiple invocations under one broader session without breaking schema.

## Chosen backend

Use `strace` as initial backend.

Invocation shape:

```bash
strace -f -qq -ttt -s 4096 -yy -o <trace-file> -e trace=%file,%desc,%process <real-codex> <original-args...>
```

Rationale:

- Unprivileged for parent tracing child on normal Linux systems.
- Tracks process tree via `-f` across `fork`, `vfork`, and `clone`.
- Emits PID with each traced syscall when multiple processes exist.
- Captures path-oriented mutating syscalls directly: `open*`, `creat`, `rename*`, `unlink*`, `mkdir*`, `rmdir`, `chmod*`, `chown*`, `utime*`, `truncate*`, `link*`, `symlink*`, `mknod*`.
- Captures descriptor writes (`write`, `pwrite*`, `writev`, `ftruncate`, `fsync`, `close`) enough to classify previously opened writable paths as modified. `fsync` and `close` are tracking-only signals in v1, not event producers by themselves.
- Runs in local tests without root, fanotify privileges, auditd configuration, or eBPF loader.

Rejected initial backends:

- `inotify`: efficient, but cannot reliably attribute events to Codex process tree; sees directory events from all writers.
- `fanotify`: stronger filesystem watch model, but permission/capability constraints and attribution complexity exceed minimal scope.
- `eBPF`: good fidelity/attribution potential, but kernel/version/capability complexity too high for initial repo and tests.
- `auditd`: system-wide service, needs privileged rules and may affect host state.
- Hybrid: possible later, but strace alone satisfies acceptance criteria with documented limits.

## Event model

Default log format: JSONL, one object per event. Unknown-path events are allowed only when syscall clearly mutated filesystem but parser cannot resolve path. No sentinel strings like `"<unknown>"`; use JSON `null`. Sessions with no detected filesystem mutations must still create an empty `.jsonl` file.

Required fields and types:

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
  "raw_syscall": "openat(AT_FDCWD, \"file.txt\", O_WRONLY|O_CREAT|O_TRUNC, 0666) = 3",
  "result": 3
}
```

Field rules:

- `timestamp`: ISO-8601 UTC derived from strace syscall completion timestamp (`-ttt`) when available. Parser wall-clock time is fallback only when trace lacks timestamp.
- `session_id` / `invocation_id`: strings. Sanitized filename-safe session id used for files; original unsafe caller input should not become path segment.
- `pid`: integer PID from trace line, or `null` only if parser cannot extract PID from single-process trace.
- `process.pid`: same as `pid`; `process.ppid`: integer when inferred from fork/clone state, else `null`; `process.comm`: string when read from proc/trace, else `null`.
- `operation`: one of `create`, `modify`, `delete`, `rename`, `chmod`, `metadata`.
- `path`: absolute path string when resolved, else `null`.
- `old_path` / `new_path`: absolute path strings for rename when resolved, else `null`. For non-rename events, both `null`.
- `command`: argv array used to invoke real Codex, with wrapper-resolved executable path at index 0 and original user args unchanged after it.
- `raw_syscall`: raw strace syscall text for event source.
- `result`: integer syscall return value when parsed, else string/`null` for unusual strace output.

Operation mapping:

Only successful mutating syscalls emit filesystem events. Failed syscalls are ignored for event JSONL by default. Future versions may add separate diagnostic records, but v1 event logs mean "kernel accepted operation".

- `create`: strong creation evidence only: `creat`, `open*` with `O_CREAT|O_EXCL`, successful `mkdir`, `mknod`, `symlink`, or `link` destination.
- `modify`: successful write-like syscall (`write`, `pwrite*`, `writev`) against fd known to reference regular path opened writable and returning positive byte count; `truncate*`; `ftruncate`; `fallocate` when parsed and path known from fd. Zero-byte write results do not emit `modify`. `open*` with `O_WRONLY`/`O_RDWR`/`O_TRUNC` may seed fd state but does not by itself emit `modify` unless `O_TRUNC` succeeds.
- `delete`: successful `unlink*` and `rmdir`.
- `rename`: successful `rename`, `renameat`, `renameat2`; set `old_path` and `new_path`, use `path = new_path`.
- `chmod`: successful `chmod`, `fchmod`, `fchmodat`.
- `metadata`: successful `chown*`, `utime*`, `utimensat`, `futimesat`, and similar ownership/time metadata syscalls.
- `open_write`: not an event type in v1. Writable opens without actual write/truncate evidence are tracked internally only.

Ambiguous creation note: `open*` with `O_CREAT` but without `O_EXCL` can open existing files. V1 does not classify that alone as `create`; if later write occurs, emit `modify`. If `O_TRUNC` also succeeds, emit `modify`.

Event ordering:

- Preserve trace order in JSONL.
- V1 does not coalesce events. Emit one event per observed mutating syscall. Repeated positive-byte writes to same fd produce repeated `modify` events.

Strace line-shape rules:

- Minimum supported `strace`: 5.10 or newer, or distro build that emits line shapes covered by committed parser fixtures.
- Parser support contract is fixture-defined: initial implementation must include canonical trace fixtures for normal PID-prefixed lines, single-process lines, `-yy` fd annotations, and unfinished/resumed fragments from supported `strace` output.
- Parser must tolerate multi-process `strace -f` output containing `<unfinished ...>` and `<... resumed>` syscall fragments.
- For v1, parser should stitch unfinished/resumed pairs by PID and syscall name when feasible before applying operation mapping.
- If stitching fails, parser must skip fragment safely, keep raw trace, and avoid false events.
- Parser fixture tests must include at least one unfinished/resumed mutating syscall and one safely skipped malformed fragment.

Path rules:

- Convert relative paths to absolute paths using traced process cwd where available.
- Maintain per-PID cwd from `chdir`, `fchdir` when distinguishable; default initial cwd is wrapper cwd.
- Resolve `AT_FDCWD` against per-PID cwd.
- Resolve `*at` syscalls with dirfd from per-PID fd table when available; use `-yy` descriptor path annotations to map dirfd/fd operations back to paths.
- If dirfd path cannot be resolved, emit event with `path: null` only when mutation itself is clear, and keep `raw_syscall` for audit. Parser tests must cover this fallback.
- Rename partial resolution: emit rename event whenever syscall succeeds and at least raw old/new arguments are parseable. Set each of `old_path`, `new_path`, and `path` independently: resolved absolute string when known, otherwise `null`; `path` mirrors `new_path`. Do not skip successful rename solely because one side is unknown.
- Best effort only for symlink canonicalization; log path passed to syscall after absolute normalization, not necessarily realpath.
- If path is unknown for deleted-open files, inherited FDs, fd reuse, or missed cwd/fchdir state, emit `path: null` rather than fake path.

## Process-tree attribution

`strace -f` launches real Codex as traced child and follows descendants. Only syscalls from that traced tree enter trace file. Parser uses PID prefixes emitted by strace to set `pid` and update per-PID state.

Process identity best effort:

- Record PID from trace line.
- Track parent/child from clone/fork/vfork return values where parseable.
- Optional `comm` may be null initially; can be filled later from `/proc/<pid>/comm` while process still exists.

This avoids monitoring unrelated system writes because no global filesystem watch exists.

## Recursive log write avoidance

Observer parent and `strace` process write `.codev/observe/*` logs outside traced Codex process tree. Since only real Codex child tree is traced, observer log writes do not generate events.

Additional guard: parser drops events only for active trace/JSONL artifact file paths for current session unless environment sets `CODEV_OBSERVE_INCLUDE_LOG_WRITES=1`. It must not drop all writes under observe directory, because Codex may legitimately modify other files there.

Observe directory and log files:

- Create observe dir with mode `0700` when possible.
- Create trace and JSONL files with mode `0600` using exclusive creation.
- If caller supplies existing observe dir, do not widen permissions. Warn if directory is group/world-readable.
- If `CODEV_OBSERVE_SESSION_ID` collides with existing log path, append deterministic numeric suffix (`-1`, `-2`, ...) before launching Codex; never overwrite previous logs.
- Raw trace and JSONL may contain absolute paths, command arguments, and syscall strings that include secrets. Documentation must label this as severe sensitive-data risk in setup section and log-format section. Wrapper should print one stderr warning per session unless `CODEV_OBSERVE_QUIET=1`. Redaction is not v1 scope. Restrictive permissions are best effort and may not hold on non-POSIX filesystems; docs must state this.
- Symlink/path safety: resolve observe dir with `realpath` before use, reject final observe dir if it is a symlink unless caller explicitly set `CODEV_OBSERVE_ALLOW_SYMLINK_DIR=1`, create files with exclusive open under resolved dir, and verify final log file resolved parent remains observe dir before writing.

## Real Codex binary lookup

When wrapper command is named `codex`, lookup order:

1. If `CODEV_OBSERVE_REAL_CODEX` set, use it after executable/path validation.
2. Else inspect `PATH` entries in order and find executable named `codex` whose resolved path is not wrapper resolved path.
3. Else try sibling names `codex.real` and `codex.bin` next to wrapper for packaged installs.
4. If none found, print actionable error and exit `127`.

Wrapper must not recurse into itself. It compares `realpath(candidate)` to `realpath(sys.argv[0])` and skips equal path. It must invoke real Codex using argv-array execution (`subprocess` without `shell=True` or direct `execve`), never shell interpolation.

## Permissions and dependencies

Required:

- Linux.
- Python 3 standard library for wrapper/parser.
- `strace` 5.10+ installed and executable, or compatible output covered by fixtures.
- Kernel ptrace policy allowing parent process to trace its own child. Normal `kernel.yama.ptrace_scope=1` permits this. No root expected.

Failure behavior:

- Missing `strace`: print error with install/setup hint, exit `127` without launching Codex unless explicit bypass set.
- `ptrace` denied by sandbox/seccomp/Yama: print error, preserve nonzero failure, no partial success claim.
- Missing/unwritable observe dir: print error and exit nonzero before launching Codex.
- Failed mutating syscalls from Codex: ignored in event JSONL. Keep raw trace for debugging.
- Real Codex exits nonzero: wrapper exits same code if trace/parsing infrastructure succeeded.
- Parser failure after Codex exits: keep raw `.trace`, always create `.jsonl.partial` containing parsed events so far or empty file if none, print warning. In default non-strict mode, real Codex exit code always wins, even if parsing failed. In `CODEV_OBSERVE_STRICT_PARSE=1`, parser/trace infrastructure failure wins after wrapper reports original Codex code on stderr.
- Wrapper interrupted by SIGINT/SIGTERM: forward signal to traced process group when possible, wait briefly, then terminate. Preserve conventional signal exit code (`128 + signal`) if Codex does not provide clearer status. Keep partial trace.
- Trace/log file collision: never overwrite; use deterministic numeric suffix before launch.
- `CODEV_OBSERVE_SESSION_ID` sanitization: allow only `[A-Za-z0-9_.-]` in filename component; replace other characters with `_`; reject empty, `.` and `..`; never allow slash/path traversal. Preserve sanitized value in `session_id` for consistency.

## Fidelity limits

Known limits accepted for initial scope:

- `mmap` writes: shared writable mappings may not emit write syscalls. Initial implementation may only log `mmap`/writable-open evidence or miss final dirtying. Documented limitation.
- Creation semantics: plain `open(..., O_CREAT)` without `O_EXCL` is not enough for `create`; users may see later `modify` instead for files that were newly created but ambiguous from syscall result.
- Atomic-save patterns: editors often write temp file then `rename` over target. Log includes temp create/modify and rename old/new paths; semantic "file edited" inference remains consumer task.
- Symlinks: logged path may be symlink path used by process, not resolved target. Hard to prove target without extra stat races.
- Hardlinks: modifying one link mutates inode visible through other links; log records path used by syscall only.
- Deleted-open files: writes through fd after unlink may map to last known fd path with deleted marker if strace annotates it; otherwise path may be unknown.
- FDs inherited across exec/fork: best effort per-PID fd table copied across fork when parseable.
- CWD races: relative path normalization depends on parsed cwd state and can be wrong if parser misses `fchdir`/namespace details.
- `io_uring` and uncommon syscalls may be incomplete in first pass.
- Descriptor-to-descriptor mutators such as `copy_file_range`, `sendfile`, and `splice` may be missed or under-attributed in v1 unless explicitly parsed later.
- Signals/TTY: strace generally preserves interactive stdio, but adds tracing overhead and can perturb timing-sensitive processes.

## Minimal implementation shape

Add small Python package/script set:

```text
bin/codex                         # shim entrypoint
src/ai_observe/__init__.py
src/ai_observe/codex_observe.py    # CLI wrapper + real codex lookup + strace launch
src/ai_observe/trace_parser.py     # strace-to-JSONL parser
tests/test_codex_observe.py        # fake codex command exercising child writes
README.md or docs/observe.md       # setup and limitations
```

If repo lacks packaging, keep scripts directly executable and importable with local path bootstrap. Prefer stdlib-only tests using `pytest` if already present; otherwise `unittest`.

Implementation flow:

1. Wrapper creates observe dir and session id.
2. Wrapper starts `strace` with real Codex and original args, trace output path, inherited stdio.
3. Wrapper waits for strace process and captures exit status.
4. Wrapper parses trace file into JSONL events after process exits.
5. Wrapper exits with real Codex exit status.

Long-running interactive sessions are supported because stdio remains inherited and trace writes stream to file while session runs. JSONL appears after exit for initial implementation. Future enhancement can tail/parse trace live.

## Test requirements

Automated or reproducible tests must demonstrate:

Parser unit tests from canned strace snippets:

- Canonical supported `strace` line-shape fixtures, including version/source comment.
- Strong create detection: `O_CREAT|O_EXCL`, not plain `O_CREAT`.
- Modify detection from writable fd plus positive-byte write/truncate; zero-byte writes produce no `modify`.
- Delete, rename, chmod, metadata mapping.
- Failed syscalls do not emit events.
- Observe-dir exclusion filter.
- `strace -f` `<unfinished ...>` / `<... resumed>` stitching and malformed-fragment skip behavior.

Wrapper/integration tests:

- Fake `codex` command invoked through wrapper receives original args.
- Child process creates file.
- Child process modifies file.
- Child process deletes file.
- Child process renames file.
- Child process chmods file.
- JSONL contains expected operations and absolute paths for resolvable cases; empty JSONL exists for no-mutation run.
- Wrapper exit code equals fake Codex exit code for success and failure cases.
- Wrapper skips itself and resolves real fake Codex when shim named `codex` appears earlier in `PATH`.
- `CODEV_OBSERVE_DISABLE=1` bypasses monitor without recursion.
- Missing `strace` path fails cleanly or test skips with explicit annotation.
- Parser failure behavior keeps raw trace, creates deterministic `.jsonl.partial`, and preserves Codex exit code in non-strict mode.
- CWD change plus relative path normalization.
- Dirfd-relative `*at` syscall resolution, unknown-dirfd fallback to `path: null`, and rename partial-resolution behavior.
- Session-id sanitization, deterministic collision suffix behavior, explicit `CODEV_OBSERVE_DIR` normalization semantics, and observe-dir symlink/path-safety behavior.
- Signal forwarding/interrupted session behavior where practical.

Integration tests requiring `strace` should skip cleanly when `strace` is unavailable or ptrace is denied. Parser unit tests must still run without `strace`.

Test can create temporary directory:

```text
tmp/bin/codex          # wrapper shim
real-bin/codex         # fake real command
work/                  # file operations
work/.codev/observe/   # logs
```

Fake real command may be Python script spawning child Python process that performs filesystem operations. This proves process-tree tracking, not only parent PID tracking.

## Documentation requirements

Docs must include:

- Install/setup examples for PATH shim and `CODEV_OBSERVE_REAL_CODEX`.
- Log location and JSONL schema summary, including severe sensitive-data warning in setup and log-format sections plus note that permissions are best effort on non-POSIX filesystems.
- Dependency/permission notes for `strace` and ptrace.
- Fidelity limits listed above.
- Troubleshooting for recursion, missing real Codex, missing `strace`, and ptrace denial.

## Acceptance criteria mapping

- Chosen monitoring approach and tradeoffs: strace backend documented with rejected alternatives.
- Concrete CLI/shim UX: wrapper named `codex`, PATH/alias/env setup defined.
- Process tree: `strace -f` backend and child-process tests required.
- Avoid unrelated writes: trace only wrapped process tree.
- Avoid recursive monitoring: observer/log writer outside traced tree and log-dir filter.
- Exit status/stdio: inherited stdio and strace child exit preservation required.
- JSONL audit data: schema includes timestamp, PID, operation, nullable absolute path, rename old/new, session id, command args.
- Minimal implementation/test fit: stdlib Python + strace + temp fake Codex tests.
