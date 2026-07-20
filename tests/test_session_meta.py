"""Unit role-matrix tests for build_session_meta (Spec 36).

The meta sidecar must not label a snapshot-promoted, net-only .jsonl
"authoritative_complete" after a direct-parser failure. These tests call
build_session_meta directly with a synthetic LogPaths and assert the full
parser-status x authoritative-path matrix from the spec: the six affected
failure statuses, the allow-list boundary, the unknown-status direction,
and the unchanged rebuilt/no-promotion branches.
"""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_observe.observe import (  # noqa: E402
    LogPaths,
    NET_FALLBACK_WARNING,
    build_session_meta,
)

AFFECTED_FAILURE_STATUSES = (
    "parser_failure_partial",
    "parser_failure_empty_partial",
    "live_error_rebuild_parser_failure",
    "live_error_rebuild_failed",
    "live_timeout_rebuild_parser_failure",
    "live_timeout_rebuild_failed",
)

ALLOW_LIST_STATUSES = ("ok", "live_error_rebuilt", "backend_disabled")


def make_logs(observe_dir: Path, session_id: str = "session-x") -> LogPaths:
    return LogPaths(
        observe_dir=observe_dir,
        session_id=session_id,
        trace_path=observe_dir / f"{session_id}.trace",
        jsonl_path=observe_dir / f"{session_id}.jsonl",
        partial_path=observe_dir / f"{session_id}.jsonl.partial",
        rebuilt_path=observe_dir / f"{session_id}.jsonl.rebuilt",
        meta_path=observe_dir / f"{session_id}.meta.json",
    )


class SessionMetaRoleMatrixTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.logs = make_logs(Path(self._td.name))

    def roles(self, meta: dict) -> dict:
        artifacts = meta["artifacts"]
        return {name: artifacts[name]["role"] for name in ("jsonl", "partial", "rebuilt")}

    def test_warning_constant_contains_spec_pinned_substring(self):
        self.assertIn("snapshot fallback: net events only", NET_FALLBACK_WARNING)

    def test_degraded_promotion_yields_authoritative_net(self):
        for status in AFFECTED_FAILURE_STATUSES:
            with self.subTest(status=status):
                warnings = ["pre-existing warning"]
                meta = build_session_meta(self.logs, status, self.logs.jsonl_path, warnings)
                self.assertEqual(
                    self.roles(meta),
                    {"jsonl": "authoritative_net", "partial": "partial_direct", "rebuilt": "absent"},
                )
                self.assertEqual(
                    meta["artifacts"]["authoritative_event_path"], self.logs.jsonl_path.name
                )
                self.assertEqual(
                    meta["warnings"], ["pre-existing warning", NET_FALLBACK_WARNING]
                )

    def test_allow_list_statuses_keep_authoritative_complete(self):
        for status in ALLOW_LIST_STATUSES:
            with self.subTest(status=status):
                meta = build_session_meta(self.logs, status, self.logs.jsonl_path, [])
                self.assertEqual(
                    self.roles(meta),
                    {
                        "jsonl": "authoritative_complete",
                        "partial": "absent_or_parser_failure_partial",
                        "rebuilt": "absent",
                    },
                )
                self.assertEqual(
                    meta["artifacts"]["authoritative_event_path"], self.logs.jsonl_path.name
                )
                self.assertEqual(meta["warnings"], [])

    def test_unknown_status_degrades_to_authoritative_net(self):
        meta = build_session_meta(self.logs, "future_new_status", self.logs.jsonl_path, [])
        self.assertEqual(
            self.roles(meta),
            {"jsonl": "authoritative_net", "partial": "partial_direct", "rebuilt": "absent"},
        )
        self.assertEqual(meta["warnings"], [NET_FALLBACK_WARNING])

    def test_rebuilt_authoritative_branch_unchanged(self):
        meta = build_session_meta(self.logs, "live_timeout_rebuilt", self.logs.rebuilt_path, [])
        self.assertEqual(
            self.roles(meta),
            {
                "jsonl": "partial_live",
                "partial": "absent_or_parser_failure_partial",
                "rebuilt": "authoritative_complete",
            },
        )
        self.assertEqual(
            meta["artifacts"]["authoritative_event_path"], self.logs.rebuilt_path.name
        )
        self.assertEqual(meta["warnings"], [])

    def test_no_promotion_parser_failure_keeps_placeholder_role(self):
        meta = build_session_meta(self.logs, "parser_failure_partial", None, [])
        self.assertEqual(
            self.roles(meta),
            {
                "jsonl": "inferred_or_empty_placeholder",
                "partial": "partial_direct",
                "rebuilt": "absent",
            },
        )
        self.assertIsNone(meta["artifacts"]["authoritative_event_path"])
        self.assertEqual(meta["warnings"], [])

    def test_no_promotion_live_failure_keeps_partial_live_role(self):
        meta = build_session_meta(self.logs, "live_timeout_rebuild_failed", None, [])
        self.assertEqual(
            self.roles(meta),
            {"jsonl": "partial_live", "partial": "partial_direct", "rebuilt": "absent"},
        )
        self.assertIsNone(meta["artifacts"]["authoritative_event_path"])
        self.assertEqual(meta["warnings"], [])

    def test_caller_warnings_list_not_mutated_by_degraded_call(self):
        warnings = ["only warning"]
        meta = build_session_meta(
            self.logs, "parser_failure_partial", self.logs.jsonl_path, warnings
        )
        self.assertEqual(warnings, ["only warning"])
        self.assertIsNot(meta["warnings"], warnings)
        self.assertEqual(meta["warnings"], ["only warning", NET_FALLBACK_WARNING])


if __name__ == "__main__":
    unittest.main()
