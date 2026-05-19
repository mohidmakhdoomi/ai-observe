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

from ai_observe.viewer import server as viewer_server
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


def _decode_sse_frame(cur_lines):
    evt_type = None
    data = None
    for raw_line in cur_lines:
        s = raw_line.decode("utf-8", errors="replace")
        if s.startswith("event: "):
            evt_type = s[len("event: "):]
        elif s.startswith("data: "):
            data = s[len("data: "):]
    if evt_type is None or data is None:
        return None
    return evt_type, json.loads(data)


def _events_from_sse_frame(frame):
    evt_type, payload = frame
    if evt_type == "append":
        return [payload]
    if evt_type == "append_batch":
        return list(payload)
    return []


def _read_sse_event_frames(fh, n, timeout=5.0):
    """Read SSE frames until at least n append events have arrived."""
    deadline = time.monotonic() + timeout
    raw_frames = []
    frames = []
    cur_lines = []
    while len(frames) < n and time.monotonic() < deadline:
        line = fh.readline()
        if not line:
            break
        line = line.rstrip(b"\r\n")
        if line == b"":
            frame = _decode_sse_frame(cur_lines)
            if frame is not None:
                raw_frames.append(frame)
                frames.extend(_events_from_sse_frame(frame))
            cur_lines = []
        else:
            cur_lines.append(line)
    return raw_frames, frames


