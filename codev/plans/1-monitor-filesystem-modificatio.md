# Plan 1: Monitor filesystem modifications from wrapped Codex processes

## Constraints

- Codex-only implementation/review/runtime. No Gemini/Claude dependency.
- Keep implementation small: Python stdlib + `strace`; no packaging overhaul.
- Linux-first; tests degrade cleanly if live `strace` unavailable.
- Follow approved spec exactly: strace backend, JSONL schema, deterministic behavior.

## Phase 1: Project skeleton and wrapper CLI

### Files

- `bin/codex`
- `src/ai_observe/__init__.py`
- `src/ai_observe/codex_observe.py`
- `src/ai_observe/trace_parser.py`
- `tests/test_trace_parser.py`
- `tests/fixtures/strace/`
- `tests/test_codex_observe.py`
- `docs/observe.md`

### Work

1. Add executable `bin/codex` shim.
   - Bootstrap repo-local `src` onto `sys.path`.
   - Call `ai_observe.codex_observe.main()`.
2. Add wrapper module.
   - Parse env vars:
     - `CODEV_OBSERVE_REAL_CODEX`
     - `CODEV_OBSERVE_DIR`
     - `CODEV_OBSERVE_DISABLE`
     - `CODEV_OBSERVE_SESSION_ID`
     - `CODEV_OBSERVE_STRICT_PARSE`
     - `CODEV_OBSERVE_INCLUDE_LOG_WRITES`
     - `CODEV_OBSERVE_ALLOW_SYMLINK_DIR`
     - `CODEV_OBSERVE_QUIET`
   - Resolve real Codex via env, PATH skip-self, then sibling `codex.real` / `codex.bin`.
   - Use argv-array subprocess only; no shell.
3. Implement session/log setup.
   - Generate sanitized session id.
   - Resolve observe dir rules.
   - Enforce symlink/path-safety checks.
   - Create dir `0700` best effort.
   - Create `.trace` and `.jsonl` files using exclusive create paths; create `.jsonl.partial` only on parser failure.
   - Use deterministic suffix `-1`, `-2`, ... on collision.
4. Implement bypass mode.
   - If `CODEV_OBSERVE_DISABLE=1`, exec real Codex without strace after safe lookup.

### Tests

- Real binary lookup skips shim.
- Env real path wins.
- Bypass preserves argv and avoids recursion.
- Session-id sanitization rejects empty/`.`/`..` and strips unsafe chars.
- Collision suffix deterministic.
- Observe dir env relative/absolute normalization.
- Unset observe dir ancestor search and `$PWD/.codev/observe` fallback.
- Existing observe dir permission warning when group/world-readable.
- File mode best effort: dir `0700`, artifacts `0600`.
- Final resolved parent check for log files.
- Unwritable observe dir fails before launching real Codex.
- Symlink observe dir rejected unless env override.

## Phase 2: strace launch and exit behavior

Note: parser-dependent Phase 2 tests use minimal parser API scaffold from Phase 1/2. Full parser semantics land in Phase 3 before final validation.

### Files

- `src/ai_observe/codex_observe.py`
- `tests/test_codex_observe.py`

### Work

1. Check Linux and `strace` availability.
   - Require `strace` executable.
   - Emit actionable stderr and exit `127` if missing unless bypass.
2. Launch command:

```bash
strace -f -qq -ttt -s 4096 -yy -o <trace-file> -e trace=%file,%desc,%process <real-codex> <args...>
```

3. Preserve stdio.
   - Inherit stdin/stdout/stderr.
   - No stdout wrapping.
4. Preserve exit status.
   - Real Codex/strace exit code wins when infrastructure OK.
   - Default parser failure does not override real code.
   - Strict parse mode exits nonzero on parser failure after reporting original code.
5. Handle signals.
   - Start strace in its own process group using `start_new_session=True` where available.
   - Forward SIGINT/SIGTERM to child process group with `os.killpg`.
   - Preserve `128 + signal` if no clearer child status.
6. Emit severe sensitive-log stderr warning once per session unless quiet.
7. Implement parser-failure flow.
   - Keep raw `.trace`.
   - Write deterministic `.jsonl.partial` containing parsed events so far or empty file.
   - Default mode preserves real Codex exit code.
   - Strict mode exits nonzero and reports original Codex code.

### Tests

- Fake Codex exit `0` preserved.
- Fake Codex exit nonzero preserved.
- Missing `strace` fails cleanly or live test skips when environment cannot simulate safely.
- Ptrace denied scenario documented and covered by injectable subprocess/strace failure unit test.
- Unwritable observe dir fails before fake Codex launch.
- No-mutation run creates empty JSONL.
- Parser failure writes `.jsonl.partial`, preserves exit in default mode, overrides in strict mode.
- Signal behavior covered with best-effort integration test if stable; else documented manual verification.

## Phase 3: Trace parser core

### Files

- `src/ai_observe/trace_parser.py`
- `tests/test_trace_parser.py`
- `tests/fixtures/strace/`

### Work

1. Build small parser around fixture-defined strace text shapes.
   - Support `strace` 5.10+ style output.
   - Commit canonical fixture snippets under `tests/fixtures/strace/` with source/version comments.
   - Support PID-prefixed lines.
   - Support single-process lines.
   - Support `-yy` fd annotations.
   - Stitch `<unfinished ...>` / `<... resumed>` by PID/syscall when feasible.
   - Skip malformed fragments safely.
