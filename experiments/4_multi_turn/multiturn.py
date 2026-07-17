"""Experiment 4: multi-turn / repeated prompting within ONE observed session.

The original #31 use case that round 1 never exercised: every round-1 run was a
single one-shot prompt. Here we drive a *chained* multi-turn conversation under
a **single ai-observe wrapper** and verify ai-observe captures file operations
from turns 2, 3, ... just as well as turn 1.

Driving mechanism (the small harness extension the issue anticipated, kept here
in the experiment folder): wrap a per-tool *chained shell driver* in one
`ai-observe -- bash -c "<turn1> && <turn2> && <turn3>"`. This is a single
ai-observe process = a single strace tree; the per-turn agent invocations are
grandchildren captured by descendant tracing (already proven by round 1's
subprocess scenario). Conversation continuity across turns is the *agent's* job,
via each tool's resume/continue mechanism:

  * claude: `claude -p <t1>` then `claude -c -p <tN>`     (-c = --continue, most recent in cwd)
  * agy:    `agy -p <t1>`    then `agy -c -p <tN>`         (-c = --continue)
  * codex:  `codex exec <t1>` then `codex exec resume --last <tN>`

Why a chained driver rather than tmux send-keys: it is scriptable, hermetic,
and repeatable without a PTY -- the same rationale that made non-interactive the
harness default in round 1. tmux send-keys remains the documented fallback for
genuinely interactive-only flows; multi-turn does not require it because all
three tools expose a resume/continue print mode.

Turns (each prompt names its file explicitly, so capture is verifiable even if a
tool's continuity is imperfect -- but we ALSO detect continuity as a side fact):
  1. create turn1.txt = "one"
  2. create turn2.txt = "two"
  3. append "three" to turn1.txt

Verifies, per tool:
  * actual files on disk (agent-reality oracle)
  * canonical .jsonl events attribute a create to each of turn1/turn2 and a
    modify to turn1 -- i.e. later-turn ops are NOT lost
  * viewer served the same events (sanitized SSE count)

Run: python3 multiturn.py [--tools claude,agy,codex]
Writes data/output/multiturn_report.json (curated, committed) + raw artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "1_driving_mechanism"))
from harness import (  # noqa: E402
    AI_OBSERVE, SRC, ViewerMonitor, load_events, summarize_events,
    list_workdir, tool_available,
)

OUT = HERE / "data" / "output"

TURNS = [
    "Create a file named turn1.txt containing exactly the word one. Then stop.",
    "Create a file named turn2.txt containing exactly the word two. Then stop.",
    "Append a line containing exactly the word three to the existing file turn1.txt. Do not create other files. Then stop.",
]


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _chain_for(tool: str, workdir: Path) -> str:
    """Build a single shell string running all TURNS chained for `tool`."""
    q = _sh_quote
    parts: list[str] = []
    if tool == "claude":
        parts.append(f"claude -p {q(TURNS[0])} --dangerously-skip-permissions")
        for t in TURNS[1:]:
            parts.append(f"claude -c -p {q(t)} --dangerously-skip-permissions")
    elif tool == "agy":
        wd = str(workdir)
        parts.append(f"agy -p {q(TURNS[0])} --dangerously-skip-permissions --add-dir {q(wd)}")
        for t in TURNS[1:]:
            parts.append(f"agy -c -p {q(t)} --dangerously-skip-permissions --add-dir {q(wd)}")
    elif tool == "codex":
        # `--sandbox` is an `exec` global flag; on resume it MUST precede the
        # `resume` subcommand (`codex exec resume --sandbox ...` is rejected --
        # "unexpected argument '--sandbox'"). Discovered while debugging a turn-2
        # abort; documented in notes.md.
        parts.append(f"codex exec --sandbox workspace-write {q(TURNS[0])}")
        for t in TURNS[1:]:
            parts.append(f"codex exec --sandbox workspace-write resume --last {q(t)}")
    else:
        raise ValueError(tool)
    # `&&` so a failed turn aborts the chain (surfaces broken continuity loudly).
    return " && ".join(parts)


def _drive(tool: str, workdir: Path, session: str, viewer_port: int, timeout: float) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    chain = _chain_for(tool, workdir)
    cmd = [str(AI_OBSERVE), "--session", session, "--", "bash", "-lc", chain]
    env = dict(os.environ)
    env["AI_OBSERVE_DIR"] = str(OUT)
    env["AI_OBSERVE_ROOTS"] = str(workdir)

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(workdir), env=env, timeout=timeout,
                              capture_output=True, text=True)
        rc = proc.returncode
        timed_out = False
        # Whether ai-observe emitted a stderr warning (kept as a bool, not text:
        # the raw warning echoes absolute paths, which must not enter committed
        # curated evidence -- relative-path-only rule).
        had_stderr = bool((proc.stderr or "").strip())
    except subprocess.TimeoutExpired:
        rc = -9
        timed_out = True
        had_stderr = True
    duration = round(time.time() - t0, 1)

    jsonl = OUT / f"{session}.jsonl"
    disk = summarize_events(load_events(jsonl), workdir=workdir)
    rows = disk.get("rows", [])

    def ops_on(name: str) -> list[str]:
        return sorted({r.get("operation") for r in rows
                       if name == (r.get("path") or "").rsplit("/", 1)[-1]})

    # Count direct writes that LAND ON a final file (create/rename/modify whose
    # destination basename == name). Atomic-write tools (claude) rewrite a file
    # as a fresh tmp+rename, so a turn-3 "append" to turn1.txt shows up as a
    # SECOND rename onto turn1.txt, not as a "modify" -- both count as a write.
    all_events = load_events(jsonl)

    def writes_onto(name: str) -> int:
        n = 0
        for e in all_events:
            if e.get("source") != "strace":
                continue
            dest = (e.get("new_path") or e.get("path") or "").rsplit("/", 1)[-1]
            if dest == name and e.get("operation") in ("create", "rename", "modify", "write"):
                n += 1
        return n

    files = list_workdir(workdir)
    t1 = workdir / "turn1.txt"
    t1_content = t1.read_text() if t1.exists() else ""

    viewer_count = 0
    if jsonl.exists() and jsonl.stat().st_size > 0:
        mon = ViewerMonitor(jsonl, port=viewer_port)
        if mon.start():
            viewer_count = len(mon.collect_events())
            mon.stop()

    check = {
        "actual_files": files,
        "turn1_present": "turn1.txt" in files,
        "turn2_present": "turn2.txt" in files,
        "turn1_has_one": "one" in t1_content,
        "turn1_has_three_appended": "three" in t1_content,      # continuity signal
        "ops_on_turn1": ops_on("turn1.txt"),
        "ops_on_turn2": ops_on("turn2.txt"),
        "writes_onto_turn1": writes_onto("turn1.txt"),  # expect >=2: turn1 create + turn3 rewrite
        "writes_onto_turn2": writes_onto("turn2.txt"),  # expect >=1: turn2 create
        # The core assertion: LATER-turn file ops were captured, not just turn 1.
        "turn2_create_captured": writes_onto("turn2.txt") >= 1,          # turn 2 op captured
        "turn3_op_on_turn1_captured": writes_onto("turn1.txt") >= 2,     # turn 3 op captured
    }

    return {
        "tool": tool, "session": session, "ok": rc == 0, "returncode": rc,
        "duration_s": duration, "timed_out": timed_out, "had_stderr": had_stderr,
        "disk_event_total": disk.get("total"),
        "disk_by_source": disk.get("by_source"),
        "disk_by_operation": disk.get("by_operation"),
        "viewer_events_count": viewer_count,
        "check": check,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", default="claude,agy,codex")
    ap.add_argument("--timeout", type=float, default=420.0)
    args = ap.parse_args()
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]

    report: dict = {"turns": TURNS, "results": {}}
    port = 7900
    for tool in tools:
        if not tool_available(tool):
            report["results"][tool] = {"available": False}
            print(f"[{tool}] NOT AVAILABLE")
            continue
        workdir = HERE / "work" / tool
        print(f"[{tool}] driving {len(TURNS)}-turn chain ...")
        res = _drive(tool, workdir, f"mt_{tool}", port, args.timeout)
        port += 1
        report["results"][tool] = res
        c = res["check"]
        print(f"[{tool}] ok={res['ok']} rc={res['returncode']} dur={res['duration_s']}s "
              f"events={res['disk_event_total']} {res['disk_by_operation']} "
              f"viewer={res['viewer_events_count']}")
        print(f"        files={c['actual_files']} t2_create_captured={c['turn2_create_captured']} "
              f"t3_op_captured={c['turn3_op_on_turn1_captured']} (writes_onto_turn1={c['writes_onto_turn1']}) "
              f"t1_appended={c['turn1_has_three_appended']}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "multiturn_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {OUT / 'multiturn_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
