"""Tool-free self-tests: per-tool single-prompt argv + scenario registration.

Locks the exact non-interactive invocation each tool receives (regression guard
for the tool quirks the harness bakes in) and confirms the S1–S4 scenarios are
discoverable with the expected applicability — all without running any agent.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from ..__main__ import discover_scenarios
from ..harness import TOOLS


class SinglePromptArgvTests(unittest.TestCase):
    WD = Path("/tmp/wd")

    def test_claude_argv(self):
        self.assertEqual(TOOLS["claude"]("PROMPT", self.WD),
                         ["claude", "-p", "PROMPT", "--dangerously-skip-permissions"])

    def test_agy_argv_adds_dir(self):
        self.assertEqual(
            TOOLS["agy"]("PROMPT", self.WD),
            ["agy", "-p", "PROMPT", "--dangerously-skip-permissions",
             "--add-dir", str(self.WD)])

    def test_codex_argv_workspace_write(self):
        self.assertEqual(TOOLS["codex"]("PROMPT", self.WD),
                         ["codex", "exec", "--sandbox", "workspace-write", "PROMPT"])


class ScenarioRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.reg = discover_scenarios()

    def test_phase3_scenarios_present(self):
        for name in ("single_write", "ephemeral", "modify", "subprocess"):
            self.assertIn(name, self.reg, f"scenario {name!r} not discovered")

    def test_applicability(self):
        self.assertEqual(self.reg["single_write"].applies_to, {"claude", "agy", "codex"})
        self.assertEqual(self.reg["ephemeral"].applies_to, {"claude", "agy"})  # #32 path
        self.assertEqual(self.reg["modify"].applies_to, {"claude", "agy"})
        self.assertEqual(self.reg["subprocess"].applies_to, {"claude", "agy", "codex"})


if __name__ == "__main__":
    unittest.main()