2. Maintain state.
   - Per-PID cwd; initialize to wrapper cwd.
   - Track `chdir` and best-effort `fchdir` from fd table.
   - Per-PID fd table.
   - Parent/child relationship from fork/clone/vfork when parseable.
   - Copy cwd/fd state to child on fork where parseable.
3. Normalize paths.
   - Relative paths against per-PID cwd.
   - `AT_FDCWD` handling.
   - `*at` dirfd resolution from fd table / `-yy` annotation.
   - Unknown paths become `null`, never sentinel.
4. Emit JSONL schema v1.
   - Preserve trace order.
   - No coalescing.
   - One event per mutating syscall.
   - Drop only active session trace/JSONL artifact paths unless include-log-writes env set.
   - Populate and test all required fields: `schema_version`, `timestamp`, `session_id`, `invocation_id`, `pid`, `process.pid`, `process.ppid`, `process.comm`, `operation`, `path`, `old_path`, `new_path`, `command`, `raw_syscall`, `result`.
   - Timestamp comes from strace `-ttt` when present; parser wall-clock fallback only when absent.

### Operation mapping

- `create`: `creat`, `open*` with `O_CREAT|O_EXCL`, `mkdir*`, `mknod*`, `symlink*`, `link*` destination.
- `modify`: positive-byte `write` / `pwrite*` / `writev` on known writable fd; successful writable `open*` with `O_TRUNC`; `truncate*`; `ftruncate`; parsed `fallocate` when path known.
- `delete`: `unlink*`, `rmdir`.
- `rename`: `rename*`; partial path resolution allowed; `path` mirrors `new_path`.
- `chmod`: `chmod*`, `fchmod*`.
- `metadata`: `chown*`, `utime*`, `utimensat`, `futimesat`.
- Failed syscalls emit no event.
- Zero-byte writes emit no event.
- `fsync`/`close` update state only, no event.

### Tests

Parser fixtures must cover:

- Create with `O_CREAT|O_EXCL`; plain `O_CREAT` not create.
- Positive-byte write modify; zero-byte write no event.
- Successful writable `open*` with `O_TRUNC` emits `modify`; non-truncating writable open alone emits no event.
- Delete, rename, chmod, metadata.
- Failed syscalls ignored.
- PID-prefixed and single-process lines.
- `-yy` fd annotation.
- Unfinished/resumed stitch and malformed fragment skip.
- `chdir`, best-effort `fchdir`, and relative path normalization.
- Dirfd `*at` resolution and unknown-dirfd `path: null`.
- Rename one-side unknown behavior.
- Active artifact path exclusion only.
- Full JSONL schema field population, including timestamp source/fallback and command argv array.
- Parser partial-output behavior via injectable parse error.

## Phase 4: Integration tests and fake Codex workflow

### Files

- `tests/test_codex_observe.py`
- `tests/helpers/` only if needed

### Work

1. Build temp fixture layout:

```text
tmp/bin/codex          # shim copy or wrapper invocation
real-bin/codex         # fake real Codex script
work/                  # cwd
work/.codev/observe/   # logs
```

2. Fake real Codex script spawns child Python process.
   - Child creates file.
   - Child modifies file.
   - Child truncates file via `O_TRUNC`.
   - Child changes cwd and performs relative-path write.
   - Child performs dirfd-relative operation where practical.
   - Child renames file.
   - Child chmods file.
   - Child deletes file.
   - Parent records argv received.
3. Run wrapper through PATH shim.
4. Read generated JSONL.
5. Assert operations and absolute paths for resolvable cases.
6. Skip live integration tests if `strace` missing or ptrace denied.

### Tests

- Process-tree tracking shown by child process operations.
- Live integration covers cwd-relative normalization and, when stable in Python, dirfd-relative `*at` behavior; parser fixtures remain authoritative fallback.
- Wrapper preserves argv.
- Wrapper preserves exit code.
- Wrapper writes logs under deterministic observe dir.
- Empty JSONL exists for no-mutation session.
- Bypass mode runs fake real Codex without `.trace`/JSONL requirement.

## Phase 5: Documentation and final review artifact

### Files

- `docs/observe.md`
- `README.md` only if repo already has one or linking doc helps.
- `codev/reviews/1-monitor-filesystem-modificatio.md`

### Work

1. Document install/setup:

```bash
export PATH="$PWD/bin:$PATH"
export CODEV_OBSERVE_REAL_CODEX="$(command -v codex)"
codex "implement feature"
```

2. Document env vars and lookup order.
3. Document log location and JSONL schema.
4. Document severe sensitive-data risk in setup and log-format sections.
5. Document permissions best effort and non-POSIX caveat.
6. Document Linux/strace/ptrace requirements.
7. Document fidelity limits from spec.
8. Document rejected backend tradeoffs from spec at summary level.
9. Document troubleshooting:
   - recursion / wrong real Codex
   - missing `strace`
   - ptrace denied
   - unwritable observe dir
   - symlink observe dir
   - parser partial output
10. Write review notes after implementation.

## Validation commands

Use available local runner. Preferred order:

```bash
python3 -m unittest discover -s tests
```

If pytest already available and tests use pytest skips:

```bash
python3 -m pytest
```

Manual smoke when `strace` available:

```bash
tmpdir=$(mktemp -d)
# create fake real codex, set CODEV_OBSERVE_REAL_CODEX, run bin/codex, inspect .codev/observe/*.jsonl
```

## PR checklist

- Spec unchanged except typo fixes if needed.
- Plan complete and approved before implementation.
- No Gemini/Claude consults or dependencies.
- No broad `git add .` / `git add -A`; add files explicitly.
- Tests pass or live strace tests skip with clear reason.
- Documentation covers setup and limitations.
