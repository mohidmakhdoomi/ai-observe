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
sys.path.insert(0, str(ROOT / "tests"))  # tests/_util.py is here

from ai_observe.viewer import server as viewer_server
from ai_observe.viewer.server import ViewerServer, assert_loopback
from ai_observe.viewer import __main__ as cli
from _util import poll_until  # noqa: E402


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
    path_and_query = parsed.path + (("?" + parsed.query) if parsed.query else "")
    sock = _socket.create_connection((parsed.hostname, parsed.port), timeout=timeout)
    req = (
        f"GET {path_and_query} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
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


def _get_json(url, timeout=2.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
        # Wait until the tailer has caught up on the pre-written events.
        self.assertTrue(poll_until(lambda: srv.broadcaster_for(None).snapshot_len() >= 3))
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
            self.assertTrue(poll_until(lambda: srv.broadcaster_for(None).snapshot_len() >= 5))
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
        self.assertTrue(poll_until(lambda: srv.broadcaster_for(None).snapshot_len() >= 2))

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
        # Intentional fixed sleep: wait until both clients are likely
        # subscribed before appending. "Both SSE clients subscribed" is not
        # observable from server state, so there is no condition to poll;
        # replay-vs-live correctness below does not depend on this timing.
        time.sleep(0.3)
        _append_events(self.path, [_make_event(path="/live", idx=9)])
        viewer_server._join_thread_safely(t1, timeout=5.0)
        viewer_server._join_thread_safely(t2, timeout=5.0)
        for s in socks:
            try:
                s.close()
            except OSError:
                pass
        self.assertEqual(errors, [])
        for paths in (results[0], results[1]):
            self.assertEqual([f["path"] for f in paths], ["/r0", "/r1", "/live"])

    def test_session_endpoint_sanitizes_meta_and_artifact_state(self):
        rebuilt_path = self.path.with_name(self.path.name + ".rebuilt")
        partial_path = self.path.with_name(self.path.name + ".partial")
        meta_path = self.path.with_name("events.meta.json")
        _append_events(self.path, [_make_event(path="/canonical", idx=0)])
        _append_events(rebuilt_path, [_make_event(path="/rebuilt", idx=1)])
        _append_events(partial_path, [_make_event(path="/partial", idx=2)])
        meta_path.write_text(json.dumps({
            "schema_version": 1,
            "parser": {"status": "live_timeout_rebuilt", "source": "strace"},
            "warnings": ["internal warning not sent to browser verbatim"],
            "snapshot": {
                "enabled": True,
                "complete": False,
                "diagnostics": [{"code": "missing_root"}],
                "emitted_event_count": 3,
                "roots": ["/secret/root"],
            },
            "artifacts": {
                "authoritative_event_path": rebuilt_path.name,
                "jsonl": {"path": self.path.name, "role": "partial_live", "exists": True},
                "rebuilt": {"path": rebuilt_path.name, "role": "authoritative_complete", "exists": True},
                "partial": {"path": partial_path.name, "role": "partial_direct", "exists": True},
                "meta": {"path": meta_path.name, "role": "metadata", "exists": True},
            },
        }), encoding="utf-8")
        srv = self._server()
        payload = _get_json(srv.url + "session", timeout=3.0)
        self.assertEqual(payload["default_artifact"], "rebuilt")
        self.assertEqual(payload["authoritative_artifact"], "rebuilt")
        self.assertEqual(payload["parser_status"], "live_timeout_rebuilt")
        self.assertEqual(payload["warnings_count"], 1)
        self.assertEqual(payload["snapshot"], {"enabled": True, "complete": False, "diagnostic_count": 1, "emitted_event_count": 3})
        self.assertEqual(sorted(payload["artifacts"].keys()), ["jsonl", "meta", "partial", "rebuilt"])
        self.assertEqual(payload["artifacts"]["jsonl"]["role"], "partial_live")
        self.assertEqual(payload["artifacts"]["rebuilt"]["role"], "authoritative_complete")
        self.assertEqual(payload["artifacts"]["partial"]["role"], "partial_direct")
        serialized = json.dumps(payload)
        self.assertNotIn("/secret/root", serialized)
        self.assertNotIn("internal warning not sent to browser verbatim", serialized)

    def test_session_endpoint_passes_authoritative_net_role_through(self):
        # Pin the tolerance NFR4 relies on: the viewer passes role strings
        # through verbatim and selects artifacts via authoritative_event_path,
        # so the authoritative_net vocabulary needs no viewer change.
        rebuilt_path = self.path.with_name(self.path.name + ".rebuilt")
        partial_path = self.path.with_name(self.path.name + ".partial")
        meta_path = self.path.with_name("events.meta.json")
        _append_events(self.path, [_make_event(path="/net", idx=0)])
        _append_events(partial_path, [_make_event(path="/partial", idx=1)])
        meta_path.write_text(json.dumps({
            "schema_version": 1,
            "parser": {"status": "parser_failure_partial", "source": "strace"},
            "warnings": ["snapshot fallback: net events only; direct-layer detail was lost"],
            "artifacts": {
                "authoritative_event_path": self.path.name,
                "jsonl": {"path": self.path.name, "role": "authoritative_net", "exists": True},
                "rebuilt": {"path": rebuilt_path.name, "role": "absent", "exists": False},
                "partial": {"path": partial_path.name, "role": "partial_direct", "exists": True},
                "meta": {"path": meta_path.name, "role": "metadata", "exists": True},
            },
        }), encoding="utf-8")
        srv = self._server()
        payload = _get_json(srv.url + "session", timeout=3.0)
        self.assertEqual(payload["default_artifact"], "jsonl")
        self.assertEqual(payload["authoritative_artifact"], "jsonl")
        self.assertEqual(payload["parser_status"], "parser_failure_partial")
        self.assertEqual(payload["warnings_count"], 1)
        self.assertEqual(payload["artifacts"]["jsonl"]["role"], "authoritative_net")
        self.assertEqual(payload["artifacts"]["partial"]["role"], "partial_direct")

    def test_sse_can_switch_between_default_rebuilt_and_partial_artifacts(self):
        rebuilt_path = self.path.with_name(self.path.name + ".rebuilt")
        partial_path = self.path.with_name(self.path.name + ".partial")
        meta_path = self.path.with_name("events.meta.json")
        _append_events(self.path, [_make_event(path="/canonical", idx=0)])
        _append_events(rebuilt_path, [_make_event(path="/rebuilt", idx=1)])
        _append_events(partial_path, [_make_event(path="/partial", idx=2)])
        meta_path.write_text(json.dumps({
            "schema_version": 1,
            "parser": {"status": "live_timeout_rebuilt", "source": "strace"},
            "artifacts": {
                "authoritative_event_path": rebuilt_path.name,
                "jsonl": {"path": self.path.name, "role": "partial_live", "exists": True},
                "rebuilt": {"path": rebuilt_path.name, "role": "authoritative_complete", "exists": True},
                "partial": {"path": partial_path.name, "role": "partial_direct", "exists": True},
                "meta": {"path": meta_path.name, "role": "metadata", "exists": True},
            },
        }), encoding="utf-8")
        srv = self._server()
        # Wait until both artifact tailers have caught up on their event.
        self.assertTrue(poll_until(lambda: srv.broadcaster_for("rebuilt").snapshot_len() >= 1))
        self.assertTrue(poll_until(lambda: srv.broadcaster_for("partial").snapshot_len() >= 1))
        sock_default, fh_default = _open_sse(srv.url + "events", timeout=3.0)
        sock_partial, fh_partial = _open_sse(srv.url + "events?artifact=partial", timeout=3.0)
        try:
            default_backlog = _read_sse_frames(fh_default, 1, timeout=3.0)
            partial_backlog = _read_sse_frames(fh_partial, 1, timeout=3.0)
            self.assertEqual([f["path"] for f in default_backlog], ["/rebuilt"])
            self.assertEqual([f["path"] for f in partial_backlog], ["/partial"])
        finally:
            sock_default.close()
            sock_partial.close()

    def test_empty_jsonl_initially_emits_no_appends(self):
        # Path exists, file is empty. /events connects: no append frames
        # arrive before something is written, and the first append frame is
        # the first event we write.
        self.path.write_bytes(b"")
        srv = self._server()
        # Intentional fixed sleep (negative check): give the tailer time to
        # complete its initial scan of the empty file before connecting.
        # "Tailer is idle at EOF" is not observable from public server
        # state, and the point is to prove no stale frame arrives.
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
        # Intentional fixed sleep: brief settle so the tailer finishes its
        # initial scan of the empty file before we connect (not observable
        # from public server state); the SSE handshake below is what the
        # shutdown assertion actually depends on.
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
            viewer_server._join_thread_safely(stopper, timeout=5.0)
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
        viewer_server._join_thread_safely(t, timeout=10.0)
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
        viewer_server._join_thread_safely(t, timeout=10.0)
        self.assertEqual(opened_with, [])

    def test_join_thread_safely_tolerates_tstate_lock_race(self):
        # Regression for a CPython thread-teardown race (gh-89322 / bpo-45274):
        # Thread.join() can raise AssertionError from _wait_for_tstate_lock
        # ("assert self._is_stopped") while the joined thread clears its
        # C-level tstate lock. It surfaced in CI (run 29309258243, py3.12 leg)
        # as ViewerServer.stop() -> _serve_thread.join() aborting the one-shot
        # viewer shutdown. _join_thread_safely must swallow that assertion and
        # return once the thread is no longer alive, not propagate it.
        class RacingThread:
            def __init__(self):
                self.join_calls = 0

            def join(self, timeout=None):
                self.join_calls += 1
                raise AssertionError("simulated _wait_for_tstate_lock race")

            def is_alive(self):
                return False

        rt = RacingThread()
        # Must not raise even though join() always asserts.
        viewer_server._join_thread_safely(rt, timeout=1.0)
        self.assertGreaterEqual(rt.join_calls, 1)

    def test_join_thread_safely_joins_live_thread_cleanly(self):
        # Normal path: a real, briefly-running thread joins without error and
        # is observed stopped afterward.
        started = threading.Event()
        th = threading.Thread(target=started.set)
        th.start()
        self.assertTrue(started.wait(timeout=2.0))
        viewer_server._join_thread_safely(th, timeout=2.0)
        self.assertFalse(th.is_alive())

    def test_harness_thread_joins_route_through_race_tolerant_helper(self):
        # Regression for issue #43: this module's own harness threads must be
        # joined through viewer_server._join_thread_safely -- the same race-
        # tolerant path ViewerServer.stop() uses -- never via a raw
        # Thread.join() call. A raw join can hit the CPython thread-teardown
        # race (gh-89322 / bpo-45274) and abort an otherwise-passing test,
        # which is precisely the flake this fix removes. The guard is static
        # and deterministic: it inspects this module's own source so a
        # reintroduced raw join fails HERE, loudly, instead of intermittently
        # in CI.
        import re

        source = Path(__file__).resolve().read_text(encoding="utf-8")
        # Thread joins in this module always pass a timeout (a keyword, by
        # convention, or a positional number); str.join never takes one, so
        # this pattern isolates thread joins from "".join(...) and from any
        # mention of .join in a comment.
        raw_thread_join = re.compile(r"\.join\(\s*(?:timeout\b|\d)")
        offenders = [
            source[: m.start()].count("\n") + 1
            for m in raw_thread_join.finditer(source)
        ]
        self.assertEqual(
            offenders,
            [],
            f"raw thread joins at lines {offenders} in {Path(__file__).name}; "
            "route them through viewer_server._join_thread_safely to stay "
            "race-tolerant (issue #43)",
        )


if __name__ == "__main__":
    unittest.main()
