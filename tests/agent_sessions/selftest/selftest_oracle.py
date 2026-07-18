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
    OPEN_BUGS,
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
    """Deterministic #32/#33 flip-homes driving ai-observe's REAL trace_parser,
    tool-free — so flip detection works with NO agent.

    Every assertion is derived from `OPEN_BUGS[N].active`, so flipping a bug is a
    TRUE single edit (`OPEN_BUGS[N].active = False`) that keeps these tests green
    once the ai-observe fix lands — while remaining rot-proof in BOTH directions:
      * fix landed but flag NOT flipped (active, no longer reproduces) → FAIL loud
      * flag flipped but fix NOT landed (inactive, still reproduces)  → FAIL loud
    """

    def test_bug32_reproduction_matches_registry(self):
        dropped, plain_ok = bug32_signature()
        self.assertTrue(plain_ok, "plain unlinkat should always be captured")
        if OPEN_BUGS[32].active:
            self.assertTrue(dropped, "#32 active but not reproducing — fix landed? flip the flag")
        else:
            self.assertFalse(dropped, "#32 flipped inactive but the annotated deletion still drops")

    def test_deletion_gate_tracks_registry(self):
        r = expect_deletion_captured("ephemeral", "claude")
        expected = known_bug_status(32) if OPEN_BUGS[32].active else PASS
        self.assertEqual(r.status, expected)

    def test_bug33_reproduction_matches_registry(self):
        unpaired = bug33_unpaired_marker_delete()
        if OPEN_BUGS[33].active:
            self.assertTrue(unpaired, "#33 active but no unpaired marker delete — fix landed? flip the flag")
        else:
            self.assertFalse(unpaired, "#33 flipped inactive but the /newroot marker delete is still unpaired")

    def test_marker_noise_gate_tracks_registry(self):
        r = expect_no_marker_noise("single_write", "codex")
        expected = known_bug_status(33) if OPEN_BUGS[33].active else PASS
        self.assertEqual(r.status, expected)

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
