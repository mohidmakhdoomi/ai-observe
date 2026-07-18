"""Round-2 multi-turn chained driver (Exp 4), graduated (Spec 38, Phase 4).

Drives a *chained* multi-turn conversation under a SINGLE ai-observe wrapper —
`ai-observe -- bash -lc "<turn1> && <turn2> && …"` — one strace tree, per-turn agent
invocations captured as grandchildren by descendant tracing. Conversation
continuity across turns is each tool's own resume/continue mechanism:

  * claude: `claude -p <t1>`            then `claude -c -p <tN>`
  * agy:    `agy -p <t1> --add-dir <wd>` then `agy -c -p <tN> --add-dir <wd>`
  * codex:  `codex exec --sandbox workspace-write <t1>`
            then `codex exec --sandbox workspace-write resume --last <tN>`

The `--sandbox` flag is an `exec` GLOBAL flag: on resume it MUST precede the
`resume` subcommand (`codex exec --sandbox … resume …`), never after it — a
documented footgun from the round-2 experiment, pinned by a tool-free argv self-test.

Stdlib only.
"""

from __future__ import annotations

from pathlib import Path

from .harness import load_events, run_observed_command
from .oracle import ensure_tool_usable


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def chain_for(tool: str, turns: list[str], workdir: Path) -> str:
    """Build a single shell string running all `turns` chained for `tool`.

    Turns are joined with `&&` so a failed turn aborts the chain (surfacing broken
    continuity loudly rather than silently skipping later turns).
    """
    q = _sh_quote
    parts: list[str] = []
    if tool == "claude":
        parts.append(f"claude -p {q(turns[0])} --dangerously-skip-permissions")
        for t in turns[1:]:
            parts.append(f"claude -c -p {q(t)} --dangerously-skip-permissions")
    elif tool == "agy":
        wd = str(workdir)
        parts.append(f"agy -p {q(turns[0])} --dangerously-skip-permissions --add-dir {q(wd)}")
        for t in turns[1:]:
            parts.append(f"agy -c -p {q(t)} --dangerously-skip-permissions --add-dir {q(wd)}")
    elif tool == "codex":
        parts.append(f"codex exec --sandbox workspace-write {q(turns[0])}")
        for t in turns[1:]:
            parts.append(f"codex exec --sandbox workspace-write resume --last {q(t)}")
    else:
        raise ValueError(f"unknown tool {tool!r}")
    return " && ".join(parts)


def chained_command(tool: str, turns: list[str], workdir: Path) -> list[str]:
    """The argv (after ai-observe's `--`) for a chained multi-turn run."""
    return ["bash", "-lc", chain_for(tool, turns, workdir)]


def run_multi_turn(tool: str, turns: list[str], session: str, workdir: Path,
                   outdir: Path, *, timeout: float = 420.0):
    """Drive a chained multi-turn conversation under one ai-observe wrapper.

    Returns (SessionResult, canonical events). Applies the M4 usability gate.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    res = run_observed_command(
        chained_command(tool, turns, workdir),
        tool=tool, session=session, workdir=workdir, outdir=outdir, timeout=timeout)
    ensure_tool_usable(tool, res)
    return res, load_events(res.jsonl_path)