def _read_sse_frames(fh, n, timeout=5.0):
    """Read n appended events from a buffered SSE file handle."""
    _, frames = _read_sse_event_frames(fh, n, timeout=timeout)
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
                    sorted([
                        "schema_version",
                        "timestamp",
                        "operation",
                        "path",
                        "old_path",
                        "new_path",
                        "result",
                        "source",
                        "confidence",
                    ]),
                )
                self.assertEqual(f["source"], "strace")
                self.assertEqual(f["confidence"], "direct")
            # Now append new events and read them live.
            _append_events(self.path, [_make_event(path="/live", idx=9)])
            live = _read_sse_frames(fh, 1, timeout=3.0)
            self.assertEqual(live[0]["path"], "/live")
        finally:
            sock.close()

    def test_sse_append_batch_replay_and_live_exactly_once_across_boundaries(self):
        _append_events(self.path, [_make_event(path=f"/r{i}", idx=i) for i in range(5)])
        with mock.patch.object(viewer_server, "_APPEND_BATCH_SIZE", 2):
            srv = self._server()
            time.sleep(0.2)
            sock, fh = _open_sse(srv.url + "events", timeout=3.0)
            try:
                raw_backlog, backlog = _read_sse_event_frames(fh, 5, timeout=3.0)
                self.assertEqual([f["path"] for f in backlog], [f"/r{i}" for i in range(5)])
                self.assertEqual([kind for kind, _ in raw_backlog], ["append_batch", "append_batch", "append_batch"])
                self.assertEqual([len(payload) for _, payload in raw_backlog], [2, 2, 1])
                for f in backlog:
                    self.assertEqual(
                        sorted(f.keys()),
                        sorted([
                            "schema_version",
                            "timestamp",
                            "operation",
                            "path",
                            "old_path",
                            "new_path",
                            "result",
                            "source",
                            "confidence",
                        ]),
                    )

                _append_events(self.path, [_make_event(path="/live", idx=9)])
                raw_live, live = _read_sse_event_frames(fh, 1, timeout=3.0)
                self.assertEqual([f["path"] for f in live], ["/live"])
                self.assertEqual([kind for kind, _ in raw_live], ["append_batch"])
                self.assertEqual([len(payload) for _, payload in raw_live], [1])
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
        for s in socks:
            try:
                s.close()
            except OSError:
                pass
        self.assertEqual(errors, [])
        for paths in (results[0], results[1]):
            self.assertEqual([f["path"] for f in paths], ["/r0", "/r1", "/live"])

    def test_empty_jsonl_initially_emits_no_appends(self):
        # Path exists, file is empty. /events connects: no append frames
        # arrive before something is written, and the first append frame is
        # the first event we write.
        self.path.write_bytes(b"")
        srv = self._server()
        time.sleep(0.15)
        sock, fh = _open_sse(srv.url + "events", timeout=3.0)
        try:
            # Write a single event after a beat. The first frame the SSE
            # reader sees must be that event — never a stale backlog frame.
            _append_events(self.path, [_make_event(path="/first", idx=0)])
            frames = _read_sse_frames(fh, 1, timeout=3.0)
            self.assertEqual([f["path"] for f in frames], ["/first"])
        finally:
            sock.close()

    def test_shutdown_frame_sent_to_connected_client(self):
        srv = self._server()
        time.sleep(0.1)
        sock, fh = _open_sse(srv.url + "events", timeout=3.0)
        try:
            # Stop the server; the connected client should receive a final
            # `event: shutdown` frame before the socket closes.
            stopper = threading.Thread(target=srv.stop)
            # Avoid the addCleanup double-stop; just run our own.
            stopper.start()

            saw_shutdown = False
            sock.settimeout(3.0)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                line = fh.readline()
                if not line:
                    break
                if line.strip() == b"event: shutdown":
                    saw_shutdown = True
                    break
            stopper.join(timeout=5.0)
            self.assertTrue(saw_shutdown)
        finally:
            sock.close()
        # Override addCleanup's server.stop() since we already stopped it.
        # Re-stopping a stopped server is a no-op for safety but explicitly
        # noting here.

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

    def _run_cli_one_shot(self, args):
        captured_stderr = []
        real_stderr_write = sys.stderr.write

        def capture(s):
            captured_stderr.append(s)
            return real_stderr_write(s)

        original_event = threading.Event

        class OneShotEvent(original_event):
            def wait(self, timeout=None):
                self.set()
                return True

        with mock.patch.object(sys.stderr, "write", side_effect=capture):
            with mock.patch.object(cli.threading, "Event", OneShotEvent):
                with mock.patch.object(cli.signal, "signal", lambda *a, **k: None):
                    rc = cli.main(args)
        return rc, "".join(captured_stderr)

    def test_default_port_constant_is_stable_7878(self):
        self.assertEqual(cli.DEFAULT_PORT, 7878)
        parser = cli._build_parser()
        args = parser.parse_args([str(Path(self.tmp.name) / "x.jsonl")])
        self.assertIsNone(args.port)

    def test_default_port_collision_falls_back_to_ephemeral(self):
        jsonl = Path(self.tmp.name) / "fallback.jsonl"
        jsonl.write_bytes(b"")
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        occupied = sock.getsockname()[1]

        with mock.patch.object(cli, "DEFAULT_PORT", occupied):
            rc, stderr = self._run_cli_one_shot([str(jsonl), "--no-browser"])
        self.assertEqual(rc, 0)
        self.assertIn("http://127.0.0.1:", stderr)
        self.assertNotIn(f":{occupied}/", stderr)

    def test_explicit_port_collision_does_not_fall_back(self):
        jsonl = Path(self.tmp.name) / "explicit.jsonl"
        jsonl.write_bytes(b"")
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        occupied = sock.getsockname()[1]

        with self.assertRaises(OSError):
            cli.main([str(jsonl), "--port", str(occupied), "--no-browser"])

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

    def test_cli_calls_webbrowser_open_and_swallows_failure(self):
        # Use an empty .jsonl so the server has nothing to do, then stop it
        # immediately via SIGINT-like signal.
        jsonl = Path(self.tmp.name) / "empty.jsonl"
        jsonl.write_bytes(b"")

        # Patch webbrowser.open to raise; the CLI must not propagate or print
        # an error trace (spec: silent failure).
        opened_with = []

        def fake_open(url):
            opened_with.append(url)
            raise RuntimeError("simulated browser failure")

        captured_stderr = []
        real_stderr_write = sys.stderr.write

        def capture(s):
            captured_stderr.append(s)
            return real_stderr_write(s)

        # Run main in a thread so we can stop it via the stop event.
        result = {}

        def run():
            try:
                with mock.patch.object(cli.webbrowser, "open", side_effect=fake_open):
                    with mock.patch.object(sys.stderr, "write", side_effect=capture):
                        # Patch signal.signal so the test process doesn't
                        # actually install signal handlers (would conflict
                        # with unittest in some envs); replace the wait loop
                        # by patching Event.wait to set after one call.
                        original_event = threading.Event

                        class OneShotEvent(original_event):
                            def __init__(self):
                                super().__init__()

                            def wait(self, timeout=None):
                                self.set()
                                return True

                        with mock.patch.object(cli.threading, "Event", OneShotEvent):
                            with mock.patch.object(cli.signal, "signal", lambda *a, **k: None):
                                result["rc"] = cli.main([str(jsonl)])
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=10.0)
        self.assertNotIn("error", result, result)
        self.assertEqual(result.get("rc"), 0)
        self.assertEqual(len(opened_with), 1)
        joined_stderr = "".join(captured_stderr)
        self.assertNotIn("webbrowser.open failed", joined_stderr)
        self.assertNotIn("Traceback", joined_stderr)

    def test_cli_no_browser_skips_webbrowser_open(self):
        jsonl = Path(self.tmp.name) / "empty2.jsonl"
        jsonl.write_bytes(b"")

        opened_with = []

        def fake_open(url):
            opened_with.append(url)

        def run():
            with mock.patch.object(cli.webbrowser, "open", side_effect=fake_open):
                original_event = threading.Event

                class OneShotEvent(original_event):
                    def wait(self, timeout=None):
                        self.set()
                        return True

                with mock.patch.object(cli.threading, "Event", OneShotEvent):
                    with mock.patch.object(cli.signal, "signal", lambda *a, **k: None):
                        cli.main([str(jsonl), "--no-browser"])

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=10.0)
        self.assertEqual(opened_with, [])


if __name__ == "__main__":
    unittest.main()
