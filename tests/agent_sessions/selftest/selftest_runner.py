"""Tool-free self-tests for the opt-in runner (Spec 38, Phase 2).

Exercises the gating spine — missing-tool loud exit, the M4 unauthenticated
(present-but-unusable) branch via a fake scenario raising `ToolUnusable`, the
explicit named `excluded` record for requested-but-non-applicable pairs, the
`--keep-artifacts` boundary (including `.`-from-root and a symlink), and temp-dir
auto-cleanup — all without any real agent tool.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .. import ROOT
from ..__main__ import (
    ARTIFACTS_DIRNAME,
    ArgError,
    RunContext,
    resolve_artifact_dir,
    run_suite,
)
from ..oracle import EXCLUDED, FAIL, PASS, CheckResult, ToolUnusable


class _FakeScenario:
    def __init__(self, name, applies_to, on_run):
        self.name = name
        self.applies_to = set(applies_to)
        self._on_run = on_run

    def run(self, tool, ctx):
        return self._on_run(tool, ctx)


class RunSuiteTests(unittest.TestCase):
    def setUp(self):
        self.ctx = RunContext(artifact_dir=Path(tempfile.gettempdir()))

    def test_applicable_tool_runs(self):
        scen = _FakeScenario("s", {"claude"},
                             lambda tool, ctx: [CheckResult("s", tool, "agent-actual", PASS)])
        results = run_suite(["claude"], [scen], self.ctx, explicit_tools={"claude"})
        self.assertEqual([r.status for r in results], [PASS])

    def test_explicit_non_applicable_tool_is_excluded_and_named(self):
        scen = _FakeScenario("timeline", {"claude"},
                             lambda tool, ctx: [CheckResult("timeline", tool, "viewer", PASS)])
        results = run_suite(["claude", "codex"], [scen], self.ctx,
                            explicit_tools={"claude", "codex"})
        excluded = [r for r in results if r.status == EXCLUDED]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0].tool, "codex")
        self.assertIn("codex", excluded[0].detail)
        # claude still ran.
        self.assertTrue(any(r.tool == "claude" and r.status == PASS for r in results))

    def test_non_applicable_tool_silent_when_not_explicitly_requested(self):
        scen = _FakeScenario("timeline", {"claude"},
                             lambda tool, ctx: [CheckResult("timeline", tool, "viewer", PASS)])
        # Default all-tools run: codex not explicitly named → informational skip.
        results = run_suite(["claude", "codex"], [scen], self.ctx, explicit_tools=set())
        self.assertFalse(any(r.status == EXCLUDED for r in results))

    def test_tool_unusable_becomes_loud_named_fail(self):
        def boom(tool, ctx):
            raise ToolUnusable(tool, "produced zero watched-root events")
        scen = _FakeScenario("single_write", {"agy"}, boom)
        results = run_suite(["agy"], [scen], self.ctx, explicit_tools={"agy"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, FAIL)
        self.assertIn("agy", results[0].detail)
        self.assertIn("not authenticated", results[0].detail)


class StubToolSeamTests(unittest.TestCase):
    """The M4 present-but-unusable path through *real* PATH tool resolution.

    Puts a stub agent on a temp PATH (installed, but produces nothing), so
    `tool_available` resolves it via PATH exactly like a real tool, then drives
    the actual detection rule (`ensure_tool_usable`) and the runner's loud-fail
    rendering — all without a real agent, ai-observe, or strace.
    """

    def setUp(self):
        self.ctx = RunContext(artifact_dir=Path(tempfile.gettempdir()))

    def test_present_but_unusable_stub_tool_is_loud_named_fail(self):
        import types

        from ..harness import tool_available
        from ..oracle import ensure_tool_usable

        with tempfile.TemporaryDirectory() as td:
            stub = Path(td) / "stubagent"
            stub.write_text("#!/bin/sh\nexit 0\n")  # present, writes nothing
            stub.chmod(0o755)
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = str(td) + os.pathsep + old_path
            try:
                # Real PATH resolution finds the stub (installed).
                self.assertTrue(tool_available("stubagent"))

                def run(tool, ctx):
                    cp = subprocess.run([tool], capture_output=True, text=True)
                    # No events produced (nothing was written / observed).
                    result = types.SimpleNamespace(
                        returncode=cp.returncode, disk_events={"total": 0})
                    ensure_tool_usable(tool, result)  # raises ToolUnusable
                    return []

                scen = _FakeScenario("single_write", {"stubagent"}, run)
                results = run_suite(["stubagent"], [scen], self.ctx,
                                    explicit_tools={"stubagent"})
            finally:
                os.environ["PATH"] = old_path

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, FAIL)
        self.assertIn("stubagent", results[0].detail)
        self.assertIn("not authenticated", results[0].detail)


class KeepArtifactsBoundaryTests(unittest.TestCase):
    def test_repo_root_rejected(self):
        with self.assertRaises(ArgError):
            resolve_artifact_dir(str(ROOT))

    def test_tracked_in_repo_path_rejected(self):
        with self.assertRaises(ArgError):
            resolve_artifact_dir(str(ROOT / "tests"))

    def test_symlink_into_repo_rejected(self):
        # A symlink that resolves into the repo tree must still be rejected.
        with tempfile.TemporaryDirectory() as td:
            link = Path(td) / "link_into_repo"
            link.symlink_to(ROOT / "tests")
            with self.assertRaises(ArgError):
                resolve_artifact_dir(str(link))

    def test_ignored_subtree_accepted(self):
        target = ROOT / "tests" / "agent_sessions" / ARTIFACTS_DIRNAME / "st_probe"
        path, cleanup = resolve_artifact_dir(str(target))
        try:
            self.assertTrue(path.is_dir())
        finally:
            cleanup()
            # tidy the probe dir we created under the ignored subtree
            try:
                path.rmdir()
                path.parent.rmdir()
            except OSError:
                pass

    def test_outside_repo_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "artifacts"
            path, cleanup = resolve_artifact_dir(str(target))
            self.addCleanup(cleanup)
            self.assertTrue(path.is_dir())


class TempDirCleanupTests(unittest.TestCase):
    def test_default_temp_dir_is_removed_on_cleanup(self):
        path, cleanup = resolve_artifact_dir(None)
        self.assertTrue(path.is_dir())
        cleanup()
        self.assertFalse(path.exists(), "default temp artifact dir must auto-clean")


class CliSubprocessTests(unittest.TestCase):
    """End-to-end via the real CLI, run from the repo root (inherits sys.path)."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "tests.agent_sessions", *args],
            cwd=str(ROOT), capture_output=True, text=True)

    def test_missing_tool_exits_nonzero_and_names_it(self):
        cp = self._run("--tools", "nope")
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("nope", cp.stderr)

    def test_keep_artifacts_dot_from_repo_root_rejected(self):
        cp = self._run("--keep-artifacts", ".")  # cwd is ROOT
        self.assertEqual(cp.returncode, 2)
        self.assertIn("inside the repo working tree", cp.stderr)


if __name__ == "__main__":
    unittest.main()
