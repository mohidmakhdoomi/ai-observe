"""S3 modify/append: the agent appends to an existing file.

agent-actual (HARD): the appended content is on disk. canonical (HARD): a write
lands on the modified file (an append shows as a modify, or a tmp+rename for
atomic-write tools). claude/agy only (per the plan's S3).
"""

from __future__ import annotations

from ..harness import writes_onto
from ..oracle import CheckResult, check_agent_file, check_captured, check_viewer, hard_check
from . import drive, session_dirs, viewer_served_all

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
        # Append (not overwrite): the seed line must SURVIVE and the appended word
        # must be present — overwriting notes.txt with just "appended" must fail.
        appended_ok = "line one" in content and "appended" in content
        out.append(hard_check(self.name, tool, "agent-actual", appended_ok,
                              f"seed_survived={'line one' in content} appended={'appended' in content}"))
        out.append(check_captured(self.name, tool, writes_onto(events, "notes.txt") >= 1,
                                  f"writes_onto(notes.txt)={writes_onto(events, 'notes.txt')}"))
        # viewer: served all canonical events (HARD completeness).
        ok, detail = viewer_served_all(res)
        out.append(check_viewer(self.name, tool, ok, detail))
        return out


SCENARIO = Modify()
