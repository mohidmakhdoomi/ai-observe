"""Tool-free self-tests for the graduated harness (Spec 38, Phase 1).

Exercises the entire in-process viewer-monitor path, ephemeral-port allocation,
and checkout-first entrypoint resolution — all against a static fixture, with no
agent tool involved. Run from the repo root:

    python -m unittest tests.agent_sessions.selftest.selftest_harness
"""

from __future__ import annotations

import unittest
from pathlib import Path

from .. import ROOT
from ..harness import ViewerMonitor, load_events, resolve_ai_observe

FIXTURE = ROOT / "tests" / "fixtures" / "viewer" / "basic.jsonl"


class ViewerMonitorFixtureTests(unittest.TestCase):
    """The in-process monitor serves a static fixture end-to-end (no agent)."""

    def setUp(self):
        self.assertTrue(FIXTURE.exists(), f"missing fixture {FIXTURE}")
        self.expected = len(load_events(FIXTURE))
        self.assertGreater(self.expected, 0, "fixture should carry events")

    def test_monitor_serves_session_and_events(self):
        mon = ViewerMonitor(FIXTURE)
        self.addCleanup(mon.stop)
        self.assertTrue(mon.start(), "viewer monitor failed to start on fixture")
        # /session is served.
        info = mon.session_info
        self.assertIsInstance(info, dict)
        # /events replays the full fixture backlog.
        events = mon.collect_events(max_wait=6.0, settle=1.0)
        self.assertEqual(
            len(events), self.expected,
            f"viewer served {len(events)} events; fixture has {self.expected}",
        )

    def test_ephemeral_ports_are_distinct_and_nonzero(self):
        mon_a = ViewerMonitor(FIXTURE)
        self.addCleanup(mon_a.stop)
        mon_b = ViewerMonitor(FIXTURE)
        self.addCleanup(mon_b.stop)
        self.assertTrue(mon_a.start())
        self.assertTrue(mon_b.start())
        self.assertGreater(mon_a.port, 0)
        self.assertGreater(mon_b.port, 0)
        self.assertNotEqual(
            mon_a.port, mon_b.port,
            "two monitors must bind distinct OS-assigned ports (no collision)",
        )
        # Both serve /session concurrently.
        self.assertIsInstance(mon_a.poll_session(), dict)
        self.assertIsInstance(mon_b.poll_session(), dict)


class EntrypointResolutionTests(unittest.TestCase):
    def test_prefers_checkout_bin_ai_observe(self):
        checkout = ROOT / "bin" / "ai-observe"
        self.assertTrue(checkout.exists(), "expected checkout bin/ai-observe")
        self.assertEqual(Path(resolve_ai_observe()), checkout)


class NoExperimentPathHackTests(unittest.TestCase):
    def test_no_experiments_dir_on_syspath(self):
        # N1, checked behaviorally (not by source-grep, which false-positives on
        # docstrings): importing the package must not place any experiments/ dir
        # on sys.path. It should add ROOT/src and nothing under experiments/.
        import sys

        import tests.agent_sessions  # noqa: F401 — ensures the package is imported

        offenders = [p for p in sys.path if "experiments" in Path(p).parts]
        self.assertEqual(offenders, [], f"experiments/ dir on sys.path: {offenders}")
        self.assertIn(str(ROOT / "src"), sys.path, "package should put ROOT/src on sys.path")


if __name__ == "__main__":
    unittest.main()
