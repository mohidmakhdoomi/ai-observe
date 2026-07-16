"""Experiment 1 driver: prove the harness drives all available tools.

Runs the SAME minimal write-a-file scenario under claude / agy / codex, each
wrapped by ai-observe and monitored via the viewer. Writes a combined report to
data/output/feasibility_report.json and prints a compact table.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from harness import run_observed_session, TOOLS, tool_available

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "output"

# One filename per tool so parallel-safe and easy to eyeball. agy is prompted
# with a bare filename (its --add-dir workspace is the workdir).
PROMPTS = {
    "claude": "Create a file named claude.txt containing exactly: hello from claude. Then stop.",
    "agy": "Create a file named agy.txt in the workspace directory containing exactly: hello from agy. Then stop.",
    "codex": "Create a file named codex.txt containing exactly: hello from codex. Then stop.",
}


def main() -> int:
    results = {}
    port = 7950
    for tool in ("claude", "agy", "codex"):
        if not tool_available(tool):
            results[tool] = {"available": False}
            print(f"[{tool}] NOT AVAILABLE")
            continue
        workdir = HERE / "work" / tool
        res = run_observed_session(
            tool, PROMPTS[tool], f"feas_{tool}",
            workdir=workdir, outdir=OUT, viewer_port=port,
        )
        port += 1
        results[tool] = res.to_dict()
        d = res.disk_events
        print(f"[{tool}] ok={res.ok} rc={res.returncode} dur={res.duration_s}s "
              f"disk={d.get('total')}({d.get('by_source')}) "
              f"viewer={res.viewer_events_count} files={res.workdir_files} notes={res.notes}")

    (OUT).mkdir(parents=True, exist_ok=True)
    (OUT / "feasibility_report.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {OUT / 'feasibility_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
