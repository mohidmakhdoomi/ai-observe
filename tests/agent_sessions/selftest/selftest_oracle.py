"""Tool-free self-tests for the oracle + known-bug registry (Spec 38, Phase 2).

Covers all four `known_bug_gate` branches (active/reproduces, active/stale,
inactive/correct, inactive/regressed), the bug-specific gates, and the
`ensure_tool_usable` detection rule — all with synthetic inputs, no agent tool.
"""

from __future__ import annotations

import types
import unittest

from ..oracle import (
    FAIL,
    PASS,
    KnownBug,
    ToolUnusable,
    bug32_signature,
    bug33_unpaired_marker_delete,
    ensure_tool_usable,
    expect_authority_not_overstated,
    expect_deletion_captured,
    expect_no_marker_noise,
    known_bug_gate,
    known_bug_status,
)

ACTIVE = {7: KnownBug(7, "demo bug", active=True)}
FIXED = {7: KnownBug(7, "demo bug", active=False)}


class KnownBugGateTests(unittest.TestCase):
    def test_active_and_reproduces_is_known_bug(self):
        r = known_bug_gate("s", "claude", "canonical", 7,
                           buggy_present=True, correct_present=False, registry=ACTIVE)
        self.assertEqual(r.status, known_bug_status(7))

    def test_active_but_no_longer_reproduces_fails_loud(self):
        r = known_bug_gate("s", "claude", "canonical", 7,
                           buggy_present=False, correct_present=True, registry=ACTIVE)
        self.assertEqual(r.status, FAIL)
        self.assertIn("flip OPEN_BUGS[7].active=False", r.detail)

    def test_inactive_and_correct_passes(self):
        r = known_bug_gate("s", "claude", "canonical", 7,
                           buggy_present=False, correct_present=True, registry=FIXED)
        self.assertEqual(r.status, PASS)

    def test_inactive_but_regressed_fails(self):
        r = known_bug_gate("s", "claude", "canonical", 7,
                           buggy_present=True, correct_present=False, registry=FIXED)
        self.assertEqual(r.status, FAIL)
        self.assertIn("regressed", r.detail)


class BugSpecificGateTests(unittest.TestCase):
    """These drive ai-observe's REAL trace_parser (deterministic, tool-free), so
    they double as live #32/#33 reproduction checks — rot-proof: when either bug is
    fixed in ai-observe but its flag is not flipped, the gate fails loudly."""

    def test_bug32_signature_reproduces_at_parser_level(self):
        dropped, plain_ok = bug32_signature()
        self.assertTrue(plain_ok, "plain unlinkat should always be captured")
        self.assertTrue(dropped, "#32 should still drop the annotated-dirfd deletion")

    def test_deletion_gate_is_known_bug_32(self):
        r = expect_deletion_captured("ephemeral", "claude")
        self.assertEqual(r.status, known_bug_status(32))

    def test_bug33_signature_reproduces_at_parser_level(self):
        self.assertTrue(bug33_unpaired_marker_delete(),
                        "#33 should still leave an unpaired /newroot marker delete")

    def test_marker_noise_gate_is_known_bug_33(self):
        r = expect_no_marker_noise("single_write", "codex")
        self.assertEqual(r.status, known_bug_status(33))

    def test_authority_overstated_is_known_bug_36(self):
        meta = {"parser": {"status": "parser_failure_partial"},
                "artifacts": {"jsonl": {"role": "authoritative_complete"}}}
        r = expect_authority_not_overstated("degraded", "claude", meta)
        self.assertEqual(r.status, known_bug_status(36))

    def test_authority_ok_when_parser_healthy(self):
        # Clean parser_status → not the #36 signature → stale-annotation FAIL
        # (while #36 is active, the gate expects the bug to reproduce).
        meta = {"parser": {"status": "ok"},
                "artifacts": {"jsonl": {"role": "authoritative_complete"}}}
        r = expect_authority_not_overstated("degraded", "claude", meta)
        self.assertEqual(r.status, FAIL)


class EnsureToolUsableTests(unittest.TestCase):
    def _result(self, returncode, total):
        return types.SimpleNamespace(returncode=returncode, disk_events={"total": total})

    def test_nonzero_returncode_raises(self):
        with self.assertRaises(ToolUnusable) as cm:
            ensure_tool_usable("agy", self._result(1, 5))
        self.assertEqual(cm.exception.tool, "agy")

    def test_zero_events_raises(self):
        with self.assertRaises(ToolUnusable) as cm:
            ensure_tool_usable("codex", self._result(0, 0))
        self.assertEqual(cm.exception.tool, "codex")

    def test_usable_result_does_not_raise(self):
        ensure_tool_usable("claude", self._result(0, 4))  # no exception


if __name__ == "__main__":
    unittest.main()
