"""Experiment 2: scenario coverage for ai-observe, reusing the Experiment 1 harness.

Each scenario drives a real agent under ai-observe with a prompt engineered to
exercise a specific ai-observe behavior, then compares three surfaces:
  (a) what the agent actually did (files left on disk + a per-scenario check),
  (b) what ai-observe reported (canonical .jsonl events + provenance),
  (c) what the viewer served (sanitized SSE count).

Scenarios:
  * subprocess  -- agent runs a shell loop creating N files via a grandchild
                   process. Tests process-tree-scoped capture.
  * ephemeral   -- agent creates a file then deletes it in the same session.
                   Tests direct (strace) vs inferred (snapshot/net) provenance,
                   and the documented issue #18 (create+delete between snapshots).
  * modify      -- a pre-seeded file is appended to. Tests modify vs create.

Run: python3 scenarios.py [--tools claude,agy,codex] [--only subprocess,...]
Writes data/output/coverage_matrix.json (curated, committed) + raw artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Import the harness from Experiment 1.
sys.path.insert(0, str(HERE.parent / "1_driving_mechanism"))
from harness import run_observed_session, tool_available  # noqa: E402

OUT = HERE / "data" / "output"


# --- scenario definitions -------------------------------------------------
# Each has: prompt, a setup(workdir) that prepares state, and a check(workdir,
# result) returning a dict of scenario-specific verification facts.

def _noop_setup(workdir: Path) -> None:
    pass


def _subprocess_prompt(tool: str) -> str:
    loc = "the workspace directory" if tool == "agy" else "the current directory"
    return (f"Run exactly this shell command in {loc} and nothing else: "
            f"for i in 1 2 3; do echo line$i > sub_$i.txt; done")


def _subprocess_check(workdir: Path, res) -> dict:
    files = sorted(p.name for p in workdir.glob("sub_*.txt"))
    return {"expected_files": ["sub_1.txt", "sub_2.txt", "sub_3.txt"],
            "actual_files": files,
            "all_present": files == ["sub_1.txt", "sub_2.txt", "sub_3.txt"]}


def _ephemeral_prompt(tool: str) -> str:
    loc = "the workspace directory" if tool == "agy" else "the current directory"
    return (f"In {loc}, create a file named ephemeral.txt containing the word temp, "
            f"then immediately delete that same file. Leave nothing behind. Then stop.")


def _ephemeral_check(workdir: Path, res) -> dict:
    still_there = (workdir / "ephemeral.txt").exists()
    # Did ai-observe's DIRECT (strace) layer see the ephemeral file at all?
    rows = res.disk_events.get("rows", [])
    direct_saw = any("ephemeral" in (r.get("path") or "") and r.get("source") == "strace"
                     for r in rows)
    snapshot_saw = any("ephemeral" in (r.get("path") or "") and r.get("source") == "snapshot"
                       for r in rows)
    return {"file_remains": still_there,
            "direct_strace_saw_ephemeral": direct_saw,
            "snapshot_saw_ephemeral": snapshot_saw,
            "note": "net effect is nothing; direct should see it, snapshot should not"}


def _modify_setup(workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "existing.txt").write_text("original line\n")


def _modify_prompt(tool: str) -> str:
    loc = "the workspace directory" if tool == "agy" else "the current directory"
    return (f"There is a file named existing.txt in {loc}. Append a new line "
            f"containing the word appended to it. Do not create any other files. Then stop.")


def _modify_check(workdir: Path, res) -> dict:
    p = workdir / "existing.txt"
    content = p.read_text() if p.exists() else ""
    rows = res.disk_events.get("rows", [])
    ops_on_file = sorted({r.get("operation") for r in rows
                          if "existing.txt" in (r.get("path") or "")})
    return {"content_has_appended": "appended" in content,
            "original_preserved": "original line" in content,
            "operations_reported_on_file": ops_on_file}


SCENARIOS = {
    "subprocess": (_subprocess_prompt, _noop_setup, _subprocess_check),
    "ephemeral": (_ephemeral_prompt, _noop_setup, _ephemeral_check),
    "modify": (_modify_prompt, _modify_setup, _modify_check),
}


def run(tools: list[str], only: list[str]) -> dict:
    matrix: dict = {}
    port = 7960
    for scen in only:
        prompt_fn, setup_fn, check_fn = SCENARIOS[scen]
        matrix[scen] = {}
        for tool in tools:
            if not tool_available(tool):
                matrix[scen][tool] = {"available": False}
                print(f"[{scen}/{tool}] NOT AVAILABLE")
                continue
            workdir = HERE / "work" / f"{scen}_{tool}"
            setup_fn(workdir)
            res = run_observed_session(
                tool, prompt_fn(tool), f"{scen}_{tool}",
                workdir=workdir, outdir=OUT, viewer_port=port,
            )
            port += 1
            de = res.disk_events
            check = check_fn(workdir, res)
            matrix[scen][tool] = {
                "ok": res.ok, "returncode": res.returncode, "duration_s": res.duration_s,
                "disk_event_total": de.get("total"),
                "disk_by_source": de.get("by_source"),
                "disk_by_operation": de.get("by_operation"),
                "viewer_events_count": res.viewer_events_count,
                "workdir_files": res.workdir_files,
                "check": check,
                "notes": res.notes,
            }
            print(f"[{scen}/{tool}] ok={res.ok} events={de.get('total')} "
                  f"{de.get('by_operation')} viewer={res.viewer_events_count} check={check}")
    return matrix


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", default="claude,agy,codex")
    ap.add_argument("--only", default="subprocess,ephemeral,modify")
    args = ap.parse_args()
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    matrix = run(tools, only)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "coverage_matrix.json").write_text(json.dumps(matrix, indent=2, default=str))
    print(f"\nwrote {OUT / 'coverage_matrix.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
