"""Loopback-only HTTP server for the ai_observe viewer.

Serves a small static UI, a sanitized session-status JSON document at
`/session`, and a Server-Sent Events stream at `/events`.

Each SSE client selects one event artifact (`.jsonl`, `.jsonl.rebuilt`, or
`.jsonl.partial`). The server replays that artifact's full sanitized backlog,
then streams live appended events with no gaps or duplicates.

Per the spec:

- Binds only to 127.0.0.1. No flag to change this.
- Tab title is fixed; sensitive fields are never sent to the page (see
  `tailer.sanitize_event`).
- Meta/artifact status exposed to the browser is sanitized and never includes
  raw syscalls, argv, PID/process details, or full manifest contents.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlsplit

from .tailer import JsonlTailer


_HOST = "127.0.0.1"
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_APPEND_BATCH_SIZE = 512
_EVENT_ARTIFACT_KEYS = ("jsonl", "rebuilt", "partial")


@dataclass(frozen=True)
class _SessionPaths:
    requested_artifact: str
    jsonl: Path
    rebuilt: Path
    partial: Path
    meta: Path

    def artifact_map(self) -> dict[str, Path]:
        return {
            "jsonl": self.jsonl,
            "rebuilt": self.rebuilt,
            "partial": self.partial,
            "meta": self.meta,
        }


def _event_batches(events: List[dict], batch_size: Optional[int] = None):
    """Yield bounded, non-empty event batches in original order."""
    if batch_size is None:
        batch_size = _APPEND_BATCH_SIZE
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    for start in range(0, len(events), batch_size):
        batch = events[start : start + batch_size]
        if batch:
            yield batch


class _Broadcaster:
    """Append-only event log with a condition for live waiters.

    Each SSE client snapshots `len(events)` under the lock, sends the
    backlog, then loops on the condition to pick up new entries.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._events: List[dict] = []
        self._shutdown = False

    def append(self, event: dict) -> None:
        with self._cond:
            self._events.append(event)
            self._cond.notify_all()

    def shutdown(self) -> None:
        with self._cond:
            self._shutdown = True
            self._cond.notify_all()

    def snapshot_len(self) -> int:
        with self._lock:
            return len(self._events)

    def slice(self, start: int, end: int) -> List[dict]:
        with self._lock:
            return list(self._events[start:end])

    def wait_for_more(self, current_len: int, timeout: float = 1.0) -> bool:
        """Block until more events exist or shutdown. Returns False on shutdown."""
        with self._cond:
            if self._shutdown:
                return False
            if len(self._events) > current_len:
                return True
            self._cond.wait(timeout=timeout)
            return not self._shutdown


class _ArtifactStream:
    def __init__(self, path: Path, poll_interval: float) -> None:
        self._path = Path(path)
        self._broadcaster = _Broadcaster()
        self._tailer = JsonlTailer(
            self._path,
            on_event=self._broadcaster.append,
            poll_interval=poll_interval,
        )

    @property
    def broadcaster(self) -> _Broadcaster:
        return self._broadcaster

    def start(self) -> None:
        self._tailer.start()

    def stop(self) -> None:
        self._broadcaster.shutdown()
        self._tailer.stop()


def _resolve_session_paths(path: Path) -> _SessionPaths:
    path = Path(path)
    requested_artifact = "jsonl"
    if path.name.endswith(".jsonl.partial"):
        requested_artifact = "partial"
        jsonl_path = path.with_name(path.name[: -len(".partial")])
    elif path.name.endswith(".jsonl.rebuilt"):
        requested_artifact = "rebuilt"
        jsonl_path = path.with_name(path.name[: -len(".rebuilt")])
    elif path.name.endswith(".meta.json"):
        stem = path.name[: -len(".meta.json")]
        jsonl_path = path.with_name(f"{stem}.jsonl")
    else:
        jsonl_path = path

    meta_name = (
        f"{jsonl_path.name[: -len('.jsonl')]}.meta.json"
        if jsonl_path.name.endswith(".jsonl")
        else f"{jsonl_path.name}.meta.json"
    )
    return _SessionPaths(
        requested_artifact=requested_artifact,
        jsonl=jsonl_path,
        rebuilt=jsonl_path.with_name(f"{jsonl_path.name}.rebuilt"),
        partial=jsonl_path.with_name(f"{jsonl_path.name}.partial"),
        meta=jsonl_path.with_name(meta_name),
    )


