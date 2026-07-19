"""Tool-free self-tests for the S7 degraded #36 flip-home (Spec 38, Phase 5).

Exercises `authority_overstated` + `expect_authority_not_overstated` against synthetic
`.meta.json` dicts shaped exactly like `build_session_meta`'s output — the buggy
(authority-overstated) shape and a hypothetical fixed shape — through BOTH registry
states (active + flipped). This proves the one-line flip (`OPEN_BUGS[36].active = False`)
is rot-proof in both directions with NO claude run:
  * fix landed (role downgraded) but flag NOT flipped → FAIL demanding the flip
  * flag flipped but bug still reproduces               → FAIL flagging the regression
"""

from __future__ import annotations

import unittest

from ..oracle import (
    FAIL,
    PASS,
    KnownBug,
    authority_overstated,
    expect_authority_not_overstated,
    known_bug_status,
)


def _meta(jsonl_role: str, parser_status: str = "parser_failure_partial") -> dict:
    """A `.meta.json` shaped like `build_session_meta` output.

    The `artifacts` map deliberately mixes a bare-string `authoritative_event_path`
    with dict entries, matching the real sidecar — the role scan must tolerate the
    non-Mapping value without raising.
    """
    return {
        "schema_version": 1,
        "session_id": "deg_probe",
        "parser": {"status": parser_status, "source": "strace"},
        "artifacts": {
            "authoritative_event_path": "deg_probe.jsonl",
            "trace": {"path": "deg_probe.trace", "role": "trace", "exists": True},
            "jsonl": {"path": "deg_probe.jsonl", "role": jsonl_role, "exists": True},
            "partial": {"path": "deg_probe.jsonl.partial", "role": "partial_direct", "exists": True},
            "rebuilt": {"path": "deg_probe.jsonl.rebuilt", "role": "absent", "exists": False},
            "meta": {"path": "deg_probe.meta.json", "role": "metadata", "exists": True},
        },
        "warnings": ["snapshot fallback: net events only"],
    }


# The #36 signature: parse-failure status + a `.jsonl` STILL labelled authoritative_complete.
BUGGY = _meta("authoritative_complete")
# Hypothetical fix: the role is downgraded once the direct parser has failed.
FIXED_SHAPE = _meta("authoritative_net")

_ACTIVE = {36: KnownBug(36, "sidecar overstates authority", active=True)}
_FLIPPED = {36: KnownBug(36, "sidecar overstates authority", active=False)}


class AuthorityOverstatedDetectionTests(unittest.TestCase):
    def test_buggy_shape_detected(self):
        self.assertTrue(authority_overstated(BUGGY))

    def test_fixed_shape_not_detected(self):
        self.assertFalse(authority_overstated(FIXED_SHAPE))

    def test_healthy_parser_never_overstated(self):
        # A clean parse never trips the gate, even with an authoritative_complete jsonl.
        self.assertFalse(authority_overstated(_meta("authoritative_complete", parser_status="ok")))

    def test_mixed_type_artifacts_map_does_not_raise(self):
        # `authoritative_event_path` is a bare string alongside dict entries; the role
        # scan must not choke on the non-Mapping value.
        self.assertIsInstance(BUGGY["artifacts"]["authoritative_event_path"], str)
        self.assertTrue(authority_overstated(BUGGY))


class Bug36FlipHomeTests(unittest.TestCase):
    """`expect_authority_not_overstated` is rot-proof in BOTH registry directions."""

    def test_active_buggy_is_known_bug(self):
        r = expect_authority_not_overstated("degraded", "claude", BUGGY, registry=_ACTIVE)
        self.assertEqual(r.status, known_bug_status(36))

    def test_active_but_fixed_shape_fails_flip_the_flag(self):
        # Fix landed (role downgraded) but flag not flipped → loud FAIL demanding the flip.
        r = expect_authority_not_overstated("degraded", "claude", FIXED_SHAPE, registry=_ACTIVE)
        self.assertEqual(r.status, FAIL)
        self.assertIn("flip", r.detail.lower())

    def test_flipped_and_fixed_passes(self):
        # The one-line flip lands with the real fix → hard PASS.
        r = expect_authority_not_overstated("degraded", "claude", FIXED_SHAPE, registry=_FLIPPED)
        self.assertEqual(r.status, PASS)

    def test_flipped_but_regressed_fails(self):
        # Flag flipped but the bug still reproduces → loud regression FAIL.
        r = expect_authority_not_overstated("degraded", "claude", BUGGY, registry=_FLIPPED)
        self.assertEqual(r.status, FAIL)
        self.assertIn("regressed", r.detail.lower())


if __name__ == "__main__":
    unittest.main()
