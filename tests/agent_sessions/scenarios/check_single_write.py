"""S1 single-write: the agent creates one file; all three views agree.

agent-actual (HARD): hello.txt on disk AND its content is correct. canonical
(HARD): a write lands on hello.txt. viewer (HARD): the viewer served all canonical
events. codex: the #33 marker-noise is annotated (does not fail while #33 is open).
"""

from __future__ import annotations

from ..harness import writes_onto
from ..oracle import (
    CheckResult,
    check_agent_file,
    check_captured,
    check_viewer,
    expect_no_marker_noise,
    hard_check,
)
from . import drive, session_dirs, viewer_served_all

_PROMPT = "Create a file named hello.txt containing exactly the word hello. Then stop."


class SingleWrite:
    name = "single_write"
    applies_to = {"claude", "agy", "codex"}

    def run(self, tool: str, ctx) -> list[CheckResult]:
        workdir, _ = session_dirs(ctx, f"sw_{tool}")
        res, events = drive(tool, _PROMPT, f"sw_{tool}", ctx)
        out: list[CheckResult] = []
        # agent-actual: file present AND content correct (HARD).
        out.append(check_agent_file(self.name, tool, res.workdir_files, "hello.txt"))
        target = workdir / "hello.txt"
        content = target.read_text() if target.exists() else ""
        # Exact-output enforcement (allowing only trailing-newline normalization):
        # the prompt asks for a file containing exactly the word "hello".
        out.append(hard_check(self.name, tool, "agent-actual", content.strip() == "hello",
                              f"content={content.strip()!r} (expected exactly 'hello')"))
        # canonical: a write landed on hello.txt (HARD).
        out.append(check_captured(self.name, tool, writes_onto(events, "hello.txt") >= 1,
                                  f"writes_onto(hello.txt)={writes_onto(events, 'hello.txt')}"))
        if tool == "codex":
            out.append(expect_no_marker_noise(self.name, tool))
        # viewer: served all canonical events (HARD completeness).
        ok, detail = viewer_served_all(res)
        out.append(check_viewer(self.name, tool, ok, detail))
        return out


SCENARIO = SingleWrite()
