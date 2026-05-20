from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_observe.backends import (  # noqa: E402
    BackendCapabilities,
    backends_in_finalize_order,
    backends_in_prepare_order,
    parse_backend_selection,
)
from ai_observe.backends.snapshot import SnapshotBackend  # noqa: E402
from ai_observe.backends.strace import StraceBackend  # noqa: E402


class BackendSelectionTests(unittest.TestCase):
    def test_default_backend_selection(self):
        self.assertEqual(parse_backend_selection(None), ("strace", "snapshot"))
        self.assertEqual(parse_backend_selection(""), ("strace", "snapshot"))

    def test_selection_deduplicates_and_validates(self):
        self.assertEqual(parse_backend_selection("snapshot,strace,snapshot"), ("snapshot", "strace"))
        with self.assertRaisesRegex(ValueError, "unsupported backend name"):
            parse_backend_selection("fanotify")

    def test_prepare_and_finalize_orders_are_stable(self):
        selected = ("strace", "snapshot")
        self.assertEqual(backends_in_prepare_order(selected), ("snapshot", "strace"))
        self.assertEqual(backends_in_finalize_order(selected), ("strace", "snapshot"))
        self.assertEqual(backends_in_prepare_order(("snapshot",)), ("snapshot",))
        self.assertEqual(backends_in_finalize_order(("snapshot",)), ("snapshot",))


class BackendSurfaceTests(unittest.TestCase):
    def test_concrete_backends_expose_backend_protocol_surface(self):
        strace = StraceBackend(
            error_factory=lambda message, code: RuntimeError(f"{code}:{message}"),
            trace_parser_cls=object,
            live_tracer_cls=object,
            parse_trace_file=lambda *args, **kwargs: None,
            safe_write_jsonl=lambda *args, **kwargs: None,
            env_flag=lambda *args, **kwargs: False,
            env_value=lambda *args, **kwargs: None,
            live_enabled=lambda *args, **kwargs: False,
            live_poll_seconds=lambda *args, **kwargs: 0.2,
            live_join_timeout=lambda *args, **kwargs: 30.0,
        )
        snapshot = SnapshotBackend(
            error_factory=lambda message, code: RuntimeError(f"{code}:{message}"),
            prepare_plan=lambda *args, **kwargs: None,
            finalize_plan=lambda *args, **kwargs: None,
            merge_snapshot_events=lambda *args, **kwargs: (None, 0),
            build_snapshot_summary=lambda *args, **kwargs: {},
            build_session_meta=lambda *args, **kwargs: {},
            safe_write_meta=lambda *args, **kwargs: None,
        )

        for backend in (strace, snapshot):
            with self.subTest(backend=backend.name):
                self.assertIsInstance(backend.name, str)
                self.assertIsInstance(backend.capabilities, BackendCapabilities)
                self.assertTrue(callable(backend.prepare))
                self.assertTrue(callable(backend.stop))
                self.assertTrue(callable(backend.finalize))


if __name__ == "__main__":
    unittest.main()
