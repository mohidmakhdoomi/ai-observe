"""Experiment 7: cross-tool mid-session interrupt (agy, codex).

Exp 3 established the interrupt/recovery contract for claude only (F4: a clean
mid-session SIGINT finalizes an authoritative .jsonl capturing exactly the files
written before the interrupt, with no phantom entries, and does NOT trigger the
degraded .partial/.rebuilt paths). This experiment repeats that scenario for agy
and codex to check the contract holds across tools with different process trees
and (for codex) a mount-namespace sandbox.

Robustness over Exp 3's fixed-offset kill: tools have very different startup
latencies, so a fixed `kill_after` that lands mid-session for claude may land in
startup for agy/codex. Here we poll the workdir and send SIGINT a short grace
AFTER the FIRST paced file appears -- guaranteeing the interrupt lands with >=1
write already captured and more still pending, for every tool.

For each tool we record: whether the interrupt landed mid-session, which
artifacts exist afterward, the sidecar/viewer authoritative artifact + parser
status, and captured-before-interrupt vs. actually-on-disk files (the fidelity
oracle: no phantom captures, no missed pre-interrupt writes beyond known bugs).

Run: python3 interrupt_xtool.py [--tools agy,codex,claude]
Writes data/output/xtool_interrupt_report.json (curated, committed) + raw artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "1_driving_mechanism"))
from harness import (  # noqa: E402
    AI_OBSERVE, TOOLS, ViewerMonitor, load_events, summarize_events, tool_available,
)

OUT = HERE / "data" / "output"

N = 6
# Bare shell loop keeps the agent's own behavior out of the timing; one file/sec
# so an interrupt after the first file reliably lands mid-session with more pending.
def _task(tool: str) -> str:
    loc = "the workspace directory" if tool == "agy" else "the current directory"
    return (f"Run exactly this shell command in {loc} and nothing else: "
            f"for i in $(seq 1 {N}); do echo file$i > f$i.txt; sleep 1; done")


def _drive_interrupt(tool: str, session: str, workdir: Path, port: int,
                     grace_after_first: float, max_startup: float) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    for suf in (".jsonl", ".jsonl.partial", ".jsonl.rebuilt", ".trace", ".meta.json"):
        p = OUT / f"{session}{suf}"
        if p.exists():
            p.unlink()

    env = dict(os.environ)
    env["AI_OBSERVE_DIR"] = str(OUT)
    env["AI_OBSERVE_ROOTS"] = str(workdir)
    cmd = [str(AI_OBSERVE), "--session", session, "--"] + TOOLS[tool](_task(tool), workdir)

    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=str(workdir), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)

    # Wait for the first paced file to appear (mid-session marker), then SIGINT.
    interrupted = False
    first_file_at = None
    deadline = time.time() + max_startup
    while time.time() < deadline:
        if proc.poll() is not None:
            break  # agent exited before writing anything (startup failure/too fast)
        if any(workdir.glob("f*.txt")):
            first_file_at = round(time.time() - t0, 1)
            break
        time.sleep(0.2)

    if first_file_at is not None and proc.poll() is None:
        time.sleep(grace_after_first)
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            interrupted = True

    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()
    duration = round(time.time() - t0, 1)

    def art(suffix):
        p = OUT / f"{session}{suffix}"
        return {"exists": p.exists(), "size": (p.stat().st_size if p.exists() else 0)}

    artifacts = {k: art(v) for k, v in {
        "jsonl": ".jsonl", "partial": ".jsonl.partial",
        "rebuilt": ".jsonl.rebuilt", "meta": ".meta.json"}.items()}

    meta = {}
    meta_p = OUT / f"{session}.meta.json"
    if meta_p.exists():
        try:
            meta = json.loads(meta_p.read_text())
        except Exception:
            pass

    event_file = None
    for suffix in (".jsonl", ".jsonl.rebuilt", ".jsonl.partial"):
        p = OUT / f"{session}{suffix}"
        if p.exists() and p.stat().st_size > 0:
            event_file = p
            break
    disk = summarize_events(load_events(event_file), workdir=workdir) if event_file else {"total": 0}
    captured = sorted({(r.get("path") or "").rsplit("/", 1)[-1]
                       for r in disk.get("rows", [])
                       if (r.get("path") or "").endswith(".txt")})
    actual = sorted(p.name for p in workdir.glob("f*.txt"))
    # No phantom captures: every captured file must actually exist on disk.
    phantom = sorted(set(captured) - set(actual))

    viewer = {}
    if event_file:
        mon = ViewerMonitor(event_file, port=port)
        if mon.start():
            mon.collect_events(max_wait=6, settle=1.5)
            si = mon.session_info or {}
            viewer = {"parser_status": si.get("parser_status"),
                      "authoritative_artifact": si.get("authoritative_artifact"),
                      "warnings_count": si.get("warnings_count")}
            mon.stop()

    return {
        "tool": tool, "session": session,
        "first_file_at_s": first_file_at, "interrupted_mid_session": interrupted,
        "returncode": proc.returncode, "duration_s": duration,
        "artifacts": artifacts,
        "meta_parser_status": (meta.get("parser") or {}).get("status"),
        "meta_authoritative": (meta.get("artifacts") or {}).get("authoritative_event_path"),
        "degraded_artifact_produced": artifacts["partial"]["exists"] or artifacts["rebuilt"]["exists"],
        "auth_event_total": disk.get("total"),
        "auth_by_operation": disk.get("by_operation"),
        "captured_txt": captured, "actual_txt_on_disk": actual,
        "phantom_captures": phantom,
        "viewer": viewer,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", default="agy,codex,claude")
    ap.add_argument("--grace", type=float, default=1.5, help="seconds after first file before SIGINT")
    args = ap.parse_args()
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]

    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {"task_files": N, "results": {}}
    port = 7940
    for tool in tools:
        if not tool_available(tool):
            report["results"][tool] = {"available": False}
            print(f"[{tool}] NOT AVAILABLE")
            continue
        print(f"=== {tool} mid-session interrupt ===")
        res = _drive_interrupt(tool, f"xint_{tool}", HERE / "work" / tool, port,
                               grace_after_first=args.grace, max_startup=90.0)
        port += 1
        report["results"][tool] = res
        print(f"  first_file@{res['first_file_at_s']}s interrupted={res['interrupted_mid_session']} "
              f"rc={res['returncode']} parser_status={res['meta_parser_status']!r} "
              f"degraded_artifact={res['degraded_artifact_produced']}")
        print(f"  captured={res['captured_txt']} actual={res['actual_txt_on_disk']} "
              f"phantom={res['phantom_captures']}")

    (OUT / "xtool_interrupt_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {OUT / 'xtool_interrupt_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
