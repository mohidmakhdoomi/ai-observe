"""Round-2 timeline-sampling probe (Exp 9), graduated (Spec 38, Phase 4).

Proves TIMELINESS: during a long-running observed command, events become visible in
the viewer PROGRESSIVELY as the command runs, not in a single dump at the end (the
property that makes the viewer useful for watching a live session). Distinct from
viewer completeness (Exp 5): here we attach one viewer as soon as the `.jsonl`
exists, then SAMPLE the viewer-visible backlog on a fixed cadence while the command
runs, and confirm the count strictly increases across several ticks mid-run.

Uses a non-blocking `Popen` launch (so we can sample while the session runs) and the
graduated in-process ephemeral-port `ViewerMonitor`.

Stdlib only.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .harness import TOOLS, ViewerMonitor, load_events, resolve_ai_observe, summarize_events


def sample_timeline(tool: str, session: str, workdir: Path, outdir: Path, *,
                    n: int = 12, interval: float = 1.6, sample_period: float = 2.0,
                    timeout: float = 240.0) -> dict:
    """Drive a long observed session and sample viewer visibility on a cadence.

    Returns a report dict with `incremental_confirmed` (>=3 distinct increasing
    viewer-visible counts seen WHILE the run proceeds) and `final_complete`
    (viewer eventually served all canonical events).
    """
    workdir = Path(workdir).resolve()
    outdir = Path(outdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    jsonl = outdir / f"{session}.jsonl"

    task = (f"Run exactly this shell command in the current directory and nothing "
            f"else: for i in $(seq 1 {n}); do echo file$i > w$i.txt; sleep {interval}; done")
    cmd = [resolve_ai_observe(), "--session", session, "--"] + TOOLS[tool](task, workdir)
    env = dict(os.environ)
    env["AI_OBSERVE_DIR"] = str(outdir)
    env["AI_OBSERVE_ROOTS"] = str(workdir)

    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=str(workdir), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)

    # Wait until the .jsonl exists (viewer refuses to start otherwise — F5), then
    # attach one in-process viewer for the whole run.
    while not (jsonl.exists() and jsonl.stat().st_size > 0):
        if proc.poll() is not None:
            break
        if time.time() - t0 > timeout:
            break
        time.sleep(0.1)
    mon = ViewerMonitor(jsonl)
    started = mon.start(timeout=10.0)

    timeline = []
    while proc.poll() is None:
        if time.time() - t0 > timeout:
            break
        tick = round(time.time() - t0, 1)
        visible = len(mon.collect_events(max_wait=1.8, settle=0.5)) if started else 0
        timeline.append({"t": tick, "viewer_visible": visible,
                         "disk_lines": len(load_events(jsonl)),
                         "files_written": len(list(workdir.glob("w*.txt")))})
        elapsed = (time.time() - t0) - tick
        if elapsed < sample_period:
            time.sleep(sample_period - elapsed)

    try:
        proc.wait(timeout=30)
    except Exception:
        proc.kill()
    returncode = proc.returncode
    run_duration = round(time.time() - t0, 1)

    final_visible = len(mon.collect_events(max_wait=8, settle=2.0)) if started else 0
    mon.stop()
    canonical = load_events(jsonl)

    seen = [row["viewer_visible"] for row in timeline]
    distinct_increasing, prev = 0, -1
    for v in seen:
        if v > prev:
            distinct_increasing += 1
            prev = v
    max_during_run = max(seen) if seen else 0

    return {
        "tool": tool,
        "returncode": returncode,
        "run_duration_s": run_duration,
        "viewer_started": started,
        "n_samples": len(timeline),
        "timeline": timeline,
        "distinct_increasing_ticks_during_run": distinct_increasing,
        "max_visible_during_run": max_during_run,
        "final_visible": final_visible,
        "canonical_total": len(canonical),
        "canonical_by_operation": summarize_events(canonical, workdir=workdir).get("by_operation"),
        "incremental_confirmed": distinct_increasing >= 3 and max_during_run > 0,
        # Completeness is EQUALITY: the viewer served exactly the canonical events.
        # An over-serving viewer (final_visible > canonical) must NOT pass.
        "final_complete": final_visible == len(canonical) and len(canonical) > 0,
        "actual_files": sorted(p.name for p in workdir.glob("w*.txt")),
    }
