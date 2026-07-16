"""Generic command wrapper that traces filesystem mutations with strace."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
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
from typing import Any, Callable

from .backends import BackendSession, backends_in_finalize_order, backends_in_prepare_order, parse_backend_selection
from .backends.snapshot import SnapshotBackend
from .backends.strace import StraceBackend
from .snapshot import (
    Manifest,
    all_exclude_patterns,
    capture_manifest,
    deduplicate_snapshot_events,
    diff_manifests,
    parse_roots,
    synthesize_events,
)
from .trace_parser import ParserFailure, TraceParser, dump_event, parse_trace_file

SESSION_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
OBSERVER_SHIM_NAMES = frozenset({"ai-observe", "codex", "claude", "gemini", "opencode", "agy"})


@dataclass
class LogPaths:
    observe_dir: Path
    session_id: str
    trace_path: Path
    jsonl_path: Path
    partial_path: Path
    rebuilt_path: Path
    meta_path: Path


@dataclass
class SnapshotPlan:
    roots: list[Path]
    exclude_patterns: list[str]
    hash_files: bool
    max_files: int | None
    start_manifest: Manifest | None
    end_manifest: Manifest | None
    diagnostics: list[dict[str, Any]]
    warnings: list[str]
    raw_events: list[dict[str, Any]]


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


def build_backends(env: dict[str, str]) -> tuple[tuple[str, ...], dict[str, Any]]:
    try:
        names = parse_backend_selection(env_value(env, "BACKENDS"))
    except ValueError as exc:
        raise ObserveError(str(exc), 2) from exc

    available = {
        "strace": StraceBackend(
            error_factory=ObserveError,
            trace_parser_cls=TraceParser,
            live_tracer_cls=LiveTracer,
            parse_trace_file=parse_trace_file,
            safe_write_jsonl=safe_write_jsonl,
            env_flag=env_flag,
            env_value=env_value,
            live_enabled=_live_enabled,
            live_poll_seconds=_live_poll_seconds,
            live_join_timeout=_live_join_timeout,
        ),
        "snapshot": SnapshotBackend(
            error_factory=ObserveError,
            prepare_plan=prepare_snapshot_plan,
            finalize_plan=finalize_snapshot_plan,
            merge_snapshot_events=merge_snapshot_events,
            build_snapshot_summary=build_snapshot_summary,
            build_session_meta=build_session_meta,
            safe_write_meta=safe_write_meta,
        ),
    }
    return names, {name: available[name] for name in names}


def run_resolved_command(real_argv: list[str], env: dict[str, str], *, error_prefix: str) -> int:
    real_command = Path(real_argv[0])
    if env_flag(env, "DISABLE"):
        os.execvpe(str(real_command), real_argv, env)
        raise AssertionError("execvpe returned")
    if env_flag(env, "NESTED"):
        os.execvpe(str(real_command), real_argv, env)
        raise AssertionError("execvpe returned")
    backend_names, backend_map = build_backends(env)

    logs = prepare_logs(env)
    if not env_flag(env, "QUIET"):
        print(
            f"{error_prefix}: warning: trace/JSONL logs may contain secrets from paths, argv, and syscalls",
            file=sys.stderr,
        )

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
    active_artifacts = {
        str(Path(p).resolve())
        for p in (logs.trace_path, logs.jsonl_path, logs.partial_path, logs.rebuilt_path, logs.meta_path)
    }
    session = BackendSession(
        env,
        dict(env),
        list(real_argv),
        list(real_argv),
        command,
        logs,
        initial_cwd=initial_cwd,
        active_artifacts=active_artifacts,
        error_prefix=error_prefix,
        include_log_writes=include_log_writes,
        fail_after_events=fail_after_n,
    )
    for name in backends_in_prepare_order(backend_names):
        backend_map[name].prepare(session)

    launch_subject = "strace" if "strace" in backend_names else "observed command"

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
            proc = subprocess.Popen(
                session.launch_argv,
                stdin=None,
                stdout=None,
                stderr=None,
                env=session.child_env,
                start_new_session=True,
            )
            forced_terminated = wait_for_process(proc, lambda: interrupted, float(env_value(env, "SIGNAL_GRACE", "2")))
        except PermissionError as exc:
            raise ObserveError(f"failed to start {launch_subject}: {exc}", 1) from exc
        except OSError as exc:
            raise ObserveError(f"failed to run {launch_subject}: {exc}", 1) from exc
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)  # type: ignore[arg-type]

    codex_code = normalize_exit_code(proc.returncode if proc is not None else 1)
    if interrupted and (codex_code == 0 or forced_terminated):
        codex_code = 128 + interrupted
    if "strace" in backend_names and codex_code == 1 and logs.trace_path.stat().st_size == 0:
        print(f"{error_prefix}: strace failed; ptrace may be denied by sandbox/seccomp/Yama", file=sys.stderr)
    for name in backends_in_finalize_order(backend_names):
        backend_map[name].stop(session)
    for name in backends_in_finalize_order(backend_names):
        backend_map[name].finalize(session, codex_code)

    try:
        safe_write_meta(
            logs.meta_path,
            build_session_meta(
                logs,
                session.state.parser_status,
                session.state.authoritative_path,
                session.state.meta_warnings,
                snapshot_summary=session.state.snapshot_summary,
                parser_source=session.state.parser_source,
            ),
            logs.observe_dir,
        )
    except Exception as exc:
        print(f"{error_prefix}: warning: could not write {logs.meta_path}: {exc}", file=sys.stderr)

    if session.state.parse_failed and env_flag(env, "STRICT_PARSE"):
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


def safe_write_meta(path: Path, data: dict, observe_dir: Path) -> None:
    verify_log_path_safe(path, observe_dir)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ObserveError(f"cannot safely write log path {path}: {exc}", 1) from exc
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        import json
        json.dump(data, fh, sort_keys=True, separators=(",", ":"))
        fh.write("\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def prepare_snapshot_plan(
    env: dict[str, str],
    logs: LogPaths,
    initial_cwd: str,
    active_artifacts: set[str],
    *,
    error_prefix: str,
) -> SnapshotPlan:
    roots, root_diags = parse_roots(env_value(env, "ROOTS"), cwd=initial_cwd)
    diagnostics: list[dict[str, Any]] = []
    warnings: list[str] = []
    _record_snapshot_diagnostics(root_diags, diagnostics, warnings, error_prefix=error_prefix)
    max_files, max_files_warning = _snapshot_max_files(env)
    if max_files_warning is not None:
        warnings.append(max_files_warning)
        print(f"{error_prefix}: warning: {max_files_warning}", file=sys.stderr)
    patterns = all_exclude_patterns(env_value(env, "SNAPSHOT_EXCLUDE"))
    patterns.extend(_artifact_exclude_patterns(roots, logs.observe_dir, active_artifacts))
    start_manifest: Manifest | None = None
    if roots:
        start_manifest = capture_manifest(
            roots,
            hash_files=env_flag(env, "SNAPSHOT_HASH"),
            exclude_patterns=patterns,
            max_files=max_files,
        )
        _record_snapshot_diagnostics(
            start_manifest.diagnostics,
            diagnostics,
            warnings,
            error_prefix=error_prefix,
        )
    return SnapshotPlan(
        roots=roots,
        exclude_patterns=patterns,
        hash_files=env_flag(env, "SNAPSHOT_HASH"),
        max_files=max_files,
        start_manifest=start_manifest,
        end_manifest=None,
        diagnostics=diagnostics,
        warnings=warnings,
        raw_events=[],
    )


def finalize_snapshot_plan(plan: SnapshotPlan, logs: LogPaths, *, error_prefix: str) -> SnapshotPlan:
    if not plan.roots or plan.start_manifest is None:
        return plan
    plan.end_manifest = capture_manifest(
        plan.roots,
        hash_files=plan.hash_files,
        exclude_patterns=plan.exclude_patterns,
        max_files=plan.max_files,
    )
    _record_snapshot_diagnostics(
        plan.end_manifest.diagnostics,
        plan.diagnostics,
        plan.warnings,
        error_prefix=error_prefix,
    )
    diff_records = diff_manifests(plan.start_manifest, plan.end_manifest)
    plan.raw_events = synthesize_events(
        diff_records,
        session_id=logs.session_id,
        invocation_id=logs.session_id,
        timestamp=_iso_utc_now(),
    )
    return plan


def merge_snapshot_events(
    logs: LogPaths,
    authoritative_path: Path | None,
    parser_status: str,
    snapshot_events: list[dict[str, Any]],
    *,
    error_prefix: str,
) -> tuple[Path | None, int]:
    if not snapshot_events:
        return authoritative_path, 0

    if authoritative_path in {logs.jsonl_path, logs.rebuilt_path}:
        target_path = authoritative_path
        direct_events = read_jsonl_events(target_path, logs.observe_dir)
        filtered = deduplicate_snapshot_events(snapshot_events, direct_events)
        if not filtered:
            return authoritative_path, 0
        safe_write_jsonl(target_path, [*direct_events, *filtered], logs.observe_dir)
        return authoritative_path, len(filtered)

    if logs.jsonl_path.exists() and logs.jsonl_path.stat().st_size == 0:
        safe_write_jsonl(logs.jsonl_path, snapshot_events, logs.observe_dir)
        return (logs.jsonl_path if snapshot_events else authoritative_path), len(snapshot_events)

    print(
        f"{error_prefix}: warning: snapshot events available but no safe canonical event artifact target was found",
        file=sys.stderr,
    )
    return authoritative_path, 0


def build_snapshot_summary(plan: SnapshotPlan) -> dict[str, Any]:
    manifests_complete = True
    if plan.start_manifest is not None:
        manifests_complete = manifests_complete and plan.start_manifest.complete
    if plan.end_manifest is not None:
        manifests_complete = manifests_complete and plan.end_manifest.complete
    return {
        "enabled": True,
        "source": "snapshot",
        "roots": [str(root) for root in plan.roots],
        "hash_files": plan.hash_files,
        "max_files": plan.max_files,
        "complete": bool(plan.roots) and manifests_complete and not plan.diagnostics,
        "diagnostics": plan.diagnostics,
        "raw_event_count": len(plan.raw_events),
        "emitted_event_count": 0,
    }


def build_session_meta(
    logs: LogPaths,
    parser_status: str,
    authoritative_path: Path | None,
    warnings: list[str],
    *,
    snapshot_summary: dict[str, Any] | None = None,
    parser_source: str = "strace",
) -> dict:
    def artifact(path: Path, role: str) -> dict:
        return {
            "path": path.name,
            "role": role,
            "exists": True if path == logs.meta_path else path.exists(),
        }

    if authoritative_path == logs.rebuilt_path:
        jsonl_role = "partial_live"
        rebuilt_role = "authoritative_complete"
        partial_role = "absent_or_parser_failure_partial"
    elif authoritative_path == logs.jsonl_path:
        jsonl_role = "authoritative_complete"
        rebuilt_role = "absent"
        partial_role = "absent_or_parser_failure_partial"
    else:
        if parser_status.startswith("live_timeout") or parser_status.startswith("live_error"):
            jsonl_role = "partial_live"
        else:
            jsonl_role = "inferred_or_empty_placeholder"
        rebuilt_role = "absent"
        partial_role = "partial_direct"

    meta = {
        "schema_version": 1,
        "session_id": logs.session_id,
        "parser": {
            "status": parser_status,
            "source": parser_source,
        },
        "artifacts": {
            "authoritative_event_path": authoritative_path.name if authoritative_path is not None else None,
            "trace": artifact(logs.trace_path, "trace"),
            "jsonl": artifact(logs.jsonl_path, jsonl_role),
            "partial": artifact(logs.partial_path, partial_role),
            "rebuilt": artifact(logs.rebuilt_path, rebuilt_role),
            "meta": artifact(logs.meta_path, "metadata"),
        },
        "warnings": warnings,
    }
    if snapshot_summary is not None:
        meta["snapshot"] = snapshot_summary
    return meta


def _record_snapshot_diagnostics(
    diags,
    diagnostics: list[dict[str, Any]],
    warnings: list[str],
    *,
    error_prefix: str,
) -> None:
    for diag in diags:
        diagnostics.append(diag.to_dict())
        warning = f"snapshot {diag.code}: {diag.message}"
        warnings.append(warning)
        print(f"{error_prefix}: warning: {warning}", file=sys.stderr)


def _snapshot_max_files(env: dict[str, str]) -> tuple[int | None, str | None]:
    raw = env_value(env, "SNAPSHOT_MAX_FILES")
    if raw in {None, ""}:
        return None, None
    try:
        value = int(raw)
    except ValueError:
        return None, f"snapshot max-files value is invalid and was ignored: {raw!r}"
    if value <= 0:
        return None, f"snapshot max-files value must be positive and was ignored: {raw!r}"
    return value, None


def _artifact_exclude_patterns(
    roots: list[Path],
    observe_dir: Path,
    active_artifacts: set[str],
) -> list[str]:
    patterns: list[str] = []
    for root in roots:
        try:
            rel_dir = observe_dir.resolve(strict=False).relative_to(root)
        except ValueError:
            rel_dir = None
        if rel_dir is not None:
            rel_dir_text = rel_dir.as_posix().strip("/")
            if rel_dir_text:
                patterns.append(f"{rel_dir_text}/**")
        for artifact in active_artifacts:
            try:
                rel = Path(artifact).relative_to(root)
            except ValueError:
                continue
            rel_text = rel.as_posix().strip("/")
            if rel_text:
                patterns.append(rel_text)
    return patterns


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


def safe_open_jsonl_read(path: Path, observe_dir: Path):
    return _safe_open_text_read(path, observe_dir, label="jsonl")


def safe_open_trace_read(path: Path, observe_dir: Path):
    return _safe_open_text_read(path, observe_dir, label="trace")


def _safe_open_text_read(path: Path, observe_dir: Path, *, label: str):
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
        raise ObserveError(f"cannot safely open {label} for read {path}: {exc}", 1) from exc
    try:
        return os.fdopen(fd, "r", encoding="utf-8", errors="replace")
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise ObserveError(f"cannot wrap read handle for {path}: {exc}", 1) from exc


def read_jsonl_events(path: Path, observe_dir: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with safe_open_jsonl_read(path, observe_dir) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

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
        try:
            return validate_real_candidate(candidate, wrapper_real, f"PATH {program}")
        except ObserveError as exc:
            if "resolves to observer shim" in str(exc):
                continue
            raise

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
    if is_observer_shim(path, wrapper_real):
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
        rebuilt_path = observe_dir / f"{session}.jsonl.rebuilt"
        meta_path = observe_dir / f"{session}.meta.json"
        if trace_path.exists() or jsonl_path.exists() or partial_path.exists() or rebuilt_path.exists() or meta_path.exists():
            continue
        try:
            verify_parent(trace_path, observe_dir)
            verify_parent(jsonl_path, observe_dir)
            verify_parent(partial_path, observe_dir)
            verify_parent(rebuilt_path, observe_dir)
            verify_parent(meta_path, observe_dir)
            exclusive_touch(trace_path)
            try:
                exclusive_touch(jsonl_path)
            except Exception:
                try:
                    trace_path.unlink()
                except OSError:
                    pass
                raise
            return LogPaths(observe_dir, session, trace_path, jsonl_path, partial_path, rebuilt_path, meta_path)
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
