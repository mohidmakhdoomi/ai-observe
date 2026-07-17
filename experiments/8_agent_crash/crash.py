"""Experiment 8: agent CRASH (SIGKILL / nonzero mid-write) vs. graceful signal.

Exp 3/7 covered a graceful SIGINT (the observer forwards it; the agent exits
cleanly). This experiment covers the uncooperative terminations the issue calls
out -- an agent that is SIGKILLed (cannot flush, cannot be forwarded a handler)
or that exits NONZERO mid-write -- and asks what ai-observe reports.

Three cases (claude; process trees are ai-observe(py) -> strace -> agent -> shell):

  * agent_sigkill  -- externally SIGKILL the AGENT subtree (all descendants of
                      the strace pid) mid-session, leaving ai-observe(py)+strace
                      alive. strace observes its tracee die and exits; ai-observe
                      finalizes. Tests: does the observer survive an abrupt agent
                      death and still produce a coherent artifact of the writes
                      that happened before the kill?

  * observer_sigkill -- SIGKILL the ENTIRE process group (ai-observe included)
                      mid-session. The coordinator never runs finalize. Tests the
                      worst case: which artifacts survive (raw .trace? live-tailed
                      .jsonl? .meta?) and is the leftover usable / correctly
                      labeled (or absent) vs. the artifact contract.

  * nonzero_midwrite -- the agent's shell command exits nonzero partway through
                      the writes (graceful process exit, just rc!=0). Tests that a
                      nonzero-but-clean exit still finalizes an authoritative
                      artifact (contrast with the SIGKILL cases).

Oracle per case: artifacts present, sidecar parser_status + authoritative path,
viewer agreement, and captured-before-termination vs. actually-on-disk files
(no phantom captures; pre-termination writes retained modulo known bugs #32/#33).

Run: python3 crash.py
Writes data/output/crash_report.json (curated, committed) + raw artifacts.
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
from harness import AI_OBSERVE, ViewerMonitor, load_events, summarize_events  # noqa: E402

OUT = HERE / "data" / "output"
N = 6
PACED = (f"Run exactly this shell command in the current directory and nothing else: "
         f"for i in $(seq 1 {N}); do echo file$i > f$i.txt; sleep 1; done")
# Exits nonzero after writing 2 files (graceful process exit, rc!=0).
NONZERO = ("Run exactly this shell command in the current directory and nothing else: "
           "echo file1 > f1.txt; sleep 1; echo file2 > f2.txt; sleep 1; exit 3")


def _pgrep_children(pid: int) -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
        return [int(x) for x in out.stdout.split()]
    except Exception:
        return []


def _descendants(pid: int) -> list[int]:
    """All descendant pids of `pid` (excluding pid itself), deepest last."""
    seen: list[int] = []
    frontier = [pid]
    while frontier:
        nxt: list[int] = []
        for p in frontier:
            for c in _pgrep_children(p):
                if c not in seen:
                    seen.append(c)
                    nxt.append(c)
        frontier = nxt
    return seen


def _launch(session: str, workdir: Path, prompt: str) -> subprocess.Popen:
    workdir.mkdir(parents=True, exist_ok=True)
    for suf in (".jsonl", ".jsonl.partial", ".jsonl.rebuilt", ".trace", ".meta.json"):
        p = OUT / f"{session}{suf}"
        if p.exists():
            p.unlink()
    env = dict(os.environ)
    env["AI_OBSERVE_DIR"] = str(OUT)
    env["AI_OBSERVE_ROOTS"] = str(workdir)
    cmd = [str(AI_OBSERVE), "--session", session, "--",
           "claude", "-p", prompt, "--dangerously-skip-permissions"]
    return subprocess.Popen(cmd, cwd=str(workdir), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def _wait_first_file(proc, workdir: Path, timeout: float) -> float | None:
    deadline = time.time() + timeout
    t0 = time.time()
    while time.time() < deadline:
        if proc.poll() is not None:
            return None
        if any(workdir.glob("f*.txt")):
            return round(time.time() - t0, 1)
        time.sleep(0.2)
    return None


def _inspect(session: str, workdir: Path, port: int, extra: dict) -> dict:
    def art(suffix):
        p = OUT / f"{session}{suffix}"
        return {"exists": p.exists(), "size": (p.stat().st_size if p.exists() else 0)}

    artifacts = {k: art(v) for k, v in {
        "jsonl": ".jsonl", "partial": ".jsonl.partial",
        "rebuilt": ".jsonl.rebuilt", "meta": ".meta.json", "trace": ".trace"}.items()}

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

    out = {
        "session": session,
        "artifacts": artifacts,
        "meta_present": bool(meta),
        "meta_parser_status": (meta.get("parser") or {}).get("status"),
        "meta_authoritative": (meta.get("artifacts") or {}).get("authoritative_event_path"),
        "auth_event_file": (event_file.name if event_file else None),
        "auth_event_total": disk.get("total"),
        "auth_by_operation": disk.get("by_operation"),
        "captured_txt": captured, "actual_txt_on_disk": actual,
        "phantom_captures": phantom,
        "viewer": viewer,
    }
    out.update(extra)
    return out


def case_agent_sigkill(port: int) -> dict:
    session, workdir = "crash_agent", HERE / "work" / "crash_agent"
    proc = _launch(session, workdir, PACED)
    first = _wait_first_file(proc, workdir, 90.0)
    killed = []
    if first is not None and proc.poll() is None:
        time.sleep(1.2)
        # Kill the agent subtree: descendants of the strace child of the py leader.
        kids = _pgrep_children(proc.pid)            # strace pid(s)
        agent_tree: list[int] = []
        for st in kids:
            agent_tree += _descendants(st)          # agent + shell, NOT strace/py
        for p in reversed(agent_tree):              # deepest first
            try:
                os.kill(p, signal.SIGKILL); killed.append(p)
            except ProcessLookupError:
                pass
    try:
        rc = proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL); rc = proc.wait()
    return _inspect(session, workdir, port,
                    {"case": "agent_sigkill", "first_file_at_s": first,
                     "killed_pids": killed, "coordinator_returncode": rc,
                     "coordinator_survived_to_finalize": True})


def case_observer_sigkill(port: int) -> dict:
    session, workdir = "crash_observer", HERE / "work" / "crash_observer"
    proc = _launch(session, workdir, PACED)
    first = _wait_first_file(proc, workdir, 90.0)
    if first is not None and proc.poll() is None:
        time.sleep(1.2)
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # whole group incl. ai-observe(py)
    try:
        rc = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        rc = None
    return _inspect(session, workdir, port,
                    {"case": "observer_sigkill", "first_file_at_s": first,
                     "coordinator_returncode": rc,
                     "coordinator_survived_to_finalize": False})


def case_nonzero(port: int) -> dict:
    session, workdir = "crash_nonzero", HERE / "work" / "crash_nonzero"
    proc = _launch(session, workdir, NONZERO)
    rc = proc.wait(timeout=120)
    return _inspect(session, workdir, port,
                    {"case": "nonzero_midwrite", "coordinator_returncode": rc,
                     "coordinator_survived_to_finalize": True})


def main() -> int:
    argparse.ArgumentParser().parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"cases": {}}
    for fn, port in ((case_agent_sigkill, 7950), (case_observer_sigkill, 7951), (case_nonzero, 7952)):
        res = fn(port)
        report["cases"][res["case"]] = res
        print(f"=== {res['case']} ===")
        print(f"  coord_rc={res.get('coordinator_returncode')} "
              f"finalize={res.get('coordinator_survived_to_finalize')} "
              f"meta_present={res['meta_present']} parser_status={res['meta_parser_status']!r}")
        print(f"  artifacts_exist={{k:v['exists'] for k,v in res['artifacts'].items()}} "
              f"auth={res['auth_event_file']}")
        print(f"  captured={res['captured_txt']} actual={res['actual_txt_on_disk']} "
              f"phantom={res['phantom_captures']}")

    (OUT / "crash_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {OUT / 'crash_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
