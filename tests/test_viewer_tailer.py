from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_observe.viewer.tailer import JsonlTailer, sanitize_event, SCHEMA_VERSION


def _event(op="modify", path="/x", result=10, ts="2026-05-13T10:00:00.000Z", **extra):
    base = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": ts,
        "session_id": "s",
        "invocation_id": "s",
        "pid": 1,
        "process": {"pid": 1, "ppid": 0, "comm": None},
        "operation": op,
        "path": path,
        "old_path": None,
        "new_path": None,
        "command": ["codex"],
        "raw_syscall": "secret",
        "result": result,
    }
    base.update(extra)
    return base


def _write_line(path: Path, ev: dict) -> None:
    with open(path, "ab") as fh:
        fh.write(json.dumps(ev).encode("utf-8"))
        fh.write(b"\n")


class _Collector:
    def __init__(self):
        self.events = []
        self.warns = []
        self._lock = threading.Lock()

    def on_event(self, ev):
        with self._lock:
            self.events.append(ev)

    def warn(self, msg):
        with self._lock:
            self.warns.append(msg)

    def snapshot(self):
        with self._lock:
            return list(self.events), list(self.warns)


def _wait_for(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class JsonlTailerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "events.jsonl"
        self.path.touch()
        self.col = _Collector()
        self.tailer = JsonlTailer(
            self.path, on_event=self.col.on_event, poll_interval=0.05, warn=self.col.warn
        )

    def _start(self):
        self.tailer.start()
        self.addCleanup(self.tailer.stop)

    def test_sanitize_event_keys_exactly_match_whitelist(self):
        raw = _event()
        out = sanitize_event(raw)
        self.assertEqual(
            sorted(out.keys()),
            sorted(["timestamp", "operation", "path", "old_path", "new_path", "result"]),
        )
        # The sensitive fields must not appear.
        for forbidden in ("raw_syscall", "command", "pid", "process", "session_id", "invocation_id", "schema_version"):
            self.assertNotIn(forbidden, out)

    def test_empty_file_then_append(self):
        self._start()
        # Initially no events.
        time.sleep(0.1)
        self.assertEqual(self.col.snapshot()[0], [])
        _write_line(self.path, _event(path="/x", result=10))
        _write_line(self.path, _event(path="/y", result=20))
        self.assertTrue(_wait_for(lambda: len(self.col.snapshot()[0]) == 2))
        evs, _ = self.col.snapshot()
        self.assertEqual(evs[0]["path"], "/x")
        self.assertEqual(evs[1]["result"], 20)

    def test_large_startup_chunk_processed_in_order_without_trailing_buffer(self):
        count = 5000
        with open(self.path, "ab") as fh:
            for i in range(count):
                fh.write(json.dumps(_event(path=f"/bulk/{i}", result=i)).encode("utf-8"))
                fh.write(b"\n")

        self.tailer._poll_once()

        evs, warns = self.col.snapshot()
        self.assertEqual(len(evs), count)
        self.assertEqual(evs[0]["path"], "/bulk/0")
        self.assertEqual(evs[-1]["path"], f"/bulk/{count - 1}")
        self.assertEqual(evs[-1]["result"], count - 1)
        self.assertEqual(self.tailer._buf, b"")
        self.assertEqual(warns, [])

    def test_large_chunk_keeps_only_final_partial_line_until_completion(self):
        count = 1000
        partial = json.dumps(_event(path="/partial", result=9999)).encode("utf-8")
        with open(self.path, "ab") as fh:
            for i in range(count):
                fh.write(json.dumps(_event(path=f"/bulk/{i}", result=i)).encode("utf-8"))
                fh.write(b"\n")
            fh.write(partial[:-2])

        self.tailer._poll_once()

        evs, _ = self.col.snapshot()
        self.assertEqual(len(evs), count)
        self.assertEqual(evs[-1]["path"], f"/bulk/{count - 1}")
        self.assertEqual(self.tailer._buf, partial[:-2])

        with open(self.path, "ab") as fh:
            fh.write(partial[-2:])
            fh.write(b"\n")
        self.tailer._poll_once()

        evs, _ = self.col.snapshot()
        self.assertEqual(len(evs), count + 1)
        self.assertEqual(evs[-1]["path"], "/partial")
        self.assertEqual(evs[-1]["result"], 9999)
        self.assertEqual(self.tailer._buf, b"")

    def test_partial_line_buffered_then_completed(self):
        self._start()
        with open(self.path, "ab") as fh:
            fh.write(b'{"schema_version":1,"operation":"modify","path":"/p","result":5,"timestamp":"t","old_path":null,"new_path":null')
        time.sleep(0.15)
        # No event yet -- the line is incomplete.
        self.assertEqual(self.col.snapshot()[0], [])
        with open(self.path, "ab") as fh:
            fh.write(b"}\n")
        self.assertTrue(_wait_for(lambda: len(self.col.snapshot()[0]) == 1))
        evs, _ = self.col.snapshot()
        self.assertEqual(evs[0]["path"], "/p")

    def test_malformed_line_skipped_with_warning(self):
        self._start()
        with open(self.path, "ab") as fh:
            fh.write(b"this is not json\n")
        _write_line(self.path, _event(path="/ok"))
        self.assertTrue(_wait_for(lambda: len(self.col.snapshot()[0]) == 1))
        evs, warns = self.col.snapshot()
        self.assertEqual(evs[0]["path"], "/ok")
        self.assertTrue(any("malformed" in w for w in warns), warns)

    def test_schema_version_mismatch_skipped(self):
        self._start()
        bad = _event(path="/future")
        bad["schema_version"] = 2
        _write_line(self.path, bad)
        _write_line(self.path, _event(path="/ok"))
        self.assertTrue(_wait_for(lambda: len(self.col.snapshot()[0]) == 1))
        _, warns = self.col.snapshot()
        self.assertTrue(any("schema_version" in w for w in warns), warns)

    def test_truncation_reopens_from_zero(self):
        self._start()
        _write_line(self.path, _event(path="/a"))
        self.assertTrue(_wait_for(lambda: len(self.col.snapshot()[0]) == 1))
        # Truncate and let the tailer observe the truncated state before
        # the next write — otherwise size-shrink-then-grow can mask the
        # truncation in a single poll cycle.
        with open(self.path, "wb") as fh:
            fh.write(b"")
        time.sleep(0.15)
        _write_line(self.path, _event(path="/b"))
        self.assertTrue(_wait_for(lambda: len(self.col.snapshot()[0]) == 2))
        evs, warns = self.col.snapshot()
        self.assertEqual([e["path"] for e in evs], ["/a", "/b"])
        self.assertTrue(any("truncated" in w for w in warns), warns)

    def test_inode_change_reopens(self):
        self._start()
        _write_line(self.path, _event(path="/a"))
        self.assertTrue(_wait_for(lambda: len(self.col.snapshot()[0]) == 1))
        # Replace the file with a fresh inode.
        new_path = Path(self.tmp.name) / "events.jsonl.new"
        _write_line(new_path, _event(path="/b"))
        os.replace(new_path, self.path)
        self.assertTrue(_wait_for(lambda: len(self.col.snapshot()[0]) == 2, timeout=5.0))
        _, warns = self.col.snapshot()
        self.assertTrue(any("inode" in w for w in warns), warns)

    def test_shutdown_warns_once_on_incomplete_trailing_fragment(self):
        self._start()
        with open(self.path, "ab") as fh:
            fh.write(b'{"schema_version":1,"operation":"modify","path":"/p"')
        time.sleep(0.15)
        self.tailer.stop()
        _, warns = self.col.snapshot()
        fragment_warns = [w for w in warns if "incomplete trailing" in w]
        self.assertEqual(len(fragment_warns), 1, warns)


if __name__ == "__main__":
    unittest.main()
