"""S5 multi-turn (Exp 4): later-turn file ops are captured under ONE wrapper.

The original #31 use case round 1 never exercised: a 3-turn chained conversation
under a single ai-observe wrapper. Proves ai-observe captures file operations from
turns 2, 3 — not just turn 1 — via each tool's resume/continue mechanism.

Turns: (1) create turn1.txt="one"  (2) create turn2.txt="two"
       (3) append "three" to turn1.txt

agent-actual (HARD): both files present; turn1 has the seed and the appended word
(continuity). canonical (HARD): a write lands on turn2 (turn-2 op captured) and >=2
writes land on turn1 (turn-1 create + turn-3 append — later-turn op captured).
viewer (HARD): completeness. codex: #33 annotated.
"""

from __future__ import annotations

from ..drivers import run_multi_turn
from ..harness import writes_onto
from ..oracle import (
    CheckResult,
    check_agent_file,
    check_captured,
    check_viewer,
    expect_no_marker_noise,
    hard_check,
)
from . import session_dirs, viewer_served_all

_TURNS = [
    "Create a file named turn1.txt containing exactly the word one. Then stop.",
    "Create a file named turn2.txt containing exactly the word two. Then stop.",
    "Append a line containing exactly the word three to the existing file turn1.txt. "
    "Do not create other files. Then stop.",
]


class MultiTurn:
    name = "multi_turn"
    applies_to = {"claude", "agy", "codex"}

    def run(self, tool: str, ctx) -> list[CheckResult]:
        workdir, outdir = session_dirs(ctx, f"mt_{tool}")
        res, events = run_multi_turn(tool, _TURNS, f"mt_{tool}", workdir, outdir,
                                     timeout=ctx.timeout)
        out: list[CheckResult] = []
        out.append(check_agent_file(self.name, tool, res.workdir_files, "turn1.txt"))
        out.append(check_agent_file(self.name, tool, res.workdir_files, "turn2.txt"))
        t1 = workdir / "turn1.txt"
        t1_content = t1.read_text() if t1.exists() else ""
        out.append(hard_check(self.name, tool, "agent-actual",
                              "one" in t1_content and "three" in t1_content,
                              f"turn1 seed+append: one={'one' in t1_content} three={'three' in t1_content}"))
        # canonical: LATER-turn ops captured, not just turn 1.
        w1, w2 = writes_onto(events, "turn1.txt"), writes_onto(events, "turn2.txt")
        out.append(check_captured(self.name, tool, w2 >= 1,
                                  f"turn-2 op captured: writes_onto(turn2.txt)={w2}"))
        out.append(check_captured(self.name, tool, w1 >= 2,
                                  f"turn-3 op captured: writes_onto(turn1.txt)={w1} (create+append)"))
        if tool == "codex":
            out.append(expect_no_marker_noise(self.name, tool))
        ok, detail = viewer_served_all(res)
        out.append(check_viewer(self.name, tool, ok, detail))
        return out


SCENARIO = MultiTurn()