def _read_meta(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _artifact_key_for_name(paths: _SessionPaths, filename: str | None) -> str | None:
    if not filename:
        return None
    for key in _EVENT_ARTIFACT_KEYS:
        if paths.artifact_map()[key].name == filename:
            return key
    return None


def _sanitize_snapshot_summary(snapshot: dict | None) -> dict | None:
    if not isinstance(snapshot, dict):
        return None
    return {
        "enabled": bool(snapshot.get("enabled")),
        "complete": bool(snapshot.get("complete")),
        "diagnostic_count": len(snapshot.get("diagnostics") or []),
        "emitted_event_count": int(snapshot.get("emitted_event_count") or 0),
    }


def _default_artifact_role(key: str, exists: bool) -> str:
    if key == "jsonl":
        return "authoritative_complete" if exists else "absent"
    return "present_without_meta" if exists else "absent"


def _default_selected_artifact(paths: _SessionPaths, authoritative_artifact: str | None) -> str:
    requested = paths.requested_artifact
    if requested in {"partial", "rebuilt"}:
        return requested
    if authoritative_artifact in {"jsonl", "rebuilt"}:
        return authoritative_artifact
    return "jsonl"


def _build_session_info(paths: _SessionPaths) -> dict:
    meta = _read_meta(paths.meta)
    meta_artifacts = meta.get("artifacts") if isinstance(meta, dict) else None
    if not isinstance(meta_artifacts, dict):
        meta_artifacts = {}

    authoritative_name = meta_artifacts.get("authoritative_event_path")
    authoritative_artifact = _artifact_key_for_name(paths, authoritative_name)
    selected_artifact = _default_selected_artifact(paths, authoritative_artifact)

    artifact_map = paths.artifact_map()
    artifacts = {}
    for key in (*_EVENT_ARTIFACT_KEYS, "meta"):
        path = artifact_map[key]
        meta_entry = meta_artifacts.get(key)
        role = meta_entry.get("role") if isinstance(meta_entry, dict) else _default_artifact_role(key, path.exists())
        artifacts[key] = {
            "path": path.name,
            "exists": path.exists(),
            "role": role,
            "kind": "event" if key in _EVENT_ARTIFACT_KEYS else "metadata",
        }

    return {
        "requested_artifact": paths.requested_artifact,
        "default_artifact": selected_artifact,
        "authoritative_artifact": authoritative_artifact,
        "parser_status": (meta.get("parser") or {}).get("status") if isinstance(meta, dict) else None,
        "warnings_count": len(meta.get("warnings") or []) if isinstance(meta, dict) else 0,
        "snapshot": _sanitize_snapshot_summary(meta.get("snapshot") if isinstance(meta, dict) else None),
        "artifacts": artifacts,
    }


def _build_handler(server: "ViewerServer"):
    static_dir = _STATIC_DIR

    class Handler(http.server.BaseHTTPRequestHandler):
        # Silence default access logs to stderr; the viewer must not log
        # request paths (which could include query strings) to disk anyway.
        def log_message(self, format, *args):  # noqa: A003 - stdlib signature
            return

        def do_GET(self):  # noqa: N802 - stdlib signature
            parsed = urlsplit(self.path)
            route = parsed.path
            if route == "/" or route == "/index.html":
                self._serve_static("index.html", "text/html; charset=utf-8")
                return
            if route.startswith("/static/"):
                name = route[len("/static/") :]
                if "/" in name or ".." in name:
                    self.send_error(404)
                    return
                ctype = self._guess_content_type(name)
                self._serve_static(name, ctype)
                return
            if route == "/session":
                self._serve_session()
                return
            if route == "/events":
                artifact = parse_qs(parsed.query).get("artifact", [None])[0]
                self._serve_events(artifact)
                return
            self.send_error(404)

        # ----- helpers -----

        def _guess_content_type(self, name: str) -> str:
            if name.endswith(".js"):
                return "application/javascript; charset=utf-8"
            if name.endswith(".css"):
                return "text/css; charset=utf-8"
            if name.endswith(".html"):
                return "text/html; charset=utf-8"
            return "application/octet-stream"

        def _serve_static(self, name: str, content_type: str) -> None:
            path = static_dir / name
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _serve_session(self) -> None:
            payload = json.dumps(server.session_info(), separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _serve_events(self, artifact: str | None) -> None:
            try:
                broadcaster = server.broadcaster_for(artifact)
            except ValueError:
                self.send_error(400)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            n = broadcaster.snapshot_len()
            try:
                self._send_append_batches(broadcaster.slice(0, n))
                while True:
                    has_more = broadcaster.wait_for_more(n, timeout=1.0)
                    if not has_more:
                        self._send_event("shutdown", {})
                        return
                    new_end = broadcaster.snapshot_len()
                    if new_end > n:
                        self._send_append_batches(broadcaster.slice(n, new_end))
                        n = new_end
            except (BrokenPipeError, ConnectionResetError):
                return

        def _send_append_batches(self, events: list) -> None:
            for batch in _event_batches(events):
                self._send_event("append_batch", batch)

        def _send_event(self, kind: str, payload) -> None:
            data = json.dumps(payload, separators=(",", ":"))
            chunk = f"event: {kind}\ndata: {data}\n\n".encode("utf-8")
            self.wfile.write(chunk)
            self.wfile.flush()

    return Handler


class ViewerServer:
    """Loopback-only viewer HTTP server.

    The viewer can present multiple sibling event artifacts from a single
    session (`.jsonl`, `.jsonl.rebuilt`, `.jsonl.partial`). The initial path
    still controls the default selection, preserving the v1 CLI surface while
    allowing phase-5 artifact banners and switching.
    """

    def __init__(self, jsonl_path: Path, port: int = 0, poll_interval: float = 0.25) -> None:
        self._path = Path(jsonl_path)
        self._session_paths = _resolve_session_paths(self._path)
        self._streams = {
            key: _ArtifactStream(getattr(self._session_paths, key), poll_interval)
            for key in _EVENT_ARTIFACT_KEYS
        }
        handler_cls = _build_handler(self)

        class _ReuseHTTPServer(http.server.ThreadingHTTPServer):
            allow_reuse_address = True

        self._httpd = _ReuseHTTPServer((_HOST, port), handler_cls)
        self._serve_thread: Optional[threading.Thread] = None
        self._serving = threading.Event()
        self._stopped = False

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address
        return f"http://{host}:{port}/"

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def session_info(self) -> dict:
        return _build_session_info(self._session_paths)

    def broadcaster_for(self, artifact: str | None) -> _Broadcaster:
        if artifact in {None, "", "auto"}:
            key = self.session_info()["default_artifact"]
        else:
            key = artifact
        if key not in self._streams:
            raise ValueError(f"unknown artifact: {artifact!r}")
        return self._streams[key].broadcaster

    def start(self) -> None:
        for stream in self._streams.values():
            stream.start()

        self._serve_thread = threading.Thread(
            target=self._serve_forever,
            name="ViewerServer",
            daemon=True,
        )
        self._serve_thread.start()
        host, port = self._httpd.server_address
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    self._serving.set()
                    break
            except OSError:
                time.sleep(0.02)
        else:
            raise RuntimeError("ViewerServer failed to start accepting connections")

    def _serve_forever(self) -> None:
        # Thread target wrapping the stdlib accept loop. A stop() that races
        # ahead of this loop's socket/selector setup can surface from inside
        # the thread as a ValueError ("Invalid file descriptor: -1") or an
        # OSError. When we are already stopping that is benign teardown noise,
        # so swallow it rather than let the thread print an unhandled
        # traceback. Anything raised while NOT stopping is a genuine serve
        # error and is re-raised unchanged.
        try:
            self._httpd.serve_forever()
        except (ValueError, OSError):
            if not self._stopped:
                raise

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        for stream in self._streams.values():
            stream.broadcaster.shutdown()
        try:
            self._httpd.shutdown()
        except Exception:  # noqa: BLE001 - defensive
            pass
        # Join the serve thread BEFORE closing the listening socket. On an
        # immediate start->stop the serve loop may not have reached its
        # selector.register() yet; closing the socket first makes that
        # register() see a -1 fd and raise inside the thread. Waiting for the
        # thread to finish guarantees it is done touching the socket, so the
        # close below cannot race the accept-loop setup.
        if self._serve_thread is not None:
            _join_thread_safely(self._serve_thread, timeout=5.0)
        try:
            self._httpd.server_close()
        except OSError:
            pass
        for stream in self._streams.values():
            stream.stop()

    def __enter__(self) -> "ViewerServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def _join_thread_safely(thread: threading.Thread, timeout: float) -> None:
    """Join *thread*, tolerating a benign CPython thread-teardown race.

    ``Thread.join()`` can intermittently raise ``AssertionError`` from
    ``threading._wait_for_tstate_lock`` (``assert self._is_stopped``) when the
    joined thread clears its C-level tstate lock concurrently with the join —
    a known CPython race (see gh-89322 / bpo-45274). When it fires, the joined
    thread's OS-level lock is already gone, so the thread has in fact finished;
    only the Python-side ``_is_stopped`` flag has not yet been observed as set.

    We therefore treat the assertion as "thread is terminating": let its state
    converge with brief bounded retries and return once it is no longer alive
    (or the deadline passes). The serve thread is a daemon, so returning while
    it is momentarily still visible as alive never blocks interpreter exit.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        try:
            thread.join(timeout=remaining if remaining > 0 else 0.0)
            return
        except AssertionError:
            # Benign tstate-lock race; fall through to re-check liveness.
            pass
        try:
            alive = thread.is_alive()
        except AssertionError:
            # is_alive() shares the same _wait_for_tstate_lock path; the state
            # is still converging, so treat as alive and retry.
            alive = True
        if not alive or time.monotonic() >= deadline:
            return
        time.sleep(0.01)


def assert_loopback(host: str) -> None:
    """Reject any host string that resolves to a non-loopback address.

    Used by the CLI to enforce the spec's "loopback only" rule even though
    no flag exposes the choice.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"host {host!r} could not be resolved: {exc}") from exc
    for family, _t, _p, _c, sockaddr in infos:
        ip = sockaddr[0]
        if family == socket.AF_INET and not ip.startswith("127."):
            raise ValueError(f"host {host!r} resolves to non-loopback {ip}")
        if family == socket.AF_INET6 and ip not in {"::1", "::ffff:127.0.0.1"}:
            raise ValueError(f"host {host!r} resolves to non-loopback {ip}")
