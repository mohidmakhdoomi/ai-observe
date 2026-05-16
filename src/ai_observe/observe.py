"""Generic command wrapper that traces filesystem mutations with strace."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import random
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Callable

from .trace_parser import ParserFailure, TraceParser, dump_event, parse_trace_file

SESSION_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
OBSERVER_SHIM_NAMES = frozenset({"ai-observe", "codex", "claude", "gemini", "opencode"})


@dataclass
class LogPaths:
    observe_dir: Path
    session_id: str
    trace_path: Path
    jsonl_path: Path
    partial_path: Path


class ObserveError(RuntimeError):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


PREFERRED_ENV_PREFIX = "AI_OBSERVE_"
LEGACY_ENV_PREFIX = "CODEV_OBSERVE_"


def env_value(env: dict[str, str], name: str, default: str | None = None) -> str | None:
    """Return preferred ``AI_OBSERVE_*`` value, falling back to legacy alias.

    ``name`` is the suffix without either prefix, e.g. ``"DIR"``. Empty
    strings are considered intentional values so callers can validate them in
    the same way they validated legacy variables.
    """
    preferred = f"{PREFERRED_ENV_PREFIX}{name}"
    if preferred in env:
        return env[preferred]
    legacy = f"{LEGACY_ENV_PREFIX}{name}"
    if legacy in env:
        return env[legacy]
    return default


def env_flag(env: dict[str, str], name: str) -> bool:
    return env_value(env, name) == "1"


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ if env is None else env)
    try:
        return run(argv, env)
    except ObserveError as exc:
        print(f"codex-observe: {exc}", file=sys.stderr)
        return exc.code


def main_shim(
    program: str,
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    error_prefix: str = "ai-observe",
) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ if env is None else env)
    try:
        return run_shim(program, argv, env, wrapper_argv0=Path(sys.argv[0]), error_prefix=error_prefix)
    except ObserveError as exc:
        print(f"{error_prefix}: {exc}", file=sys.stderr)
        return exc.code


def main_generic(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ if env is None else env)
    try:
        session_id, command = parse_generic_args(argv)
        if session_id is not None:
            env["AI_OBSERVE_SESSION_ID"] = session_id
        return run_command(command, env, wrapper_argv0=Path(sys.argv[0]), error_prefix="ai-observe")
    except ObserveError as exc:
        print(f"ai-observe: {exc}", file=sys.stderr)
        return exc.code


def parse_generic_args(argv: list[str]) -> tuple[str | None, list[str]]:
    """Parse the phase-2 generic CLI form: options, ``--``, command argv."""
    session_id: str | None = None
    idx = 0
    while idx < len(argv):
        arg = argv[idx]
        if arg == "--":
            command = argv[idx + 1 :]
            if not command:
                raise ObserveError(f"usage: {generic_usage()}", 2)
            return session_id, command
        if arg == "--session":
            if idx + 1 >= len(argv):
                raise ObserveError("--session requires a value", 2)
            session_id = argv[idx + 1]
            idx += 2
            continue
        if arg in {"-h", "--help"}:
            raise ObserveError(generic_usage(), 0)
        raise ObserveError(f"usage: {generic_usage()}", 2)
    raise ObserveError(f"usage: {generic_usage()}", 2)


def generic_usage() -> str:
    return "ai-observe [--session SESSION] -- command [args...]"


def run(argv: list[str], env: dict[str, str]) -> int:
    """Backward-compatible Codex shim runner."""
    return run_shim("codex", argv, env, wrapper_argv0=Path(sys.argv[0]), error_prefix="codex-observe")


def run_shim(
    program: str,
    argv: list[str],
    env: dict[str, str],
    *,
    wrapper_argv0: Path,
    error_prefix: str = "ai-observe",
) -> int:
    """Run a named program shim under the generic observer backend.

    Named shims pass their program name here; generic command mode uses
    ``run_command`` with an explicit requested argv.
    """
    real_command = resolve_real_program(program, env, wrapper_argv0=wrapper_argv0)
    return run_resolved_command([str(real_command), *argv], env, error_prefix=error_prefix)


def run_command(
    command_argv: list[str],
    env: dict[str, str],
    *,
    wrapper_argv0: Path,
    error_prefix: str = "ai-observe",
) -> int:
    """Run an arbitrary requested command under the observer backend."""
    real_argv = resolve_command_argv(command_argv, env, wrapper_argv0=wrapper_argv0)
    return run_resolved_command(real_argv, env, error_prefix=error_prefix)


def run_resolved_command(real_argv: list[str], env: dict[str, str], *, error_prefix: str) -> int:
    real_command = Path(real_argv[0])
    argv = real_argv[1:]
    if env_flag(env, "DISABLE"):
        os.execvpe(str(real_command), real_argv, env)
        raise AssertionError("execvpe returned")

    if sys.platform.startswith("linux") is False:
        raise ObserveError("Linux required for strace backend", 1)
    strace = shutil.which("strace", path=env.get("PATH"))
    if not strace:
        raise ObserveError(
            "strace not found; install strace or set AI_OBSERVE_DISABLE=1 "
            "(legacy CODEV_OBSERVE_DISABLE=1)",
            127,
        )

    logs = prepare_logs(env)
    if not env_flag(env, "QUIET"):
        print(
            f"{error_prefix}: warning: trace/JSONL logs may contain secrets from paths, argv, and syscalls",
            file=sys.stderr,
        )

    trace_cmd = [
        strace,
        "-f",
        "-qq",
        "-ttt",
        "-s",
        "4096",
        "-yy",
        "-o",
        str(logs.trace_path),
        "-e",
        "trace=%file,%desc,%process",
        *real_argv,
    ]
    command = list(real_argv)
    proc = None
    interrupted: int | None = None
    forced_terminated = False
    old_handlers: dict[int, Callable | int | None] = {}
    fail_after = env_value(env, "TEST_FAIL_AFTER")
    try:
        fail_after_n = int(fail_after) if fail_after else None
    except ValueError:
        fail_after_n = None
    include_log_writes = env_flag(env, "INCLUDE_LOG_WRITES")
    initial_cwd = str(Path(os.getcwd()).resolve())
    active_artifacts = {str(Path(p).resolve()) for p in (logs.trace_path, logs.jsonl_path, logs.partial_path)}

    live_parser: TraceParser | None = None
    live_tracer: LiveTracer | None = None
    if _live_enabled(env):
        live_parser = TraceParser(
            session_id=logs.session_id,
            invocation_id=logs.session_id,
            command=command,
            initial_cwd=initial_cwd,
            active_artifacts=active_artifacts,
            include_log_writes=include_log_writes,
            fail_after_events=fail_after_n,
        )
        candidate = LiveTracer(
            logs.trace_path,
            logs.jsonl_path,
            logs.observe_dir,
            live_parser,
            _live_poll_seconds(env),
        )
        try:
            candidate.start()
            live_tracer = candidate
        except Exception as exc:
            print(
                f"{error_prefix}: warning: live tracer failed to start: {exc}; continuing with post-hoc-only",
                file=sys.stderr,
            )
            live_parser = None
            live_tracer = None

    def _forward(signum: int) -> None:
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signum)
            except ProcessLookupError:
                pass
            except OSError:
                try:
                    proc.send_signal(signum)
                except OSError:
                    pass

    def _interrupt_handler(signum: int, _frame) -> None:  # type: ignore[no-untyped-def]
        nonlocal interrupted
        interrupted = signum
        _forward(signum)

    def _terminal_handler(signum: int, _frame) -> None:  # type: ignore[no-untyped-def]
        _forward(signum)
        if signum == getattr(signal, "SIGTSTP", None):
            os.kill(os.getpid(), signal.SIGSTOP)

    signal_handlers: list[tuple[int, Callable]] = [
        (signal.SIGINT, _interrupt_handler),
        (signal.SIGTERM, _interrupt_handler),
    ]
    for name in ("SIGQUIT", "SIGWINCH", "SIGTSTP", "SIGCONT"):
        sig = getattr(signal, name, None)
        if sig is not None:
            handler = _interrupt_handler if name == "SIGQUIT" else _terminal_handler
            signal_handlers.append((sig, handler))

    for sig, handler in signal_handlers:
        old_handlers[sig] = signal.getsignal(sig)
        signal.signal(sig, handler)
    try:
        try:
            verify_log_path_safe(logs.trace_path, logs.observe_dir)
            proc = subprocess.Popen(trace_cmd, stdin=None, stdout=None, stderr=None, env=env, start_new_session=True)
            forced_terminated = wait_for_process(proc, lambda: interrupted, float(env_value(env, "SIGNAL_GRACE", "2")))
        except PermissionError as exc:
            raise ObserveError(f"failed to start strace: {exc}", 1) from exc
        except OSError as exc:
            raise ObserveError(f"failed to run strace: {exc}", 1) from exc
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)  # type: ignore[arg-type]

    codex_code = normalize_exit_code(proc.returncode if proc is not None else 1)
    if interrupted and (codex_code == 0 or forced_terminated):
        codex_code = 128 + interrupted
    if codex_code == 1 and logs.trace_path.stat().st_size == 0:
        print(f"{error_prefix}: strace failed; ptrace may be denied by sandbox/seccomp/Yama", file=sys.stderr)

    parse_failed = False
    if live_tracer is not None:
        live_tracer.request_stop()
        timed_out, live_error, live_pf = live_tracer.join(_live_join_timeout(env))
        if timed_out:
            parse_failed = True
            print(
                f"{error_prefix}: live parser did not exit within join timeout; leaving partial .jsonl; original exit {codex_code}",
                file=sys.stderr,
            )
        elif live_pf is not None:
            parse_failed = True
            try:
                safe_write_jsonl(logs.partial_path, live_pf.events, logs.observe_dir)
            except Exception as exc:
                print(
                    f"{error_prefix}: parser failed; could not write {logs.partial_path}; original exit {codex_code}: {exc}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"{error_prefix}: parser failed; wrote {logs.partial_path}; original exit {codex_code}",
                    file=sys.stderr,
                )
            try:
                safe_write_jsonl(logs.jsonl_path, [], logs.observe_dir)
            except Exception:
                pass
        elif live_error is not None:
            parse_failed = True
            print(
                f"{error_prefix}: warning: live parser raised {type(live_error).__name__}: {live_error}; falling back to post-hoc rebuild; original exit {codex_code}",
                file=sys.stderr,
            )
            try:
                result = parse_trace_file(
                    logs.trace_path,
                    None,
                    session_id=logs.session_id,
                    invocation_id=logs.session_id,
                    command=command,
                    initial_cwd=initial_cwd,
                    active_artifacts=active_artifacts,
                    include_log_writes=include_log_writes,
                    fail_after_events=fail_after_n,
                )
                safe_write_jsonl(logs.jsonl_path, result.events, logs.observe_dir)
            except ParserFailure as exc:
                parse_failed = True
                try:
                    safe_write_jsonl(logs.partial_path, exc.events, logs.observe_dir)
                except Exception as inner:
                    print(
                        f"{error_prefix}: parser failed; could not write {logs.partial_path}; original exit {codex_code}: {inner}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"{error_prefix}: parser failed; wrote {logs.partial_path}; original exit {codex_code}",
                        file=sys.stderr,
                    )
                try:
                    safe_write_jsonl(logs.jsonl_path, [], logs.observe_dir)
                except Exception:
                    pass
            except Exception as exc:
                parse_failed = True
                try:
                    safe_write_jsonl(logs.partial_path, [], logs.observe_dir)
                    print(
                        f"{error_prefix}: parser failed; wrote empty {logs.partial_path}; original exit {codex_code}: {exc}",
                        file=sys.stderr,
                    )
                except Exception as inner:
                    print(
                        f"{error_prefix}: parser failed; could not write {logs.partial_path}; original exit {codex_code}: {exc}; secondary: {inner}",
                        file=sys.stderr,
                    )
                try:
                    safe_write_jsonl(logs.jsonl_path, [], logs.observe_dir)
                except Exception:
                    pass
        # Clean live exit: `.jsonl` already has the final stream; nothing else to do.
    else:
        try:
            result = parse_trace_file(
                logs.trace_path,
                None,
                session_id=logs.session_id,
                invocation_id=logs.session_id,
                command=command,
                initial_cwd=initial_cwd,
                active_artifacts=active_artifacts,
                include_log_writes=include_log_writes,
                fail_after_events=fail_after_n,
            )
            safe_write_jsonl(logs.jsonl_path, result.events, logs.observe_dir)
        except ParserFailure as exc:
            parse_failed = True
            safe_write_jsonl(logs.partial_path, exc.events, logs.observe_dir)
            print(f"{error_prefix}: parser failed; wrote {logs.partial_path}; original exit {codex_code}", file=sys.stderr)
        except Exception as exc:  # safe wrapper behavior
            parse_failed = True
            safe_write_jsonl(logs.partial_path, [], logs.observe_dir)
            print(f"{error_prefix}: parser failed; wrote empty {logs.partial_path}; original exit {codex_code}: {exc}", file=sys.stderr)

    if parse_failed and env_flag(env, "STRICT_PARSE"):
        return 1
    return int(codex_code)



def wait_for_process(proc: subprocess.Popen, interrupted_getter: Callable[[], int | None], grace_seconds: float) -> bool:
    while proc.poll() is None:
        try:
            proc.wait(timeout=0.2)
            return False
        except subprocess.TimeoutExpired:
            pass
        signum = interrupted_getter()
        if not signum:
            continue
        try:
            proc.wait(timeout=grace_seconds)
            return False
        except subprocess.TimeoutExpired:
            pass
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if proc.poll() is not None:
                return True
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return True
            except OSError:
                try:
                    proc.send_signal(sig)
                except OSError:
                    pass
            try:
                proc.wait(timeout=grace_seconds)
                return True
            except subprocess.TimeoutExpired:
                continue
    return proc.poll() is not None


def verify_log_path_safe(path: Path, observe_dir: Path) -> None:
    verify_parent(path, observe_dir)
    if path.is_symlink():
        raise ObserveError(f"refusing to use symlink log path: {path}", 1)


def safe_write_jsonl(path: Path, events, observe_dir: Path) -> None:
    verify_log_path_safe(path, observe_dir)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ObserveError(f"cannot safely write log path {path}: {exc}", 1) from exc
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        from .trace_parser import dump_event
        for event in events:
            fh.write(dump_event(event))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def safe_append_jsonl_handle(path: Path, observe_dir: Path):
    """Open a path-hardened append handle on `path`.

    Verifies path safety, then opens with O_WRONLY | O_APPEND | O_NOFOLLOW
    (when available). Returns a text-mode file object. Raises `ObserveError`
    on any failure. Caller is responsible for closing the handle.
    """
    verify_log_path_safe(path, observe_dir)
    flags = os.O_WRONLY | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ObserveError(f"cannot safely append log path {path}: {exc}", 1) from exc
    try:
        return os.fdopen(fd, "a", encoding="utf-8")
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise ObserveError(f"cannot wrap append handle for {path}: {exc}", 1) from exc


def safe_open_trace_read(path: Path, observe_dir: Path):
    """Open a path-hardened read handle on the `.trace` file.

    Verifies path safety, then opens with O_RDONLY | O_NOFOLLOW (when
    available). Returns a text-mode file object with `errors="replace"`
    so partial multi-byte sequences at read boundaries are tolerated.
    Raises `ObserveError` on any failure. Caller closes the handle.
    """
    verify_log_path_safe(path, observe_dir)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ObserveError(f"cannot safely open trace for read {path}: {exc}", 1) from exc
    try:
        return os.fdopen(fd, "r", encoding="utf-8", errors="replace")
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise ObserveError(f"cannot wrap read handle for {path}: {exc}", 1) from exc

def _live_enabled(env: dict[str, str]) -> bool:
    return env_value(env, "LIVE_PARSE", "1") != "0"


def _clamped_float(raw: str | None, *, lo: float, hi: float, default: float) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value or value < lo or value > hi:  # NaN or out-of-range
        return default
    return value


def _live_poll_seconds(env: dict[str, str]) -> float:
    ms = _clamped_float(env_value(env, "LIVE_POLL_MS"), lo=10.0, hi=2000.0, default=200.0)
    return ms / 1000.0


def _live_join_timeout(env: dict[str, str]) -> float:
    return _clamped_float(env_value(env, "LIVE_JOIN_TIMEOUT"), lo=0.1, hi=600.0, default=30.0)


class LiveTracer:
    """Tail the strace `.trace` file and append events to `.jsonl` as they arrive.

    Lifecycle:
      - `start()` opens both files (raises if either open fails) and spawns
        a daemon thread that runs `_run()`.
      - The main thread calls `request_stop()` once strace exits, then
        `join(timeout)` to wait for the tailer to drain.
      - The thread captures any exceptions into `self.error` (or
        `self.parser_failure` for `ParserFailure`) so the main thread can
        decide how to recover.
    """

    def __init__(
        self,
        trace_path: Path,
        jsonl_path: Path,
        observe_dir: Path,
        parser: TraceParser,
        poll_seconds: float,
    ) -> None:
        self.trace_path = trace_path
        self.jsonl_path = jsonl_path
        self.observe_dir = observe_dir
        self.parser = parser
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self.error: BaseException | None = None
        self.parser_failure: ParserFailure | None = None
        self.thread: threading.Thread | None = None
        self._trace_fh = None
        self._jsonl_fh = None

    def start(self) -> None:
        trace_fh = safe_open_trace_read(self.trace_path, self.observe_dir)
        try:
            jsonl_fh = safe_append_jsonl_handle(self.jsonl_path, self.observe_dir)
        except BaseException:
            try:
                trace_fh.close()
            except OSError:
                pass
            raise
        self._trace_fh = trace_fh
        self._jsonl_fh = jsonl_fh
        try:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        except BaseException:
            try:
                trace_fh.close()
            except OSError:
                pass
            try:
                jsonl_fh.close()
            except OSError:
                pass
            self._trace_fh = None
            self._jsonl_fh = None
            raise

    def request_stop(self) -> None:
        self.stop_event.set()

    def join(self, timeout: float) -> tuple[bool, BaseException | None, ParserFailure | None]:
        if self.thread is None:
            return False, self.error, self.parser_failure
        self.thread.join(timeout=timeout)
        timed_out = self.thread.is_alive()
        return timed_out, self.error, self.parser_failure

    def _emit(self, events) -> None:
        if not events:
            return
        for event in events:
            self._jsonl_fh.write(dump_event(event))
        self._jsonl_fh.flush()

    def _run(self) -> None:
        pending = ""
        try:
            while True:
                chunk = self._trace_fh.read(64 * 1024)
                if chunk:
                    pending += chunk
                    if "\n" in pending:
                        lines = pending.split("\n")
                        pending = lines[-1]
                        for line in lines[:-1]:
                            self._emit(self.parser.feed_line(line))
                    continue
                if self.stop_event.is_set():
                    if pending:
                        self._emit(self.parser.feed_line(pending))
                        pending = ""
                    break
                time.sleep(self.poll_seconds)
        except ParserFailure as exc:
            self.parser_failure = exc
        except BaseException as exc:
            self.error = exc
        finally:
            try:
                if self._trace_fh is not None:
                    self._trace_fh.close()
            except OSError:
                pass
            try:
                if self._jsonl_fh is not None:
                    self._jsonl_fh.close()
            except OSError:
                pass


def resolve_real_codex(env: dict[str, str], wrapper_argv0: Path) -> Path:
    return resolve_real_program("codex", env, wrapper_argv0=wrapper_argv0)


def resolve_real_program(program: str, env: dict[str, str], *, wrapper_argv0: Path) -> Path:
    """Resolve the real executable for a named shim without recursing.

    The resolver is parameterized for future named shims.  Phase 1 continues
    to use it only through ``resolve_real_codex`` / ``run()``.
    """
    wrapper_real = safe_resolve(wrapper_argv0)
    env_name = f"REAL_{program.upper().replace('-', '_')}"
    preferred_key = f"{PREFERRED_ENV_PREFIX}{env_name}"
    legacy_key = f"{LEGACY_ENV_PREFIX}{env_name}"
    explicit = env.get(preferred_key)
    label = preferred_key
    if explicit is None and program == "codex":
        explicit = env.get(legacy_key)
        label = legacy_key
    if explicit is not None:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return validate_real_candidate(candidate, wrapper_real, label)

    for entry in env.get("PATH", os.defpath).split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / program
        if not candidate.exists() or not os.access(candidate, os.X_OK):
            continue
        if safe_resolve(candidate) == wrapper_real:
            continue
        return validate_real_candidate(candidate, wrapper_real, f"PATH {program}")

    for name in (f"{program}.real", f"{program}.bin"):
        candidate = wrapper_real.parent / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return validate_real_candidate(candidate, wrapper_real, name)
    raise ObserveError(f"real {program} not found; set {PREFERRED_ENV_PREFIX}{env_name}", 127)


def resolve_command_argv(command_argv: list[str], env: dict[str, str], *, wrapper_argv0: Path) -> list[str]:
    if not command_argv:
        raise ObserveError(f"usage: {generic_usage()}", 2)
    wrapper_real = safe_resolve(wrapper_argv0)
    forced = env.get(f"{PREFERRED_ENV_PREFIX}REAL_COMMAND")
    if forced is not None:
        real = Path(forced).expanduser()
        if not real.is_absolute():
            real = Path.cwd() / real
        real = validate_non_recursive_executable(real, wrapper_real, f"{PREFERRED_ENV_PREFIX}REAL_COMMAND")
        return [str(real), *command_argv[1:]]

    requested = command_argv[0]
    if "/" in requested:
        candidate = Path(requested).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        real = validate_non_recursive_executable(candidate, wrapper_real, requested)
        return [str(real), *command_argv[1:]]

    for entry in env.get("PATH", os.defpath).split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / requested
        if not candidate.exists() or not os.access(candidate, os.X_OK):
            continue
        try:
            real = validate_non_recursive_executable(candidate, wrapper_real, f"PATH {requested}")
        except ObserveError:
            continue
        return [str(real), *command_argv[1:]]
    raise ObserveError(f"command not found or resolves to observer shim: {requested}", 127)


def validate_non_recursive_executable(path: Path, wrapper_real: Path, label: str) -> Path:
    path = validate_executable(path, label)
    if is_observer_shim(path, wrapper_real):
        raise ObserveError(f"{label} resolves to observer shim; refusing recursion: {path}", 127)
    return path


def is_observer_shim(path: Path, wrapper_real: Path) -> bool:
    path = safe_resolve(path)
    if path in same_directory_observer_shim_paths(wrapper_real):
        return True
    if path.name not in OBSERVER_SHIM_NAMES:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return (
        "ai_observe.observe" in text
        and ("main_shim" in text or "main_generic" in text)
    ) or "ai_observe.codex_observe" in text


def same_directory_observer_shim_paths(wrapper_real: Path) -> set[Path]:
    shim_dir = wrapper_real.parent
    paths = {wrapper_real}
    for name in OBSERVER_SHIM_NAMES:
        candidate = shim_dir / name
        if candidate.exists():
            paths.add(safe_resolve(candidate))
    return paths


def validate_real_candidate(path: Path, wrapper_real: Path, label: str) -> Path:
    path = validate_executable(path, label)
    if path == wrapper_real:
        raise ObserveError(f"{label} resolves to observer shim; refusing recursion: {path}", 127)
    return path


def validate_executable(path: Path, label: str) -> Path:
    path = safe_resolve(path)
    if not path.exists() or not path.is_file() or not os.access(path, os.X_OK):
        raise ObserveError(f"{label} is not executable: {path}", 127)
    return path


def normalize_exit_code(returncode: int | None) -> int:
    if returncode is None:
        return 1
    if returncode < 0:
        return 128 + abs(returncode)
    return returncode


def prepare_logs(env: dict[str, str]) -> LogPaths:
    configured_dir = env_value(env, "DIR")
    if configured_dir:
        configured_path = Path(configured_dir).expanduser()
        if not configured_path.is_absolute():
            configured_path = Path.cwd() / configured_path
        if configured_path.exists() and configured_path.is_symlink() and not env_flag(env, "ALLOW_SYMLINK_DIR"):
            raise ObserveError(f"observe dir must not be symlink: {configured_path}", 1)
    observe_dir = resolve_observe_dir(env)
    if observe_dir.exists() and observe_dir.is_symlink() and not env_flag(env, "ALLOW_SYMLINK_DIR"):
        raise ObserveError(f"observe dir must not be symlink: {observe_dir}", 1)
    observe_dir = observe_dir.resolve(strict=False)

    existed = observe_dir.exists()
    try:
        observe_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ObserveError(f"cannot create observe dir {observe_dir}: {exc}", 1) from exc
    if not existed:
        try:
            os.chmod(observe_dir, 0o700)
        except OSError:
            pass
    if not os.access(observe_dir, os.W_OK):
        raise ObserveError(f"observe dir not writable: {observe_dir}", 1)
    try:
        mode = observe_dir.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"ai-observe: warning: observe dir is group/world accessible: {observe_dir}", file=sys.stderr)
    except OSError:
        pass

    base_session = sanitize_session_id(env_value(env, "SESSION_ID") or generate_session_id())
    for i in range(10000):
        session = base_session if i == 0 else f"{base_session}-{i}"
        trace_path = observe_dir / f"{session}.trace"
        jsonl_path = observe_dir / f"{session}.jsonl"
        partial_path = observe_dir / f"{session}.jsonl.partial"
        if trace_path.exists() or jsonl_path.exists() or partial_path.exists():
            continue
        try:
            verify_parent(trace_path, observe_dir)
            verify_parent(jsonl_path, observe_dir)
            exclusive_touch(trace_path)
            try:
                exclusive_touch(jsonl_path)
            except Exception:
                try:
                    trace_path.unlink()
                except OSError:
                    pass
                raise
            return LogPaths(observe_dir, session, trace_path, jsonl_path, partial_path)
        except OSError as exc:
            raise ObserveError(f"cannot create observe log files in {observe_dir}: {exc}", 1) from exc
    raise ObserveError("unable to allocate unique observe log names", 1)

def resolve_observe_dir(env: dict[str, str]) -> Path:
    configured = env_value(env, "DIR")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.absolute()
    cwd = Path.cwd().resolve()
    for parent in (cwd, *cwd.parents):
        if (parent / ".codev").exists():
            return (parent / ".codev" / "observe").absolute()
    return (cwd / ".codev" / "observe").absolute()


def sanitize_session_id(value: str) -> str:
    sanitized = SESSION_SAFE_RE.sub("_", value)
    if sanitized in {"", ".", ".."}:
        raise ObserveError("invalid AI_OBSERVE_SESSION_ID/CODEV_OBSERVE_SESSION_ID after sanitization", 1)
    return sanitized


def generate_session_id() -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{now}-{os.getpid()}-{random.randrange(0x10000):04x}"


def exclusive_touch(path: Path) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def verify_parent(path: Path, observe_dir: Path) -> None:
    parent = path.parent.resolve(strict=False)
    target_parent = observe_dir.resolve(strict=False)
    if parent != target_parent:
        raise ObserveError(f"log path escapes observe dir: {path}", 1)


def safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


if __name__ == "__main__":
    raise SystemExit(main())
