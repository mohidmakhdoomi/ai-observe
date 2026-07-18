"""Live-tier oracle scenarios (Spec 38).

Each `check_*.py` module exposes a `SCENARIO` object implementing the runner's
`Scenario` protocol (`.name`, `.applies_to`, `.run(tool, ctx)`). These drive a real
agent under ai-observe and assert the three-view oracle, so they require installed +
authenticated tools — they are **excluded from CI by construction** (not named
`test_*.py`, not in a top-level module). The `check_` prefix also keeps them out of
`unittest discover`'s default `test*.py` pattern.
"""

from __future__ import annotations

from pathlib import Path

from ..harness import SessionResult, load_events, run_observed_session
from ..oracle import ensure_tool_usable


def session_dirs(ctx, session: str) -> tuple[Path, Path]:
    """Per-(scenario,tool) work + out dirs under the run's artifact dir."""
    base = Path(ctx.artifact_dir) / session
    workdir = base / "work"
    outdir = base / "out"
    return workdir, outdir


def drive(tool: str, prompt: str, session: str, ctx, *,
          roots: Path | None = None) -> tuple[SessionResult, list[dict]]:
    """Run one observed session and return (result, canonical events).

    Applies the M4 usability gate: a nonzero return or zero watched-root events
    raises `ToolUnusable(tool)` (loud, named — never a silent skip).
    """
    workdir, outdir = session_dirs(ctx, session)
    res = run_observed_session(tool, prompt, session, workdir, outdir,
                               roots=roots, timeout=ctx.timeout)
    ensure_tool_usable(tool, res)
    return res, load_events(res.jsonl_path)
