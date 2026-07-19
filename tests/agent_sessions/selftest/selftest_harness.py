"""Tool-free self-tests for the graduated harness (Spec 38, Phase 1).

Exercises the entire in-process viewer-monitor path, ephemeral-port allocation,
and checkout-first entrypoint resolution — all against a static fixture, with no
agent tool involved. Run from the repo root:

    python -m unittest tests.agent_sessions.selftest.selftest_harness
"""

from __future__ import annotations

import os
import subprocess
import time
import unittest
from pathlib import Path

from .. import ROOT
from ..harness import ViewerMonitor, load_events, resolve_ai_observe, terminate_process_group

FIXTURE = ROOT / "tests" / "fixtures" / "viewer" / "basic.jsonl"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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


class ProcessGroupTeardownTests(unittest.TestCase):
    """`terminate_process_group` kills the WHOLE tree, not just the leader (review item 3).

    Tool-free (uses `sleep`/`bash`, no agent). Not collected by CI's `test_*.py` glob.
    """

    def test_reaps_session_leader(self):
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(lambda: terminate_process_group(proc))
        self.assertIsNone(proc.poll(), "sleep should still be running")
        terminate_process_group(proc)
        self.assertIsNotNone(proc.returncode, "leader must be reaped after teardown")
        self.assertNotEqual(proc.returncode, 0, "a killed process does not exit 0")

    def test_kills_grandchild_in_group(self):
        # Parent bash backgrounds a grandchild, prints its PID, then waits — both share
        # the new session's process group. Killing only the leader would orphan the
        # grandchild; killpg takes out the whole group.
        proc = subprocess.Popen(
            ["bash", "-c", "sleep 30 & echo $!; wait"],
            stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(lambda: terminate_process_group(proc))
        grandchild = int(proc.stdout.readline().strip())
        self.assertTrue(_pid_alive(grandchild), "grandchild should be running")
        terminate_process_group(proc)
        # The orphaned grandchild is reparented to init and reaped after the group
        # SIGKILL; poll briefly for it to disappear.
        deadline = time.time() + 5
        while time.time() < deadline and _pid_alive(grandchild):
            time.sleep(0.1)
        self.assertFalse(_pid_alive(grandchild),
                         "grandchild survived group teardown (process tree leaked)")


if __name__ == "__main__":
    unittest.main()
