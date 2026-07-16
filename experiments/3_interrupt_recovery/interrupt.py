"""Experiment 3: interrupt / recovery — the error-path half of issue #31.

Drives a real agent through a *paced* task (a shell loop that writes one file
per second) under ai-observe, then sends SIGINT to the ai-observe process group
mid-session. Inspects ai-observe's degraded-session contract:
  * which artifacts exist afterward (.jsonl / .jsonl.partial / .jsonl.rebuilt / .meta.json)
  * what the sidecar + viewer report as the *authoritative* artifact and parser_status
  * whether the partial filesystem changes made before the interrupt were captured
  * the actual files left on disk vs. what ai-observe reported

Also runs a clean (non-interrupted) control for comparison.

Run: python3 interrupt.py [--kill-after 3.5]
Writes data/output/interrupt_report.json (curated, committed) + raw artifacts.
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
from harness import AI_OBSERVE, SRC, ViewerMonitor, load_events, summarize_events  # noqa: E402

OUT = HERE / "data" / "output"

# Paced task: one file per second so an interrupt reliably lands mid-session.
# Bare shell command keeps the agent's own behavior out of the timing.
N = 6
TASK = (f"Run exactly this shell command in the current directory and nothing else: "
        f"for i in $(seq 1 {N}); do echo file$i > f$i.txt; sleep 1; done")


def _drive(session: str, workdir: Path, kill_after: float | None) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["AI_OBSERVE_DIR"] = str(OUT)
    env["AI_OBSERVE_ROOTS"] = str(workdir)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [str(AI_OBSERVE), "--session", session, "--",
           "claude", "-p", TASK, "--dangerously-skip-permissions"]

    t0 = time.time()
    # New process group so we can signal the whole tree the way a terminal Ctrl-C would.
    proc = subprocess.Popen(cmd, cwd=str(workdir), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    interrupted = False
    if kill_after is not None:
        time.sleep(kill_after)
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            interrupted = True
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()
    duration = round(time.time() - t0, 1)

    # Inspect artifacts.
    def art(suffix):
        p = OUT / f"{session}{suffix}"
        return {"exists": p.exists(), "size": (p.stat().st_size if p.exists() else 0)}

    artifacts = {
        "jsonl": art(".jsonl"),
        "partial": art(".jsonl.partial"),
        "rebuilt": art(".jsonl.rebuilt"),
        "meta": art(".meta.json"),
    }

    meta = {}
    meta_p = OUT / f"{session}.meta.json"
    if meta_p.exists():
        try:
            meta = json.loads(meta_p.read_text())
        except Exception:
            pass

    # Canonical events on disk (whichever event artifact exists).
    event_file = None
    for suffix in (".jsonl", ".jsonl.rebuilt", ".jsonl.partial"):
        p = OUT / f"{session}{suffix}"
        if p.exists() and p.stat().st_size > 0:
            event_file = p
            break
    disk = summarize_events(load_events(event_file), workdir=workdir) if event_file else {"total": 0}
    captured_files = sorted({(r.get("path") or "").split("/")[-1]
                             for r in disk.get("rows", [])
                             if (r.get("path") or "").endswith(".txt")})

    # What the viewer reports for the finalized (possibly degraded) session.
    viewer = {}
    if event_file:
        mon = ViewerMonitor(event_file, port=7980)
        if mon.start():
            mon.collect_events()
            si = mon.session_info or {}
            viewer = {
                "parser_status": si.get("parser_status"),
                "authoritative_artifact": si.get("authoritative_artifact"),
                "warnings_count": si.get("warnings_count"),
            }
            mon.stop()

    actual_files = sorted(p.name for p in workdir.glob("f*.txt"))

    return {
        "session": session,
        "interrupted": interrupted,
        "kill_after": kill_after,
        "returncode": proc.returncode,
        "duration_s": duration,
        "artifacts": artifacts,
        "meta_authoritative_role": (meta.get("artifacts", {}) or {}),
        "meta_warnings": meta.get("warnings"),
        "disk_event_total": disk.get("total"),
        "disk_by_source": disk.get("by_source"),
        "disk_by_operation": disk.get("by_operation"),
        "captured_txt_files": captured_files,
        "actual_txt_files_on_disk": actual_files,
        "viewer": viewer,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    # claude -p spends ~11s in LLM/startup before it runs the shell loop, then
    # writes one file/sec. So: early (~3.5s) lands during startup (no writes
    # yet); mid (~13s) lands after 1-2 files are written.
    ap.add_argument("--early-kill", type=float, default=3.5)
    ap.add_argument("--mid-kill", type=float, default=13.0)
    args = ap.parse_args()

    report = {}
    cases = [
        ("early_interrupt", "early", args.early_kill),
        ("mid_interrupt", "mid", args.mid_kill),
        ("clean", "clean", None),
    ]
    for key, session, kill_after in cases:
        print(f"=== {key} run (kill_after={kill_after}) ===")
        report[key] = _drive(session, HERE / "work" / session, kill_after)
        d = report[key]
        print(f"  interrupted={d['interrupted']} rc={d['returncode']} dur={d['duration_s']}s "
              f"artifacts={{k:v['exists'] for k,v in d['artifacts'].items()}} "
              f"parser={d['viewer'].get('parser_status')} auth={d['viewer'].get('authoritative_artifact')}")
        print(f"  captured_txt={d['captured_txt_files']} actual_on_disk={d['actual_txt_files_on_disk']}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "interrupt_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {OUT / 'interrupt_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
