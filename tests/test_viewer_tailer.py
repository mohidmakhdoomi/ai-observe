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
sys.path.insert(0, str(ROOT / "tests"))  # tests/_util.py is here

from ai_observe.viewer.tailer import JsonlTailer, sanitize_event, SCHEMA_VERSION
from _util import poll_until  # noqa: E402


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
        "source": "strace",
        "confidence": "direct",
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
        # The sensitive fields must not appear.
        for forbidden in ("raw_syscall", "command", "pid", "process", "session_id", "invocation_id"):
            self.assertNotIn(forbidden, out)
        self.assertEqual(out["schema_version"], SCHEMA_VERSION)
        self.assertEqual(out["source"], "strace")
        self.assertEqual(out["confidence"], "direct")

    def test_empty_file_then_append(self):
        self._start()
        # Intentional fixed sleep (negative check): give the tailer time to
        # poll the empty file and assert it emitted nothing; "no event" has
        # no queryable completion condition to poll for.
        time.sleep(0.1)
        self.assertEqual(self.col.snapshot()[0], [])
        _write_line(self.path, _event(path="/x", result=10))
        _write_line(self.path, _event(path="/y", result=20))
        self.assertTrue(poll_until(lambda: len(self.col.snapshot()[0]) == 2))
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
        # Intentional fixed sleep (negative check): wait past the poll
        # interval, then assert no event -- the line is incomplete.
        time.sleep(0.15)
        self.assertEqual(self.col.snapshot()[0], [])
        with open(self.path, "ab") as fh:
            fh.write(b"}\n")
        self.assertTrue(poll_until(lambda: len(self.col.snapshot()[0]) == 1))
        evs, _ = self.col.snapshot()
        self.assertEqual(evs[0]["path"], "/p")

    def test_malformed_line_skipped_with_warning(self):
        self._start()
        with open(self.path, "ab") as fh:
            fh.write(b"this is not json\n")
        _write_line(self.path, _event(path="/ok"))
        self.assertTrue(poll_until(lambda: len(self.col.snapshot()[0]) == 1))
        evs, warns = self.col.snapshot()
        self.assertEqual(evs[0]["path"], "/ok")
        self.assertTrue(any("malformed" in w for w in warns), warns)

    def test_v1_missing_and_v2_events_are_normalized_with_provenance(self):
        self._start()
        missing = _event(path="/missing")
        missing.pop("schema_version")
        missing.pop("source")
        missing.pop("confidence")
        v1 = _event(path="/v1", schema_version=1)
        v1.pop("source")
        v1.pop("confidence")
        v2 = _event(path="/v2", schema_version=2, source="snapshot", confidence="inferred")
        _write_line(self.path, missing)
        _write_line(self.path, v1)
        _write_line(self.path, v2)
        self.assertTrue(poll_until(lambda: len(self.col.snapshot()[0]) == 3))
        evs, warns = self.col.snapshot()
        self.assertEqual([e["path"] for e in evs], ["/missing", "/v1", "/v2"])
        self.assertEqual([e["schema_version"] for e in evs], [1, 1, 2])
        self.assertEqual([e["source"] for e in evs], ["strace", "strace", "snapshot"])
        self.assertEqual([e["confidence"] for e in evs], ["direct", "direct", "inferred"])
        self.assertEqual(warns, [])

    def test_future_schema_with_known_safe_fields_is_normalized(self):
        self._start()
        future = _event(
            path="/future",
            schema_version=3,
            source="snapshot",
            confidence="inferred",
            raw_new_field={"must": "not be exposed"},
        )
        _write_line(self.path, future)
        self.assertTrue(poll_until(lambda: len(self.col.snapshot()[0]) == 1))
        evs, warns = self.col.snapshot()
        self.assertEqual(evs[0]["schema_version"], 3)
        self.assertEqual(evs[0]["path"], "/future")
        self.assertEqual(evs[0]["source"], "snapshot")
        self.assertEqual(evs[0]["confidence"], "inferred")
        self.assertNotIn("raw_new_field", evs[0])
        self.assertEqual(warns, [])

    def test_unsupported_future_schema_version_skipped(self):
        self._start()
        bad = _event(path="/future")
        bad["schema_version"] = 999
        bad.pop("operation")
        _write_line(self.path, bad)
        _write_line(self.path, _event(path="/ok"))
        self.assertTrue(poll_until(lambda: len(self.col.snapshot()[0]) == 1))
        _, warns = self.col.snapshot()
        self.assertTrue(any("schema_version" in w for w in warns), warns)

    def test_truncation_reopens_from_zero(self):
        self._start()
        _write_line(self.path, _event(path="/a"))
        self.assertTrue(poll_until(lambda: len(self.col.snapshot()[0]) == 1))
        # Truncate and let the tailer observe the truncated state before
        # the next write — otherwise size-shrink-then-grow can mask the
        # truncation in a single poll cycle. The truncation warning is the
        # queryable signal that the tailer saw it.
        with open(self.path, "wb") as fh:
            fh.write(b"")
        self.assertTrue(poll_until(lambda: any("truncated" in w for w in self.col.snapshot()[1])))
        _write_line(self.path, _event(path="/b"))
        self.assertTrue(poll_until(lambda: len(self.col.snapshot()[0]) == 2))
        evs, warns = self.col.snapshot()
        self.assertEqual([e["path"] for e in evs], ["/a", "/b"])
        self.assertTrue(any("truncated" in w for w in warns), warns)

    def test_inode_change_reopens(self):
        self._start()
        _write_line(self.path, _event(path="/a"))
        self.assertTrue(poll_until(lambda: len(self.col.snapshot()[0]) == 1))
        # Replace the file with a fresh inode.
        new_path = Path(self.tmp.name) / "events.jsonl.new"
        _write_line(new_path, _event(path="/b"))
        os.replace(new_path, self.path)
        self.assertTrue(poll_until(lambda: len(self.col.snapshot()[0]) == 2, timeout=5.0))
        _, warns = self.col.snapshot()
        self.assertTrue(any("inode" in w for w in warns), warns)

    def test_shutdown_warns_once_on_incomplete_trailing_fragment(self):
        self._start()
        with open(self.path, "ab") as fh:
            fh.write(b'{"schema_version":1,"operation":"modify","path":"/p"')
        # Wait until the tailer has buffered the incomplete fragment (other
        # tests already inspect `_buf` directly), then stop.
        self.assertTrue(poll_until(lambda: self.tailer._buf))
        self.tailer.stop()
        _, warns = self.col.snapshot()
        fragment_warns = [w for w in warns if "incomplete trailing" in w]
        self.assertEqual(len(fragment_warns), 1, warns)


if __name__ == "__main__":
    unittest.main()
