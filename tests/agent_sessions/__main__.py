"""Opt-in runner for the live-agent testing suite (Spec 38).

Run from the **repo root** (PEP 420 namespace resolution; `tests/` has no
`__init__.py`):

    python -m tests.agent_sessions [--tools claude,agy,codex] [--scenarios ...]
                                   [--json] [--keep-artifacts DIR] [--timeout S]
    python -m tests.agent_sessions --selftest      # tool-free plumbing/oracle tests

Gating (Decision 4 / req 6): a requested tool missing from PATH, or present but
unusable (no auth / no events), is a **loud, named failure** — never a silent skip.
A requested-but-non-applicable tool/scenario pair (e.g. codex + a claude-only
scenario) is reported as an explicit, named `excluded` record, not silently dropped.

Artifacts (Decision 7): raw `.trace`/`.jsonl`/`.meta.json` default to an
auto-cleaning temp dir; `--keep-artifacts DIR` persists them but refuses a tracked
in-repo destination.

Stdlib-only.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol, Sequence

from . import ROOT
from .harness import TOOLS, tool_available
from .oracle import EXCLUDED, FAIL, CheckResult, ToolUnusable

# The suite's own git-ignored artifact subtree (Phase 6 adds the .gitignore).
ARTIFACTS_DIRNAME = ".artifacts"


class ArgError(Exception):
    """A user-input error that should exit 2 with a clear stderr message."""


# ---------------------------------------------------------------------------
# Scenario protocol + registry
# ---------------------------------------------------------------------------

@dataclass
class RunContext:
    artifact_dir: Path
    timeout: float = 240.0


class Scenario(Protocol):
    name: str
    applies_to: set  # tools this scenario can run against

    def run(self, tool: str, ctx: RunContext) -> list[CheckResult]:
        ...


def discover_scenarios() -> dict:
    """Discover `scenarios/check_*.py` modules by short-name.

    Each module exposes a `SCENARIO` object implementing the `Scenario` protocol.
    Returns `{}` before the scenarios package exists (Phase 3 adds it) — the runner
    handles an empty registry.
    """
    registry: dict = {}
    scen_dir = ROOT / "tests" / "agent_sessions" / "scenarios"
    if not scen_dir.is_dir():
        return registry
    for py in sorted(scen_dir.glob("check_*.py")):
        modname = f"tests.agent_sessions.scenarios.{py.stem}"
        mod = importlib.import_module(modname)
        scen = getattr(mod, "SCENARIO", None)
        if scen is not None:
            registry[scen.name] = scen
    return registry


# ---------------------------------------------------------------------------
# Suite execution (applicability + ToolUnusable handling)
# ---------------------------------------------------------------------------

def run_suite(tools: Sequence[str], scenarios: Iterable, ctx: RunContext,
              *, explicit_tools: Optional[set] = None) -> list[CheckResult]:
    """Run each scenario against each applicable tool.

    A tool that a scenario excludes is reported as an explicit `excluded`
    `CheckResult` **only when the user explicitly named it** via `--tools`
    (`explicit_tools`); on a default all-tools run, non-applicable pairs are
    informational and silently skipped (they were not requested). A `ToolUnusable`
    raised by a scenario becomes a loud, named `fail`.
    """
    explicit = explicit_tools or set()
    results: list[CheckResult] = []
    for scen in scenarios:
        for tool in tools:
            if tool not in scen.applies_to:
                if tool in explicit:
                    applies = "/".join(sorted(scen.applies_to)) or "no tools"
                    results.append(CheckResult(
                        scen.name, tool, "runner", EXCLUDED,
                        f"scenario {scen.name!r} does not apply to tool {tool!r} ({applies}-only)"))
                continue
            try:
                results.extend(scen.run(tool, ctx))
            except ToolUnusable as e:
                results.append(CheckResult(
                    scen.name, tool, "runner", FAIL,
                    f"tool {e.tool!r} produced no events — not authenticated or agent "
                    f"error; rerun with --keep-artifacts to inspect stderr ({e.detail})"))
    return results


# ---------------------------------------------------------------------------
# Artifact directory management (Decision 7)
# ---------------------------------------------------------------------------

def resolve_artifact_dir(keep_artifacts: Optional[str]) -> tuple[Path, Callable[[], None]]:
    """Return (artifact_dir, cleanup). Default is an auto-cleaning temp dir.

    `--keep-artifacts DIR` persists artifacts but refuses a destination inside the
    repo working tree (unless under the suite's ignored `.artifacts/` subtree),
    keeping raw artifacts out of git by construction.
    """
    if keep_artifacts is None:
        td = tempfile.TemporaryDirectory(prefix="ai-observe-agent-sessions-")
        return Path(td.name), td.cleanup

    p = Path(keep_artifacts).resolve()
    ignored = (ROOT / "tests" / "agent_sessions" / ARTIFACTS_DIRNAME).resolve()
    # `is_relative_to` returns True for equal paths too, so this also rejects
    # `--keep-artifacts .` run from the repo root (the ROOT == path case).
    if p.is_relative_to(ROOT) and not p.is_relative_to(ignored):
        raise ArgError(
            f"--keep-artifacts {keep_artifacts!r} resolves inside the repo working tree "
            f"({p}); choose a path OUTSIDE the repo or under {ignored} so raw artifacts "
            f"never enter git")
    p.mkdir(parents=True, exist_ok=True)
    return p, lambda: None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_summary(results: Sequence[CheckResult]) -> str:
    lines = []
    counts: dict[str, int] = {}
    for r in results:
        key = "known-bug" if r.status.startswith("known-bug") else r.status
        counts[key] = counts.get(key, 0) + 1
        lines.append(f"  [{r.status:>12}] {r.scenario}/{r.tool} ({r.view}): {r.detail}")
    header = "agent-sessions suite: " + ", ".join(
        f"{k}={v}" for k, v in sorted(counts.items())) if results else \
        "agent-sessions suite: no checks run"
    return header + ("\n" + "\n".join(lines) if lines else "")


def exit_code_for(results: Sequence[CheckResult]) -> int:
    return 1 if any(r.is_fail for r in results) else 0


# ---------------------------------------------------------------------------
# Self-test tier (tool-free)
# ---------------------------------------------------------------------------

def _run_selftest() -> int:
    selftest_dir = ROOT / "tests" / "agent_sessions" / "selftest"
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for py in sorted(selftest_dir.glob("selftest_*.py")):
        mod = importlib.import_module(f"tests.agent_sessions.selftest.{py.stem}")
        suite.addTests(loader.loadTestsFromModule(mod))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tests.agent_sessions",
        description="Opt-in live-agent testing suite for ai-observe (run from the repo root).")
    p.add_argument("--tools", default=None,
                   help="comma-separated tools (default: all of %s)" % ",".join(TOOLS))
    p.add_argument("--scenarios", default=None,
                   help="comma-separated scenario short-names (default: all discovered)")
    p.add_argument("--json", action="store_true", help="emit a JSON report on stdout")
    p.add_argument("--keep-artifacts", default=None, metavar="DIR",
                   help="persist raw artifacts to DIR (must be outside the repo tree)")
    p.add_argument("--timeout", type=float, default=240.0,
                   help="per-session agent timeout in seconds (default: 240)")
    p.add_argument("--selftest", action="store_true",
                   help="run the tool-free plumbing/oracle self-tests and exit")
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.selftest:
        return _run_selftest()

    # Validate the --keep-artifacts boundary first, before probing PATH, so a bad
    # in-repo destination is rejected independent of which tools are installed.
    try:
        artifact_dir, cleanup = resolve_artifact_dir(args.keep_artifacts)
    except ArgError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        # Tool selection + presence preflight (loud, named).
        if args.tools:
            tools = [t.strip() for t in args.tools.split(",") if t.strip()]
            explicit = set(tools)
        else:
            tools = list(TOOLS)
            explicit = set()
        for t in tools:
            if not tool_available(t):
                print(f"tool {t!r} not found on PATH; install it or narrow --tools",
                      file=sys.stderr)
                return 2

        # Scenario selection.
        registry = discover_scenarios()
        if args.scenarios:
            wanted = [s.strip() for s in args.scenarios.split(",") if s.strip()]
            unknown = [s for s in wanted if s not in registry]
            if unknown:
                print(f"unknown scenario(s): {', '.join(unknown)}; "
                      f"available: {', '.join(sorted(registry)) or '(none yet)'}",
                      file=sys.stderr)
                return 2
            scenarios = [registry[s] for s in wanted]
        else:
            scenarios = [registry[s] for s in sorted(registry)]

        ctx = RunContext(artifact_dir=artifact_dir, timeout=args.timeout)
        results = run_suite(tools, scenarios, ctx, explicit_tools=explicit)
    finally:
        cleanup()

    print(render_summary(results), file=sys.stderr)
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    return exit_code_for(results)


if __name__ == "__main__":
    sys.exit(main())
