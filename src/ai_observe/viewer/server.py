"""Loopback-only HTTP server for the ai_observe viewer.

Serves a small static UI and exposes a Server-Sent Events stream at
`/events`. Each SSE client first receives the full backlog of events (replay
from offset 0) under a snapshot watermark, then receives new events as they
arrive — with no gaps or duplicates.

Per the spec:

- Binds only to 127.0.0.1. No flag to change this.
- Tab title is fixed; sensitive fields are never sent to the page (see
  `tailer.sanitize_event`).
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import time
from pathlib import Path
from typing import List, Optional

from .tailer import JsonlTailer


_HOST = "127.0.0.1"
_STATIC_DIR = Path(__file__).resolve().parent / "static"


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


def _build_handler(broadcaster: _Broadcaster):
    static_dir = _STATIC_DIR

    class Handler(http.server.BaseHTTPRequestHandler):
        # Silence default access logs to stderr; the viewer must not log
        # request paths (which could include query strings) to disk anyway.
        def log_message(self, format, *args):  # noqa: A003 - stdlib signature
            return

        def do_GET(self):  # noqa: N802 - stdlib signature
            if self.path == "/" or self.path == "/index.html":
                self._serve_static("index.html", "text/html; charset=utf-8")
                return
            if self.path.startswith("/static/"):
                name = self.path[len("/static/") :]
                if "/" in name or ".." in name:
                    self.send_error(404)
                    return
                ctype = self._guess_content_type(name)
                self._serve_static(name, ctype)
                return
            if self.path == "/events":
                self._serve_events()
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

        def _serve_events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            # Replay snapshot, then live tail.
            n = broadcaster.snapshot_len()
            try:
                self._send_batch("append", broadcaster.slice(0, n))
                while True:
                    has_more = broadcaster.wait_for_more(n, timeout=1.0)
                    if not has_more:
                        # Shutdown: send a final frame so the browser stops reconnecting.
                        self._send_event("shutdown", {})
                        return
                    new_end = broadcaster.snapshot_len()
                    if new_end > n:
                        self._send_batch("append", broadcaster.slice(n, new_end))
                        n = new_end
            except (BrokenPipeError, ConnectionResetError):
                return

        def _send_batch(self, kind: str, events: list) -> None:
            for ev in events:
                self._send_event(kind, ev)

        def _send_event(self, kind: str, payload: dict) -> None:
            data = json.dumps(payload, separators=(",", ":"))
            chunk = f"event: {kind}\ndata: {data}\n\n".encode("utf-8")
            self.wfile.write(chunk)
            self.wfile.flush()

    return Handler


class ViewerServer:
    """Loopback-only viewer HTTP server. Use as a context manager or call
    `start()` / `stop()` explicitly. Listens on an OS-chosen port unless one
    is supplied."""

    def __init__(self, jsonl_path: Path, port: int = 0, poll_interval: float = 0.25) -> None:
        self._path = Path(jsonl_path)
        self._broadcaster = _Broadcaster()
        self._tailer = JsonlTailer(
            self._path, on_event=self._broadcaster.append, poll_interval=poll_interval
        )
        handler_cls = _build_handler(self._broadcaster)

        # Set SO_REUSEADDR before bind to actually reduce TIME_WAIT pain when
        # tests recycle ports rapidly. ThreadingHTTPServer's superclass binds
        # in __init__, so we subclass to flip the flag before bind.
        class _ReuseHTTPServer(http.server.ThreadingHTTPServer):
            allow_reuse_address = True

        # ThreadingHTTPServer gives us one thread per request, which is what
        # SSE clients need.
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

    def start(self) -> None:
        self._tailer.start()

        self._serve_thread = threading.Thread(
            target=self._httpd.serve_forever, name="ViewerServer", daemon=True
        )
        self._serve_thread.start()
        # Block until the server is actually accepting connections. Without
        # this, a fast stop() races the serve_forever selector setup and
        # leaves a stray Exception-in-thread traceback on stderr.
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

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        # Signal SSE clients to wind down first so their wait loops exit.
        self._broadcaster.shutdown()
        try:
            self._httpd.shutdown()
        except Exception:  # noqa: BLE001 - defensive
            pass
        try:
            self._httpd.server_close()
        except OSError:
            pass
        if self._serve_thread is not None:
            self._serve_thread.join(timeout=5.0)
        self._tailer.stop()

    def __enter__(self) -> "ViewerServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


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
