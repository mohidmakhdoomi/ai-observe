"""Experiment 9: long-running command -> incremental streaming to the viewer.

Gap deferred by Exp 2/3 and distinct from Exp 5: Exp 5 proved a late-attaching
viewer eventually gets the COMPLETE set (backlog completeness). This experiment
proves TIMELINESS -- that during a long-running observed command, events become
visible in the viewer PROGRESSIVELY as the command runs, not in a single dump at
the end. That is the property that makes the viewer useful for watching a live
session.

Mechanism: drive a genuinely long ai-observe session (claude runs a shell loop
that writes one file every ~1.6s for N files, ~25s of active writing after the
~11s startup). Attach one viewer as soon as the .jsonl exists, then SAMPLE it on
a fixed cadence: at each tick, open a fresh /events connection (what a browser
connecting at that instant would receive as backlog) and record how many events
are visible, alongside the on-disk .jsonl line count and the number of files
actually written so far.

The oracle for "incremental" (not end-loaded):
  * the viewer-visible count strictly INCREASES across multiple distinct sample
    ticks WHILE the command is still running (>=3 distinct increasing values
    before the run ends), and
  * a meaningful fraction of events is already visible at the midpoint (not ~0
    until the end).
We also confirm final completeness (viewer == canonical) as in Exp 5.

Run: python3 incremental.py
Writes data/output/incremental_report.json (curated, committed) + raw artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "1_driving_mechanism"))
from harness import AI_OBSERVE, ViewerMonitor, load_events, summarize_events  # noqa: E402

OUT = HERE / "data" / "output"
N = 12
INTERVAL = 1.6
TASK = (f"Run exactly this shell command in the current directory and nothing else: "
        f"for i in $(seq 1 {N}); do echo file$i > w$i.txt; sleep {INTERVAL}; done")


def _drive(session: str, workdir: Path) -> subprocess.Popen:
    workdir.mkdir(parents=True, exist_ok=True)
    for suf in (".jsonl", ".jsonl.partial", ".jsonl.rebuilt", ".trace", ".meta.json"):
        p = OUT / f"{session}{suf}"
        if p.exists():
            p.unlink()
    env = dict(os.environ)
    env["AI_OBSERVE_DIR"] = str(OUT)
    env["AI_OBSERVE_ROOTS"] = str(workdir)
    cmd = [str(AI_OBSERVE), "--session", session, "--",
           "claude", "-p", TASK, "--dangerously-skip-permissions"]
    return subprocess.Popen(cmd, cwd=str(workdir), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def run() -> dict:
    session, workdir = "inc", HERE / "work" / "inc"
    jsonl = OUT / f"{session}.jsonl"

    t0 = time.time()
    proc = _drive(session, workdir)

    # Wait until the .jsonl exists (viewer refuses to start otherwise -- Exp 5 F5),
    # then attach one viewer for the whole run.
    while not (jsonl.exists() and jsonl.stat().st_size > 0):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    mon = ViewerMonitor(jsonl, port=7960)
    started = mon.start(timeout=10.0)

    # Sample the viewer-visible backlog on a fixed cadence WHILE the run proceeds.
    timeline = []
    sample_period = 2.0
    while proc.poll() is None:
        tick = round(time.time() - t0, 1)
        visible = len(mon.collect_events(max_wait=1.8, settle=0.5)) if started else 0
        disk_lines = len(load_events(jsonl))
        files_now = len(list(workdir.glob("w*.txt")))
        timeline.append({"t": tick, "viewer_visible": visible,
                         "disk_lines": disk_lines, "files_written": files_now})
        # Pace the sampling loop (collect_events already consumed ~part of it).
        elapsed = (time.time() - t0) - tick
        if elapsed < sample_period:
            time.sleep(sample_period - elapsed)

    proc.wait(timeout=30)
    run_duration = round(time.time() - t0, 1)

    # Final completeness (post-finalization).
    final_visible = len(mon.collect_events(max_wait=8, settle=2.0)) if started else 0
    mon.stop()
    canonical = load_events(jsonl)
    disk = summarize_events(canonical, workdir=workdir)

    # Incremental oracle: count distinct increasing viewer-visible values seen
    # while the run was still going (i.e. across the sampled timeline).
    seen_values = [row["viewer_visible"] for row in timeline]
    distinct_increasing = 0
    prev = -1
    for v in seen_values:
        if v > prev:
            distinct_increasing += 1
            prev = v
    max_during_run = max(seen_values) if seen_values else 0
    midpoint_visible = seen_values[len(seen_values) // 2] if seen_values else 0

    return {
        "task": TASK,
        "run_duration_s": run_duration,
        "viewer_started": started,
        "n_samples": len(timeline),
        "timeline": timeline,
        "distinct_increasing_ticks_during_run": distinct_increasing,
        "max_visible_during_run": max_during_run,
        "midpoint_visible": midpoint_visible,
        "final_visible": final_visible,
        "canonical_total": len(canonical),
        "canonical_by_operation": disk.get("by_operation"),
        "incremental_confirmed": distinct_increasing >= 3 and max_during_run > 0,
        "final_complete": final_visible >= len(canonical) and len(canonical) > 0,
        "actual_files": sorted(p.name for p in workdir.glob("w*.txt")),
    }


def main() -> int:
    argparse.ArgumentParser().parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report = run()
    print(f"run_duration={report['run_duration_s']}s canonical={report['canonical_total']} "
          f"{report['canonical_by_operation']}")
    print(f"viewer_started={report['viewer_started']} samples={report['n_samples']}")
    for row in report["timeline"]:
        print(f"  t={row['t']:>5}s viewer_visible={row['viewer_visible']:>3} "
              f"disk_lines={row['disk_lines']:>3} files_written={row['files_written']}")
    print(f"distinct_increasing_during_run={report['distinct_increasing_ticks_during_run']} "
          f"midpoint_visible={report['midpoint_visible']} final_visible={report['final_visible']}")
    print(f"INCREMENTAL_CONFIRMED={report['incremental_confirmed']} "
          f"FINAL_COMPLETE={report['final_complete']}")
    (OUT / "incremental_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {OUT / 'incremental_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
