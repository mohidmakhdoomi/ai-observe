"""Tool-free self-tests: per-tool single-prompt argv + scenario registration.

Locks the exact non-interactive invocation each tool receives (regression guard
for the tool quirks the harness bakes in) and confirms the S1–S4 scenarios are
discoverable with the expected applicability — all without running any agent.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from ..__main__ import discover_scenarios
from ..drivers import chain_for, chained_command
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


class ChainedMultiTurnArgvTests(unittest.TestCase):
    """Pin the round-2 chained multi-turn shell strings per tool (tool-free)."""

    WD = Path("/tmp/wd")
    TURNS = ["t1", "t2", "t3"]

    def test_claude_chain_uses_continue_after_turn1(self):
        chain = chain_for("claude", self.TURNS, self.WD)
        self.assertEqual(chain,
                         "claude -p 't1' --dangerously-skip-permissions && "
                         "claude -c -p 't2' --dangerously-skip-permissions && "
                         "claude -c -p 't3' --dangerously-skip-permissions")

    def test_agy_chain_continue_and_add_dir(self):
        chain = chain_for("agy", self.TURNS, self.WD)
        self.assertIn("agy -p 't1' --dangerously-skip-permissions --add-dir '/tmp/wd'", chain)
        self.assertIn("agy -c -p 't2' --dangerously-skip-permissions --add-dir '/tmp/wd'", chain)

    def test_codex_chain_sandbox_precedes_resume(self):
        chain = chain_for("codex", self.TURNS, self.WD)
        self.assertEqual(chain,
                         "codex exec --sandbox workspace-write 't1' && "
                         "codex exec --sandbox workspace-write resume --last 't2' && "
                         "codex exec --sandbox workspace-write resume --last 't3'")
        # the documented footgun: --sandbox must never appear AFTER `resume`.
        self.assertNotIn("resume --last 't2' --sandbox", chain)

    def test_chained_command_wraps_in_bash_lc(self):
        cmd = chained_command("claude", self.TURNS, self.WD)
        self.assertEqual(cmd[:2], ["bash", "-lc"])


class ScenarioRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.reg = discover_scenarios()

    def test_all_scenarios_present(self):
        for name in ("single_write", "ephemeral", "modify", "subprocess",
                     "multi_turn", "timeline"):
            self.assertIn(name, self.reg, f"scenario {name!r} not discovered")

    def test_applicability(self):
        self.assertEqual(self.reg["single_write"].applies_to, {"claude", "agy", "codex"})
        self.assertEqual(self.reg["ephemeral"].applies_to, {"claude", "agy"})  # #32 path
        self.assertEqual(self.reg["modify"].applies_to, {"claude", "agy"})
        self.assertEqual(self.reg["subprocess"].applies_to, {"claude", "agy", "codex"})
        self.assertEqual(self.reg["multi_turn"].applies_to, {"claude", "agy", "codex"})
        self.assertEqual(self.reg["timeline"].applies_to, {"claude"})  # claude-only


if __name__ == "__main__":
    unittest.main()
