"""Small strace-to-JSONL parser for ai-observe.

Parser deliberately supports fixture-defined strace line shapes. It favors safe
false negatives over false positives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable

SCHEMA_VERSION = 1


@dataclass
class ProcessState:
    cwd: str
    fds: dict[int, str] = field(default_factory=dict)
    writable_fds: set[int] = field(default_factory=set)
    ppid: int | None = None
    comm: str | None = None


@dataclass
class ParseResult:
    events: list[dict[str, Any]]
    errors: list[str] = field(default_factory=list)


class ParserFailure(RuntimeError):
    """Raised with partial events when parser is intentionally failed/tested."""

    def __init__(self, message: str, events: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.events = events or []


_PID_TS_RE = re.compile(r"^(?:(?P<pid>\d+)\s+)?(?:(?P<ts>\d+\.\d+)\s+)?(?P<body>.*)$")
_RESUMED_RE = re.compile(r"^<\.\.\.\s+(?P<name>\w+)\s+resumed>\s*(?P<rest>.*)$")
_SYSCALL_RE = re.compile(r"^(?P<name>\w+)\((?P<args>.*)\)\s+=\s+(?P<result>.+)$")
_UNFINISHED_RE = re.compile(r"^(?P<name>\w+)\((?P<partial>.*)<unfinished \.\.\.>$")
_FD_ANNOT_RE = re.compile(r"^(?P<fd>-?\d+)(?:<(?P<path>[^>]*)>)?$")


def parse_trace_file(
    trace_path: str | os.PathLike[str],
    jsonl_path: str | os.PathLike[str] | None,
    *,
    session_id: str,
    invocation_id: str | None = None,
    command: list[str] | None = None,
    initial_cwd: str | os.PathLike[str] | None = None,
    active_artifacts: Iterable[str | os.PathLike[str]] = (),
    include_log_writes: bool = False,
    fail_after_events: int | None = None,
) -> ParseResult:
    """Parse strace text and optionally write JSONL.

    `fail_after_events` exists for deterministic parser-failure tests.
    """
    parser = TraceParser(
        session_id=session_id,
        invocation_id=invocation_id or session_id,
        command=command or [],
        initial_cwd=str(Path(initial_cwd or os.getcwd()).resolve()),
        active_artifacts={str(Path(p).resolve()) for p in active_artifacts},
        include_log_writes=include_log_writes,
        fail_after_events=fail_after_events,
    )
    with open(trace_path, "r", encoding="utf-8", errors="replace") as fh:
        result = parser.parse_lines(fh)
    if jsonl_path is not None:
        write_jsonl(jsonl_path, result.events)
    return result


def dump_event(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"


def write_jsonl(path: str | os.PathLike[str], events: Iterable[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(dump_event(event))


class TraceParser:
    def __init__(
        self,
        *,
        session_id: str,
        invocation_id: str,
        command: list[str],
        initial_cwd: str,
        active_artifacts: set[str],
        include_log_writes: bool,
        fail_after_events: int | None = None,
    ) -> None:
        self.session_id = session_id
        self.invocation_id = invocation_id
        self.command = command
        self.initial_cwd = initial_cwd
        self.active_artifacts = active_artifacts
        self.include_log_writes = include_log_writes
        self.fail_after_events = fail_after_events
        self.states: dict[int | None, ProcessState] = {}
        self.unfinished: dict[tuple[int | None, str], tuple[str | None, str]] = {}
        self.events: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._emitted: int = 0

    def feed_line(self, raw_line: str) -> list[dict[str, Any]]:
        """Feed one trace line. Returns events newly appended by this call.

        Mirrors `parse_lines` semantics for a single line: blank lines are
        skipped; `ParserFailure` propagates; other exceptions are captured
        into `self.errors`.
        """
        line = raw_line.rstrip("\n")
        if not line.strip():
            return []
        try:
            self._parse_line(line)
        except ParserFailure:
            raise
        except Exception as exc:  # safe false-negative
            self.errors.append(f"skip line: {exc}: {line}")
        new_events = self.events[self._emitted:]
        self._emitted = len(self.events)
        return new_events

    def parse_lines(self, lines: Iterable[str]) -> ParseResult:
        for raw_line in lines:
            self.feed_line(raw_line)
        return ParseResult(self.events, self.errors)

    def _parse_line(self, line: str) -> None:
        pid, ts, body = self._split_prefix(line)
        resumed = _RESUMED_RE.match(body)
        if resumed:
            key = (pid, resumed.group("name"))
            if key not in self.unfinished:
                self.errors.append(f"unmatched resumed: {line}")
                return
            old_ts, partial = self.unfinished.pop(key)
            ts = old_ts or ts
            body = partial + resumed.group("rest")

        unfinished = _UNFINISHED_RE.match(body)
        if unfinished:
            key = (pid, unfinished.group("name"))
            self.unfinished[key] = (ts, f"{unfinished.group('name')}({unfinished.group('partial')}")
            return

        match = _SYSCALL_RE.match(body)
        if not match:
            self.errors.append(f"unparsed body: {line}")
            return

        name = match.group("name")
        args = split_args(match.group("args"))
        result_text = match.group("result").strip()
        result_value, result_path = parse_result(result_text)
        if is_failed_result(result_text, result_value):
            return

        self._update_process_state(pid, name, args, result_value, result_path)
        event = self._event_for(pid, ts, name, args, result_value, result_text, body, result_path)
        if event is None:
            return
        if self._drop_artifact_event(event):
            return
        self.events.append(event)
        if self.fail_after_events is not None and len(self.events) >= self.fail_after_events:
            raise ParserFailure("injected parser failure", self.events.copy())

    def _split_prefix(self, line: str) -> tuple[int | None, str | None, str]:
        parts = line.split(maxsplit=2)
        if not parts:
            return None, None, ""
        pid: int | None = None
        ts: str | None = None
        if len(parts) >= 3 and parts[0].isdigit() and is_float(parts[1]):
            return int(parts[0]), parts[1], parts[2]
        if len(parts) >= 2 and is_float(parts[0]):
            return None, parts[0], line.split(maxsplit=1)[1]
        if len(parts) >= 2 and parts[0].isdigit() and (parts[1].startswith("<...") or "(" in parts[1]):
            return int(parts[0]), None, line.split(maxsplit=1)[1]
        return None, None, line

    def _state(self, pid: int | None) -> ProcessState:
        if pid not in self.states:
            self.states[pid] = ProcessState(cwd=self.initial_cwd)
        return self.states[pid]

    def _update_process_state(
        self,
        pid: int | None,
        name: str,
        args: list[str],
        result_value: int | None,
        result_path: str | None,
    ) -> None:
        state = self._state(pid)
        if name in {"fork", "vfork", "clone", "clone3"} and result_value and result_value > 0:
            child = result_value
            self.states[child] = ProcessState(
                cwd=state.cwd,
                fds=dict(state.fds),
                writable_fds=set(state.writable_fds),
                ppid=pid,
            )
            return
        if name == "chdir" and args:
            path = self._path_from_arg(pid, args[0])
            if path:
                state.cwd = path
            return
        if name == "fchdir" and args:
            fd = fd_number(args[0])
            if fd is not None and fd in state.fds:
                state.cwd = state.fds[fd]
            return
        if name in {"open", "openat", "openat2", "creat"} and result_value is not None and result_value >= 0:
            path, flags = self._open_path_flags(pid, name, args)
            if result_path:
                path = result_path
            if path:
                state.fds[result_value] = path
                if name == "creat" or flags_writable(flags):
                    state.writable_fds.add(result_value)
            return
        if name == "close" and args:
            fd = fd_number(args[0])
            if fd is not None:
                state.fds.pop(fd, None)
                state.writable_fds.discard(fd)

    def _event_for(
        self,
        pid: int | None,
        ts: str | None,
        name: str,
        args: list[str],
        result_value: int | None,
        result_text: str,
        raw_syscall: str,
        result_path: str | None,
    ) -> dict[str, Any] | None:
        op: str | None = None
        path: str | None = None
        old_path: str | None = None
        new_path: str | None = None

        if name == "creat":
            op = "create"
            path = self._path_from_arg(pid, args[0]) if args else None
        elif name in {"open", "openat", "openat2"}:
            path, flags = self._open_path_flags(pid, name, args)
            if result_path:
                path = result_path
            if "O_CREAT" in flags and "O_EXCL" in flags:
                op = "create"
            elif "O_TRUNC" in flags and flags_writable(flags):
                op = "modify"
        elif name in {"write", "pwrite64", "pwritev", "pwritev2", "writev"}:
            if result_value is None or result_value <= 0 or not args:
                return None
            fd = fd_number(args[0])
            state = self._state(pid)
            if fd is None or fd not in state.writable_fds:
                return None
            op = "modify"
            path = state.fds.get(fd) or fd_path_annotation(args[0])
        elif name in {"truncate", "truncate64"}:
            op = "modify"
            path = self._path_from_arg(pid, args[0]) if args else None
        elif name in {"ftruncate", "ftruncate64", "fallocate"}:
            if not args:
                return None
            fd = fd_number(args[0])
            op = "modify"
            path = self._state(pid).fds.get(fd) if fd is not None else fd_path_annotation(args[0])
        elif name in {"unlink", "unlinkat", "rmdir"}:
            op = "delete"
            path = self._at_path(pid, args, 0 if name != "unlinkat" else 1, None if name != "unlinkat" else 0)
        elif name in {"rename", "renameat", "renameat2"}:
            op = "rename"
            if name == "rename":
                old_path = self._path_from_arg(pid, args[0]) if len(args) > 0 else None
                new_path = self._path_from_arg(pid, args[1]) if len(args) > 1 else None
            else:
                old_path = self._at_path(pid, args, 1, 0)
                new_path = self._at_path(pid, args, 3, 2)
            path = new_path
        elif name in {"chmod", "fchmod", "fchmodat"}:
            op = "chmod"
            if name == "fchmod":
                fd = fd_number(args[0]) if args else None
                path = self._state(pid).fds.get(fd) if fd is not None else None
            elif name == "fchmodat":
                path = self._at_path(pid, args, 1, 0)
            else:
                path = self._path_from_arg(pid, args[0]) if args else None
        elif name in {"chown", "lchown", "fchown", "fchownat", "utime", "utimes", "utimensat", "futimesat"}:
            op = "metadata"
            if name in {"fchown"}:
                fd = fd_number(args[0]) if args else None
                path = self._state(pid).fds.get(fd) if fd is not None else None
            elif name in {"fchownat", "utimensat", "futimesat"}:
                path = self._at_path(pid, args, 1, 0)
            else:
                path = self._path_from_arg(pid, args[0]) if args else None
        elif name in {"mkdir", "mkdirat", "mknod", "mknodat"}:
            op = "create"
            if name.endswith("at"):
                path = self._at_path(pid, args, 1, 0)
            else:
                path = self._path_from_arg(pid, args[0]) if args else None
        elif name in {"symlink", "symlinkat"}:
            op = "create"
            path = self._path_from_arg(pid, args[1]) if name == "symlink" and len(args) > 1 else self._at_path(pid, args, 2, 1)
        elif name in {"link", "linkat"}:
            op = "create"
            path = self._path_from_arg(pid, args[1]) if name == "link" and len(args) > 1 else self._at_path(pid, args, 3, 2)

        if op is None:
            return None
        return self._make_event(pid, ts, op, path, old_path, new_path, raw_syscall, result_value if result_value is not None else result_text)

    def _make_event(
        self,
        pid: int | None,
        ts: str | None,
        operation: str,
        path: str | None,
        old_path: str | None,
        new_path: str | None,
        raw_syscall: str,
        result: Any,
    ) -> dict[str, Any]:
        state = self._state(pid)
        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp": timestamp_to_iso(ts),
            "session_id": self.session_id,
            "invocation_id": self.invocation_id,
            "pid": pid,
            "process": {"pid": pid, "ppid": state.ppid, "comm": state.comm},
            "operation": operation,
            "path": path,
            "old_path": old_path,
            "new_path": new_path,
            "command": self.command,
            "raw_syscall": raw_syscall,
            "result": result,
        }

    def _drop_artifact_event(self, event: dict[str, Any]) -> bool:
        if self.include_log_writes:
            return False
        paths = [event.get("path"), event.get("old_path"), event.get("new_path")]
        return any(p in self.active_artifacts for p in paths if p)

    def _open_path_flags(self, pid: int | None, name: str, args: list[str]) -> tuple[str | None, str]:
        if name == "creat":
            return (self._path_from_arg(pid, args[0]) if args else None, "O_WRONLY|O_CREAT|O_TRUNC")
        if name == "open":
            return (self._path_from_arg(pid, args[0]) if args else None, args[1] if len(args) > 1 else "")
        if name == "openat":
            return (self._at_path(pid, args, 1, 0), args[2] if len(args) > 2 else "")
        if name == "openat2":
            return (self._at_path(pid, args, 1, 0), args[2] if len(args) > 2 else "")
        return None, ""

    def _path_from_arg(self, pid: int | None, arg: str) -> str | None:
        path = unquote_arg(arg)
        if path is None:
            return fd_path_annotation(arg)
        if os.path.isabs(path):
            return normalize_abs_path(path)
        return normalize_abs_path(Path(self._state(pid).cwd, path))

    def _at_path(self, pid: int | None, args: list[str], path_index: int, dirfd_index: int | None) -> str | None:
        if len(args) <= path_index:
            return None
        raw_path = unquote_arg(args[path_index])
        if raw_path is None:
            return fd_path_annotation(args[path_index])
        if os.path.isabs(raw_path):
            return normalize_abs_path(raw_path)
        base: str | None = self._state(pid).cwd
        if dirfd_index is not None and len(args) > dirfd_index and args[dirfd_index].strip() != "AT_FDCWD":
            base = self._dirfd_path(pid, args[dirfd_index])
        if base is None:
            return None
        return normalize_abs_path(Path(base, raw_path))

    def _dirfd_path(self, pid: int | None, arg: str) -> str | None:
        annotated = fd_path_annotation(arg)
        if annotated:
            return annotated
        fd = fd_number(arg)
        if fd is None:
            return None
        return self._state(pid).fds.get(fd)


def split_args(text: str) -> list[str]:
    args: list[str] = []
    cur: list[str] = []
    in_quote = False
    escape = False
    angle_depth = 0
    bracket_depth = 0
    for ch in text:
        if escape:
            cur.append(ch)
            escape = False
            continue
        if ch == "\\" and in_quote:
            cur.append(ch)
            escape = True
            continue
        if ch == '"':
            in_quote = not in_quote
            cur.append(ch)
            continue
        if not in_quote:
            if ch == "<":
                angle_depth += 1
            elif ch == ">" and angle_depth:
                angle_depth -= 1
            elif ch in "[{(":
                bracket_depth += 1
            elif ch in "]})" and bracket_depth:
                bracket_depth -= 1
            elif ch == "," and angle_depth == 0 and bracket_depth == 0:
                args.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    if cur or text:
        args.append("".join(cur).strip())
    return args


def unquote_arg(arg: str) -> str | None:
    arg = arg.strip()
    if not arg.startswith('"'):
        return None
    out: list[str] = []
    escape = False
    for ch in arg[1:]:
        if escape:
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
            out.append(mapping.get(ch, ch))
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == '"':
            return "".join(out)
        else:
            out.append(ch)
    return "".join(out)


def parse_result(text: str) -> tuple[int | None, str | None]:
    token = text.split()[0] if text.split() else text
    m = _FD_ANNOT_RE.match(token)
    if not m:
        try:
            return int(token), None
        except ValueError:
            return None, None
    value = int(m.group("fd"))
    return value, m.group("path")


def is_failed_result(text: str, value: int | None) -> bool:
    return value == -1 or text.lstrip().startswith("-1 ")


def fd_number(arg: str) -> int | None:
    m = _FD_ANNOT_RE.match(arg.strip())
    if not m:
        return None
    return int(m.group("fd"))


def fd_path_annotation(arg: str) -> str | None:
    m = _FD_ANNOT_RE.match(arg.strip())
    if not m:
        return None
    return m.group("path")


def flags_writable(flags: str) -> bool:
    return "O_WRONLY" in flags or "O_RDWR" in flags


def timestamp_to_iso(ts: str | None) -> str:
    if ts is None:
        now = time.time()
    else:
        try:
            now = float(ts)
        except ValueError:
            now = time.time()
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def is_float(value: str) -> bool:
    try:
        float(value)
        return "." in value
    except ValueError:
        return False


def normalize_abs_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.fspath(path))
