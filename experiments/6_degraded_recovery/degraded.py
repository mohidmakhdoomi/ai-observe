"""Experiment 6: forced degraded recovery (.partial / .jsonl.rebuilt).

Exp 3 proved a *clean* SIGINT never produces .partial/.rebuilt -- those degraded
artifacts belong to the parse-failure / live-timeout paths, which round 1 left
untested. Here we force each path deliberately and verify artifact AUTHORITY
behaves per the arch.md Artifact contract: the sidecar (.meta.json) records
parser status + the authoritative event path, and the viewer reports the same.

Forcing levers (all are supported ai-observe env knobs; no source changes):

  * LIVE-TIMEOUT -> .jsonl.rebuilt   (parser_status "live_timeout_rebuilt")
      AI_OBSERVE_LIVE_JOIN_TIMEOUT=0.1  (min) so the main thread waits only 0.1s
      for the live tailer to drain, AND AI_OBSERVE_LIVE_POLL_MS=2000 (max) so the
      tailer sleeps ~2s between reads and is virtually never drained within 0.1s
      of stop. The main thread then times out, rebuilds the canonical .jsonl
      post-hoc from the full .trace, and marks .jsonl.rebuilt authoritative.

  * PARSE-FAILURE -> .jsonl.partial  (parser_status "parser_failure_partial")
      AI_OBSERVE_TEST_FAIL_AFTER=N makes the parser raise ParserFailure after N
      events (the in-tree deterministic-failure hook). ai-observe writes the
      partial direct events to .jsonl.partial and reports NO authoritative event
      artifact (authoritative_artifact=None). This is the same terminal state a
      genuinely CORRUPT .trace reaches (the issue's other suggested trigger) --
      a mid-stream unparseable line -> ParserFailure -> partial.

  * STRICT variant: PARSE-FAILURE + AI_OBSERVE_STRICT_PARSE=1 -> ai-observe
      returns exit 1 (opt-in fail-loud) even though the agent itself exited 0.

  * CONTROL: clean run -> .jsonl authoritative, parser_status "ok".

For each case we record: artifact existence/size, meta.json parser_status +
artifact roles + warnings, what the VIEWER reports as authoritative + status,
and (for cases with an authoritative event artifact) captured files vs. the
files actually on disk -- so a degraded-but-authoritative artifact is still
checked for fidelity.

Run: python3 degraded.py
Writes data/output/degraded_report.json (curated, committed) + raw artifacts.
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
from harness import AI_OBSERVE, ViewerMonitor, load_events, summarize_events  # noqa: E402

OUT = HERE / "data" / "output"

# A paced multi-file task: enough events that TEST_FAIL_AFTER lands mid-stream
# and the live tailer has a real backlog to (fail to) drain.
N = 5
TASK = (f"Run exactly this shell command in the current directory and nothing else: "
        f"for i in $(seq 1 {N}); do echo file$i > d$i.txt; sleep 0.6; done")


def _run_case(name: str, session: str, extra_env: dict, port: int) -> dict:
    workdir = HERE / "work" / session
    workdir.mkdir(parents=True, exist_ok=True)
    for suf in (".jsonl", ".jsonl.partial", ".jsonl.rebuilt", ".trace", ".meta.json"):
        p = OUT / f"{session}{suf}"
        if p.exists():
            p.unlink()

    env = dict(os.environ)
    env["AI_OBSERVE_DIR"] = str(OUT)
    env["AI_OBSERVE_ROOTS"] = str(workdir)
    env.update(extra_env)
    cmd = [str(AI_OBSERVE), "--session", session, "--",
           "claude", "-p", TASK, "--dangerously-skip-permissions"]

    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(workdir), env=env, timeout=180,
                          capture_output=True, text=True)
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
    meta_parser = (meta.get("parser") or {})
    meta_artifacts = (meta.get("artifacts") or {})
    # authoritative event artifact per the sidecar
    auth_role = None
    for role_key, entry in meta_artifacts.items():
        if isinstance(entry, dict) and entry.get("role", "").startswith("authoritative"):
            auth_role = role_key

    # Pick the authoritative event artifact to inspect (rebuilt > jsonl > partial).
    event_file = None
    for suffix in (".jsonl.rebuilt", ".jsonl", ".jsonl.partial"):
        p = OUT / f"{session}{suffix}"
        if p.exists() and p.stat().st_size > 0:
            event_file = p
            break
    disk = summarize_events(load_events(event_file), workdir=workdir) if event_file else {"total": 0}
    captured = sorted({(r.get("path") or "").rsplit("/", 1)[-1]
                       for r in disk.get("rows", [])
                       if (r.get("path") or "").endswith(".txt")})
    actual = sorted(p.name for p in workdir.glob("d*.txt"))

    # What the viewer reports for the finalized (possibly degraded) session.
    viewer = {}
    if event_file:
        mon = ViewerMonitor(event_file, port=port)
        if mon.start():
            mon.collect_events(max_wait=6, settle=1.5)
            si = mon.session_info or {}
            viewer = {
                "parser_status": si.get("parser_status"),
                "authoritative_artifact": si.get("authoritative_artifact"),
                "warnings_count": si.get("warnings_count"),
            }
            mon.stop()

    return {
        "case": name, "session": session, "extra_env": extra_env,
        "agent_returncode": proc.returncode, "duration_s": duration,
        "artifacts": artifacts,
        "meta_parser_status": meta_parser.get("status"),
        "meta_authoritative_role_key": auth_role,
        "meta_artifact_roles": {k: (v.get("role") if isinstance(v, dict) else v)
                                for k, v in meta_artifacts.items()},
        "meta_warnings": meta.get("warnings"),
        "authoritative_event_file": (event_file.name if event_file else None),
        "auth_event_total": disk.get("total"),
        "auth_by_operation": disk.get("by_operation"),
        "captured_txt": captured,
        "actual_txt_on_disk": actual,
        "viewer": viewer,
    }


CASES = [
    ("control_clean", "deg_clean", {}),
    ("live_timeout_rebuilt", "deg_timeout",
     {"AI_OBSERVE_LIVE_JOIN_TIMEOUT": "0.1", "AI_OBSERVE_LIVE_POLL_MS": "2000"}),
    ("parse_failure_partial", "deg_partial",
     {"AI_OBSERVE_TEST_FAIL_AFTER": "2"}),
    ("parse_failure_strict", "deg_strict",
     {"AI_OBSERVE_TEST_FAIL_AFTER": "2", "AI_OBSERVE_STRICT_PARSE": "1"}),
]


def main() -> int:
    argparse.ArgumentParser().parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"task": TASK, "cases": {}}
    port = 7920
    for name, session, extra in CASES:
        print(f"=== {name} (env={extra}) ===")
        res = _run_case(name, session, extra, port)
        port += 1
        report["cases"][name] = res
        print(f"  agent_rc={res['agent_returncode']} parser_status={res['meta_parser_status']!r} "
              f"auth={res['authoritative_event_file']} "
              f"exists={{k:v['exists'] for k,v in res['artifacts'].items()}}")
        print(f"  viewer parser_status={res['viewer'].get('parser_status')!r} "
              f"auth_artifact={res['viewer'].get('authoritative_artifact')!r} "
              f"warnings={res['viewer'].get('warnings_count')}")
        print(f"  captured_txt={res['captured_txt']} actual_on_disk={res['actual_txt_on_disk']}")

    (OUT / "degraded_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {OUT / 'degraded_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
