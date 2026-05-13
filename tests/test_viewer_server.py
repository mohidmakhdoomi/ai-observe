from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
import urllib.request
import urllib.error
import socket as _socket

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_observe.viewer.server import ViewerServer, assert_loopback
from ai_observe.viewer import __main__ as cli


def _make_event(path="/x", op="modify", result=10, idx=0):
    return {
        "schema_version": 1,
        "timestamp": f"2026-05-13T10:00:{idx:02d}.000000Z",
        "session_id": "s",
        "invocation_id": "s",
        "pid": 1,
        "process": {"pid": 1, "ppid": 0, "comm": None},
        "operation": op,
        "path": path,
        "old_path": None,
        "new_path": None,
        "command": ["codex"],
        "raw_syscall": "SECRET",
        "result": result,
    }


def _append_events(path: Path, events):
    with open(path, "ab") as fh:
        for ev in events:
            fh.write(json.dumps(ev).encode("utf-8"))
            fh.write(b"\n")


def _open_sse(url, timeout=2.0):
    """Open a streaming SSE connection via a raw socket. Returns
    `(sock, file)`, where `file` is a buffered binary reader bound to
    the same socket. Caller is responsible for closing the socket."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    sock = _socket.create_connection((parsed.hostname, parsed.port), timeout=timeout)
    req = (
        f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
        f"Connection: close\r\nAccept: text/event-stream\r\n\r\n"
    ).encode("ascii")
    sock.sendall(req)
    fh = sock.makefile("rb")
    # Read and discard headers.
    while True:
        line = fh.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
    return sock, fh


def _read_sse_frames(fh, n, timeout=5.0):
    """Read n SSE 'append' frames from a buffered SSE file handle."""
    deadline = time.monotonic() + timeout
    frames = []
    cur_lines = []
    while len(frames) < n and time.monotonic() < deadline:
        line = fh.readline()
        if not line:
            break
        line = line.rstrip(b"\r\n")
        if line == b"":
            evt_type = None
            data = None
            for raw_line in cur_lines:
                s = raw_line.decode("utf-8", errors="replace")
                if s.startswith("event: "):
                    evt_type = s[len("event: "):]
                elif s.startswith("data: "):
                    data = s[len("data: "):]
            if evt_type == "append" and data is not None:
                frames.append(json.loads(data))
            cur_lines = []
        else:
            cur_lines.append(line)
    return frames


class ViewerServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "events.jsonl"
        self.path.touch()

    def _server(self):
        srv = ViewerServer(self.path, port=0, poll_interval=0.05)
        srv.start()
        self.addCleanup(srv.stop)
        return srv

    def test_index_serves_html_with_fixed_title(self):
        srv = self._server()
        with urllib.request.urlopen(srv.url, timeout=2.0) as resp:
            body = resp.read().decode("utf-8")
        self.assertIn("<title>ai_observe viewer</title>", body)
        # No raw_syscall / command leakage from any static asset.
        self.assertNotIn("raw_syscall", body)
        self.assertNotIn("innerHTML", body)

    def test_sse_replay_then_live(self):
        # Pre-write some events; the tailer will pick them up.
        _append_events(self.path, [_make_event(path=f"/r{i}", idx=i) for i in range(3)])
        srv = self._server()
        # Wait briefly so the tailer has caught up.
        time.sleep(0.2)
        sock, fh = _open_sse(srv.url + "events", timeout=3.0)
        try:
            backlog = _read_sse_frames(fh, 3, timeout=3.0)
            self.assertEqual([f["path"] for f in backlog], ["/r0", "/r1", "/r2"])
            # SSE payload must contain only the whitelisted fields.
            for f in backlog:
                self.assertEqual(
                    sorted(f.keys()),
                    sorted(["timestamp", "operation", "path", "old_path", "new_path", "result"]),
                )
            # Now append new events and read them live.
            _append_events(self.path, [_make_event(path="/live", idx=9)])
            live = _read_sse_frames(fh, 1, timeout=3.0)
            self.assertEqual(live[0]["path"], "/live")
        finally:
            sock.close()

    def test_two_concurrent_clients_each_get_full_replay(self):
        _append_events(self.path, [_make_event(path=f"/r{i}", idx=i) for i in range(2)])
        srv = self._server()
        time.sleep(0.2)

        results = [[], []]
        errors = []
        socks = []

        def consume(idx, expect):
            try:
                sock, fh = _open_sse(srv.url + "events", timeout=3.0)
                socks.append(sock)
                results[idx] = _read_sse_frames(fh, expect, timeout=4.0)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=consume, args=(0, 3))
        t2 = threading.Thread(target=consume, args=(1, 3))
        t1.start()
        t2.start()
        # Once both are likely subscribed, append a fresh event.
        time.sleep(0.3)
        _append_events(self.path, [_make_event(path="/live", idx=9)])
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        self.assertEqual(errors, [])
        for paths in (results[0], results[1]):
            self.assertEqual([f["path"] for f in paths], ["/r0", "/r1", "/live"])

    def test_unknown_path_404(self):
        srv = self._server()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(srv.url + "nope", timeout=2.0)
        self.assertEqual(ctx.exception.code, 404)

    def test_assert_loopback_accepts_127(self):
        assert_loopback("127.0.0.1")
        assert_loopback("localhost")

    def test_assert_loopback_rejects_non_loopback(self):
        with self.assertRaises(ValueError):
            assert_loopback("8.8.8.8")


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_missing_path_exits(self):
        bad = Path(self.tmp.name) / "missing.jsonl"
        with self.assertRaises(SystemExit) as ctx:
            cli.main([str(bad)])
        self.assertIn("does not exist", str(ctx.exception))

    def test_directory_path_exits(self):
        d = Path(self.tmp.name) / "subdir"
        d.mkdir()
        with self.assertRaises(SystemExit) as ctx:
            cli.main([str(d)])
        self.assertIn("directory", str(ctx.exception))

    def test_help_does_not_mention_host_or_poll_flags(self):
        parser = cli._build_parser()
        help_text = parser.format_help()
        self.assertNotIn("--host", help_text)
        self.assertNotIn("--poll-ms", help_text)


if __name__ == "__main__":
    unittest.main()
