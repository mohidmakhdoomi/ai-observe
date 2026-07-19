"""S4 subprocess: the agent writes three files via a grandchild shell.

Proves process-tree scoping — ai-observe's descendant tracing captures writes from
a grandchild shell, not just the direct agent process. agent-actual (HARD): all
three files on disk. canonical (HARD): all three captured. codex: #33 marker-noise
annotated.
"""

from __future__ import annotations

from ..harness import writes_onto
from ..oracle import CheckResult, check_agent_file, check_captured, check_viewer, expect_no_marker_noise
from . import drive, viewer_served_all

_FILES = ("s1.txt", "s2.txt", "s3.txt")
_PROMPT = ("Run exactly this shell command in the current directory and nothing else: "
           "for f in s1 s2 s3; do echo $f > $f.txt; done")


class Subprocess:
    name = "subprocess"
    applies_to = {"claude", "agy", "codex"}

    def run(self, tool: str, ctx) -> list[CheckResult]:
        res, events = drive(tool, _PROMPT, f"sub_{tool}", ctx)
        out: list[CheckResult] = []
        for fname in _FILES:
            out.append(check_agent_file(self.name, tool, res.workdir_files, fname))
            out.append(check_captured(self.name, tool, writes_onto(events, fname) >= 1,
                                      f"writes_onto({fname})={writes_onto(events, fname)}"))
        if tool == "codex":
            out.append(expect_no_marker_noise(self.name, tool))
        ok, detail = viewer_served_all(res)
        out.append(check_viewer(self.name, tool, ok, detail))
        return out


SCENARIO = Subprocess()
