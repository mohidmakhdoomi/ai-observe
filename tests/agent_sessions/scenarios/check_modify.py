"""S3 modify/append: the agent appends to an existing file.

agent-actual (HARD): the appended content is on disk. canonical (HARD): a write
lands on the modified file (an append shows as a modify, or a tmp+rename for
atomic-write tools). claude/agy only (per the plan's S3).
"""

from __future__ import annotations

from ..harness import writes_onto
from ..oracle import CheckResult, check_agent_file, check_captured, hard_check
from . import drive, session_dirs

_SEED = "line one\n"
_PROMPT = ("Append a new line containing exactly the word appended to the existing "
           "file notes.txt. Do not create any other file. Then stop.")


class Modify:
    name = "modify"
    applies_to = {"claude", "agy"}

    def run(self, tool: str, ctx) -> list[CheckResult]:
        # Seed the file BEFORE the observed session so the agent appends to it.
        workdir, _ = session_dirs(ctx, f"mod_{tool}")
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "notes.txt").write_text(_SEED)

        res, events = drive(tool, _PROMPT, f"mod_{tool}", ctx)
        out: list[CheckResult] = []
        content = ""
        target = workdir / "notes.txt"
        if target.exists():
            content = target.read_text()
        out.append(check_agent_file(self.name, tool, res.workdir_files, "notes.txt"))
        out.append(hard_check(self.name, tool, "agent-actual", "appended" in content,
                              f"appended-content-present={'appended' in content}"))
        out.append(check_captured(self.name, tool, writes_onto(events, "notes.txt") >= 1,
                                  f"writes_onto(notes.txt)={writes_onto(events, 'notes.txt')}"))
        return out


SCENARIO = Modify()
