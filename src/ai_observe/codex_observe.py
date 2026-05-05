"""Codex wrapper that traces filesystem mutations with strace."""
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
from typing import Callable

from .trace_parser import ParserFailure, parse_trace_file

SESSION_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


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


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ if env is None else env)
    try:
        return run(argv, env)
    except ObserveError as exc:
        print(f"codex-observe: {exc}", file=sys.stderr)
        return exc.code


def run(argv: list[str], env: dict[str, str]) -> int:
    real_codex = resolve_real_codex(env, wrapper_argv0=Path(sys.argv[0]))
    if env.get("CODEV_OBSERVE_DISABLE") == "1":
        os.execvpe(str(real_codex), [str(real_codex), *argv], env)
        raise AssertionError("execvpe returned")

    if sys.platform.startswith("linux") is False:
        raise ObserveError("Linux required for strace backend", 1)
    strace = shutil.which("strace", path=env.get("PATH"))
    if not strace:
        raise ObserveError("strace not found; install strace or set CODEV_OBSERVE_DISABLE=1", 127)

    logs = prepare_logs(env)
    if env.get("CODEV_OBSERVE_QUIET") != "1":
        print(
            "codex-observe: warning: trace/JSONL logs may contain secrets from paths, argv, and syscalls",
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
        str(real_codex),
        *argv,
    ]
    command = [str(real_codex), *argv]
    proc = None
    interrupted: int | None = None
    forced_terminated = False
    old_handlers: dict[int, Callable | int | None] = {}

    def _handler(signum: int, _frame) -> None:  # type: ignore[no-untyped-def]
        nonlocal interrupted
        interrupted = signum
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

    for sig in (signal.SIGINT, signal.SIGTERM):
        old_handlers[sig] = signal.getsignal(sig)
        signal.signal(sig, _handler)
    try:
        try:
            verify_log_path_safe(logs.trace_path, logs.observe_dir)
            proc = subprocess.Popen(trace_cmd, stdin=None, stdout=None, stderr=None, env=env, start_new_session=True)
            forced_terminated = wait_for_process(proc, lambda: interrupted, float(env.get("CODEV_OBSERVE_SIGNAL_GRACE", "2")))
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
        print("codex-observe: strace failed; ptrace may be denied by sandbox/seccomp/Yama", file=sys.stderr)

    parse_failed = False
    try:
        fail_after = env.get("CODEV_OBSERVE_TEST_FAIL_AFTER")
        result = parse_trace_file(
            logs.trace_path,
            None,
            session_id=logs.session_id,
            invocation_id=logs.session_id,
            command=command,
            initial_cwd=os.getcwd(),
            active_artifacts=[logs.trace_path, logs.jsonl_path, logs.partial_path],
            include_log_writes=env.get("CODEV_OBSERVE_INCLUDE_LOG_WRITES") == "1",
            fail_after_events=int(fail_after) if fail_after else None,
        )
        safe_write_jsonl(logs.jsonl_path, result.events, logs.observe_dir)
    except ParserFailure as exc:
        parse_failed = True
        safe_write_jsonl(logs.partial_path, exc.events, logs.observe_dir)
        print(f"codex-observe: parser failed; wrote {logs.partial_path}; original exit {codex_code}", file=sys.stderr)
    except Exception as exc:  # safe wrapper behavior
        parse_failed = True
        safe_write_jsonl(logs.partial_path, [], logs.observe_dir)
        print(f"codex-observe: parser failed; wrote empty {logs.partial_path}; original exit {codex_code}: {exc}", file=sys.stderr)

    if parse_failed and env.get("CODEV_OBSERVE_STRICT_PARSE") == "1":
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
        import json
        for event in events:
            fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

def resolve_real_codex(env: dict[str, str], wrapper_argv0: Path) -> Path:
    wrapper_real = safe_resolve(wrapper_argv0)
    explicit = env.get("CODEV_OBSERVE_REAL_CODEX")
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return validate_real_candidate(candidate, wrapper_real, "CODEV_OBSERVE_REAL_CODEX")

    for entry in env.get("PATH", os.defpath).split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / "codex"
        if not candidate.exists() or not os.access(candidate, os.X_OK):
            continue
        if safe_resolve(candidate) == wrapper_real:
            continue
        return validate_real_candidate(candidate, wrapper_real, "PATH codex")

    for name in ("codex.real", "codex.bin"):
        candidate = wrapper_real.parent / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return validate_real_candidate(candidate, wrapper_real, name)
    raise ObserveError("real codex not found; set CODEV_OBSERVE_REAL_CODEX", 127)


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
    configured_dir = env.get("CODEV_OBSERVE_DIR")
    if configured_dir:
        configured_path = Path(configured_dir).expanduser()
        if not configured_path.is_absolute():
            configured_path = Path.cwd() / configured_path
        if configured_path.exists() and configured_path.is_symlink() and env.get("CODEV_OBSERVE_ALLOW_SYMLINK_DIR") != "1":
            raise ObserveError(f"observe dir must not be symlink: {configured_path}", 1)
    observe_dir = resolve_observe_dir(env)
    if observe_dir.exists() and observe_dir.is_symlink() and env.get("CODEV_OBSERVE_ALLOW_SYMLINK_DIR") != "1":
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
            print(f"codex-observe: warning: observe dir is group/world accessible: {observe_dir}", file=sys.stderr)
    except OSError:
        pass

    base_session = sanitize_session_id(env.get("CODEV_OBSERVE_SESSION_ID") or generate_session_id())
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
    configured = env.get("CODEV_OBSERVE_DIR")
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
        raise ObserveError("invalid CODEV_OBSERVE_SESSION_ID after sanitization", 1)
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
