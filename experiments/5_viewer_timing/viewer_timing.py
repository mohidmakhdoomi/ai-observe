"""Experiment 5: viewer opened mid-session vs. before session start.

Gap deferred by Exp 2 and Exp 3: does the viewer's async jsonl tailing deliver a
COMPLETE backlog when a client attaches LATE (after the writer has already
appended events), vs. attaching before the writer starts?

Two distinct sub-questions, and they have DIFFERENT answers (see notes.md / F5):

  Q-A "before the session's .jsonl exists at all": the viewer CLI validates its
      target path at startup and EXITS ("path does not exist") if the artifact
      is absent -- so you cannot pre-launch a viewer and have it wait for the
      session to appear. Probed explicitly by `_probe_pre_existence()`.

  Q-B "mid-session, after the .jsonl exists and is still growing": does a
      late-attaching client get the full backlog (from byte 0) PLUS the events
      streamed after it attached? Tested by attaching viewers at two offsets to
      one live, growing session and comparing each to the canonical .jsonl.

Mechanism for Q-B: drive ONE paced ai-observe session (claude writes N files,
~1/sec, so the canonical .jsonl grows visibly over several seconds). Attach two
viewers in background threads:
  * "early" -- attaches the instant the .jsonl first EXISTS (earliest supported).
  * "mid"   -- attaches a fixed offset later, after several events are appended.
Plus an "after/replay" viewer on the fully finalized file.

Oracle: the canonical on-disk .jsonl is ground truth. Each viewer must serve the
SAME set of events (by op+basename+source) -- a late attach must lose nothing.

Timing note: during the live run, strace events stream as files are written;
the snapshot (net) backend appends its events in one burst at FINALIZATION,
after the agent exits. The live viewers therefore use a generous settle so their
collection spans that finalization burst rather than closing in the gap before
it (an earlier version with settle=4 closed early and spuriously "missed" the
snapshot events -- a harness artifact, not a viewer defect).

Run: python3 viewer_timing.py
Writes data/output/viewer_timing_report.json (curated, committed) + raw artifacts.
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
N = 8  # files, one per second -> a session that visibly grows over ~N seconds
TASK = (f"Run exactly this shell command in the current directory and nothing else: "
        f"for i in $(seq 1 {N}); do echo file$i > v$i.txt; sleep 1; done")


def _event_key(e: dict) -> tuple:
    """Identity of an event for set comparison: op + destination basename."""
    dest = (e.get("new_path") or e.get("path") or "")
    return (e.get("operation"), dest.rsplit("/", 1)[-1], e.get("source"))


def _keyset(events: list[dict]) -> set:
    return {_event_key(e) for e in events}


class BackgroundViewer:
    """Attach a ViewerMonitor once the jsonl exists (+ extra delay), collect, in a thread."""

    def __init__(self, label: str, jsonl: Path, port: int, extra_delay: float,
                 max_wait: float, settle: float):
        self.label = label
        self.jsonl = jsonl
        self.port = port
        self.extra_delay = extra_delay        # wait this long AFTER the file first exists
        self.max_wait = max_wait
        self.settle = settle
        self.events: list[dict] = []
        self.started_ok = False
        self.attach_wall = None
        self.events_on_disk_at_attach = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        # Wait until the artifact exists (viewer refuses to start otherwise),
        # then the label-specific extra delay.
        while not (self.jsonl.exists() and self.jsonl.stat().st_size > 0):
            time.sleep(0.1)
        time.sleep(self.extra_delay)
        self.attach_wall = round(time.time(), 2)
        self.events_on_disk_at_attach = len(load_events(self.jsonl))
        mon = ViewerMonitor(self.jsonl, port=self.port)
        if not mon.start(timeout=10.0):
            return
        self.started_ok = True
        self.events = mon.collect_events(max_wait=self.max_wait, settle=self.settle)
        mon.stop()

    def join(self):
        self._thread.join()


def _probe_pre_existence(port: int) -> dict:
    """Q-A: does the viewer start on a .jsonl that does not exist yet? (No.)"""
    missing = OUT / "_never_created.jsonl"
    if missing.exists():
        missing.unlink()
    mon = ViewerMonitor(missing, port=port)
    started = mon.start(timeout=4.0)
    mon.stop()
    return {
        "target_existed_at_launch": False,
        "viewer_started": started,
        "conclusion": ("viewer refuses to start on a non-existent artifact and "
                       "exits; pre-attaching before the session creates its "
                       ".jsonl is unsupported"),
    }


def _drive_paced(session: str, workdir: Path) -> subprocess.Popen:
    workdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["AI_OBSERVE_DIR"] = str(OUT)
    env["AI_OBSERVE_ROOTS"] = str(workdir)
    cmd = [str(AI_OBSERVE), "--session", session, "--",
           "claude", "-p", TASK, "--dangerously-skip-permissions"]
    return subprocess.Popen(cmd, cwd=str(workdir), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def run() -> dict:
    session = "vt"
    workdir = HERE / "work" / "paced"
    jsonl = OUT / f"{session}.jsonl"
    # Clean any stale jsonl so the "before" viewer genuinely starts empty.
    for suf in (".jsonl", ".jsonl.partial", ".jsonl.rebuilt", ".trace", ".meta.json"):
        p = OUT / f"{session}{suf}"
        if p.exists():
            p.unlink()

    # Q-A: probe the pre-existence guard before the run (fast, no agent).
    pre_existence = _probe_pre_existence(7913)

    # Live viewers span the whole run + the finalization snapshot burst. The
    # snapshot events land in one burst after the agent exits, so settle must be
    # larger than the agent-shutdown gap between the last strace event and that
    # burst -- otherwise collection closes early and spuriously drops them.
    live_max_wait = 90.0
    live_settle = 9.0

    # "early": attach the instant the .jsonl first exists (earliest supported).
    early = BackgroundViewer("early", jsonl, 7910, extra_delay=0.0,
                             max_wait=live_max_wait, settle=live_settle)
    # "mid": attach ~6s after the file appears, after several events are written.
    mid = BackgroundViewer("mid", jsonl, 7911, extra_delay=6.0,
                           max_wait=live_max_wait, settle=live_settle)

    early.start()
    mid.start()
    t0 = time.time()
    proc = _drive_paced(session, workdir)
    proc.wait(timeout=180)
    run_duration = round(time.time() - t0, 1)

    early.join()
    mid.join()

    # After finalization: canonical ground truth + a pure-replay viewer.
    canonical = load_events(jsonl)
    canon_keys = _keyset(canonical)

    after = ViewerMonitor(jsonl, port=7912)
    after_events = []
    if after.start(timeout=10.0):
        after_events = after.collect_events(max_wait=10.0, settle=2.0)
        after.stop()

    def _cmp(viewer_events, attach_wall, on_disk_at_attach=None):
        vk = _keyset(viewer_events)
        missing = canon_keys - vk
        extra = vk - canon_keys
        return {
            "attach_wall_offset_s": (round(attach_wall - t0, 1) if attach_wall else None),
            "events_on_disk_at_attach": on_disk_at_attach,
            "viewer_event_count": len(viewer_events),
            "canonical_event_count": len(canonical),
            "distinct_canonical_keys": len(canon_keys),
            "missing_from_viewer": sorted(str(k) for k in missing),
            "extra_in_viewer": sorted(str(k) for k in extra),
            "complete_backlog": not missing,
        }

    disk = summarize_events(canonical, workdir=workdir)
    report = {
        "task": TASK,
        "run_duration_s": run_duration,
        "canonical_total": len(canonical),
        "canonical_by_operation": disk.get("by_operation"),
        "canonical_by_source": disk.get("by_source"),
        "q_a_pre_existence": pre_existence,
        "q_b_attach_timings": {
            "early": _cmp(early.events, early.attach_wall, early.events_on_disk_at_attach),
            "mid": _cmp(mid.events, mid.attach_wall, mid.events_on_disk_at_attach),
            "after_replay": _cmp(after_events, time.time()),
        },
        "actual_files": sorted(p.name for p in workdir.glob("v*.txt")),
    }
    return report


def main() -> int:
    argparse.ArgumentParser().parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report = run()
    pe = report["q_a_pre_existence"]
    print(f"Q-A pre-existence: viewer_started_on_missing_file={pe['viewer_started']} "
          f"(expect False = refuses)")
    at = report["q_b_attach_timings"]
    print(f"Q-B run_duration={report['run_duration_s']}s canonical={report['canonical_total']} "
          f"{report['canonical_by_operation']} distinct_keys={at['after_replay']['distinct_canonical_keys']}")
    for label in ("early", "mid", "after_replay"):
        c = at[label]
        print(f"  [{label}] attach@{c['attach_wall_offset_s']}s on_disk_at_attach={c['events_on_disk_at_attach']} "
              f"viewer={c['viewer_event_count']} complete_backlog={c['complete_backlog']} "
              f"missing={len(c['missing_from_viewer'])}")
    (OUT / "viewer_timing_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {OUT / 'viewer_timing_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
