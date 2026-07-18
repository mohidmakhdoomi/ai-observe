"""S6 long-running timeline (Exp 9): events stream to the viewer incrementally.

Drives a long observed claude session (a shell loop writing files with sleeps) and
samples the viewer-visible backlog on a cadence. Asserts TIMELINESS — the visible
count strictly increases across >=3 distinct ticks WHILE the run proceeds — and
final completeness (the viewer eventually served all canonical events).

claude-only (per the plan): the probe needs a genuinely long, paced run.
"""

from __future__ import annotations

from ..oracle import CheckResult, check_viewer, hard_check
from ..probes import sample_timeline
from . import session_dirs


class Timeline:
    name = "timeline"
    applies_to = {"claude"}

    def run(self, tool: str, ctx) -> list[CheckResult]:
        workdir, outdir = session_dirs(ctx, f"tl_{tool}")
        # A long enough run to sample several increasing ticks; bounded by ctx.timeout.
        report = sample_timeline(tool, f"tl_{tool}", workdir, outdir,
                                 n=12, interval=1.6, timeout=ctx.timeout)
        out: list[CheckResult] = []
        out.append(hard_check(
            self.name, tool, "viewer", report["incremental_confirmed"],
            f"distinct_increasing_ticks={report['distinct_increasing_ticks_during_run']} "
            f"max_visible_during_run={report['max_visible_during_run']} "
            f"(need >=3 increasing while running)"))
        out.append(check_viewer(
            self.name, tool, report["final_complete"],
            f"final viewer_visible={report['final_visible']} canonical={report['canonical_total']}"))
        return out


SCENARIO = Timeline()
