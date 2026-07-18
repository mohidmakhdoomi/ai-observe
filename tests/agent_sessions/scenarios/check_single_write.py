"""S1 single-write: the agent creates one file; all three views agree.

agent-actual (HARD): hello.txt on disk. canonical (HARD): a write lands on
hello.txt. viewer (HARD): the viewer served events. codex: the #33 marker-noise is
annotated (does not fail the scenario while #33 is open).
"""

from __future__ import annotations

from ..harness import writes_onto
from ..oracle import CheckResult, check_agent_file, check_captured, check_viewer, expect_no_marker_noise
from . import drive

_PROMPT = "Create a file named hello.txt containing exactly the word hello. Then stop."


class SingleWrite:
    name = "single_write"
    applies_to = {"claude", "agy", "codex"}

    def run(self, tool: str, ctx) -> list[CheckResult]:
        res, events = drive(tool, _PROMPT, f"sw_{tool}", ctx)
        out: list[CheckResult] = []
        out.append(check_agent_file(self.name, tool, res.workdir_files, "hello.txt"))
        out.append(check_captured(self.name, tool, writes_onto(events, "hello.txt") >= 1,
                                  f"writes_onto(hello.txt)={writes_onto(events, 'hello.txt')}"))
        if tool == "codex":
            out.append(expect_no_marker_noise(self.name, tool))
        out.append(check_viewer(self.name, tool, res.viewer_events_count >= 1,
                                f"viewer served {res.viewer_events_count} events"))
        return out


SCENARIO = SingleWrite()
