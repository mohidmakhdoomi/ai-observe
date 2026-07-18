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

def validate_keep_artifacts(keep_artifacts: Optional[str]) -> Optional[Path]:
    """Boundary-check a `--keep-artifacts` path WITHOUT allocating anything.

    Pure validation (no mkdir, no temp dir) so it can run before the tool
    preflight — a bad in-repo destination is then rejected independent of tool
    availability or a writable temp dir. Returns the resolved path (or None for
    the default temp-dir case); raises `ArgError` on a tracked in-repo destination.
    """
    if keep_artifacts is None:
        return None
    p = Path(keep_artifacts).resolve()
    ignored = (ROOT / "tests" / "agent_sessions" / ARTIFACTS_DIRNAME).resolve()
    # `is_relative_to` returns True for equal paths too, so this also rejects
    # `--keep-artifacts .` run from the repo root (the ROOT == path case).
    if p.is_relative_to(ROOT) and not p.is_relative_to(ignored):
        raise ArgError(
            f"--keep-artifacts {keep_artifacts!r} resolves inside the repo working tree "
            f"({p}); choose a path OUTSIDE the repo or under {ignored} so raw artifacts "
            f"never enter git")
    return p


def allocate_artifact_dir(validated: Optional[Path]) -> tuple[Path, Callable[[], None]]:
    """Allocate the artifact dir from a pre-validated path.

    `None` → an auto-cleaning temp dir (Decision 7); otherwise create the
    validated persistent dir. Allocation happens only after preflight, so a
    missing-tool failure never depends on a writable temp dir.
    """
    if validated is None:
        td = tempfile.TemporaryDirectory(prefix="ai-observe-agent-sessions-")
        return Path(td.name), td.cleanup
    validated.mkdir(parents=True, exist_ok=True)
    return validated, lambda: None


def resolve_artifact_dir(keep_artifacts: Optional[str]) -> tuple[Path, Callable[[], None]]:
    """Validate then allocate (convenience for callers/tests)."""
    return allocate_artifact_dir(validate_keep_artifacts(keep_artifacts))


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


# Exit codes: 0 = checks ran, none failed; 1 = a check failed; 2 = usage/arg error;
# 3 = nothing runnable (zero actual checks — loud, never a silent green).
EXIT_NOTHING_RUNNABLE = 3


def real_checks(results: Sequence[CheckResult]) -> list[CheckResult]:
    """Results that represent an actual assertion (not an `excluded` report)."""
    return [r for r in results if r.status != EXCLUDED]


def final_exit_code(results: Sequence[CheckResult]) -> int:
    if any(r.is_fail for r in results):
        return 1
    if not real_checks(results):
        return EXIT_NOTHING_RUNNABLE
    return 0


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

    # 1. Validate the --keep-artifacts boundary (pure, no allocation) first, so a
    #    bad in-repo destination is rejected independent of tools or temp-dir writability.
    try:
        validated = validate_keep_artifacts(args.keep_artifacts)
    except ArgError as e:
        print(str(e), file=sys.stderr)
        return 2

    # 2. Tool selection. An *unknown* tool (not a known agent) is always an error,
    #    independent of scenarios — this is a typo/mistake, not an install issue.
    if args.tools:
        tools = [t.strip() for t in args.tools.split(",") if t.strip()]
        explicit = set(tools)
    else:
        tools = list(TOOLS)
        explicit = set()
    unknown_tools = [t for t in tools if t not in TOOLS]
    if unknown_tools:
        print(f"unknown tool(s): {', '.join(unknown_tools)}; known: {', '.join(TOOLS)}",
              file=sys.stderr)
        return 2

    # 3. Scenario selection (must precede presence preflight so applicability is known).
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

    # 4. Presence preflight — only for tools a selected scenario will actually USE.
    #    A requested tool that no selected scenario applies to is NOT preflit for
    #    presence: its absence is irrelevant and run_suite reports it as `excluded`
    #    (Codex iter-2: a requested-but-non-applicable pair must not hard-fail on
    #    the tool's absence). A known tool that IS applicable but missing → loud fail.
    used = {t for t in tools if any(t in s.applies_to for s in scenarios)}
    missing = [t for t in tools if t in used and not tool_available(t)]
    if missing:
        print(f"tool(s) not found on PATH: {', '.join(missing)}; install them or "
              f"narrow --tools", file=sys.stderr)
        return 2

    # 5. Allocate the artifact dir only now (after preflight) and run.
    artifact_dir, cleanup = allocate_artifact_dir(validated)
    try:
        ctx = RunContext(artifact_dir=artifact_dir, timeout=args.timeout)
        results = run_suite(tools, scenarios, ctx, explicit_tools=explicit)
    finally:
        cleanup()

    print(render_summary(results), file=sys.stderr)
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))

    code = final_exit_code(results)
    if code == EXIT_NOTHING_RUNNABLE:
        # Loud, never a silent green: an opt-in gating capability that ran zero
        # actual checks (no scenarios discovered/selected, or none applicable to the
        # requested tools) is a failure, not a success.
        print("no checks were run — nothing runnable for the requested tools/scenarios "
              "(no applicable scenario). This is not success; narrow or fix "
              "--tools/--scenarios.", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
