"""S2 ephemeral (create-then-delete): the #32 flip-home.

The agent creates a file then deletes it. agent-actual (HARD): the file is ABSENT
on disk. canonical: whether the deletion was captured is gated through #32 — while
#32 is open, claude/agy delete via annotated `unlinkat(AT_FDCWD<dir>, …)` which
ai-observe silently drops, so the delete is NOT captured (the annotated signature).
When #32 is fixed, flip `OPEN_BUGS[32].active=False` and this becomes a hard
assertion that the delete IS captured.

applies_to is claude/agy only: #32 is their libc-deletion path. codex deletes via
`unlink(<abs>)`, which ai-observe already captures — a different path, out of scope
for this gate.
"""

from __future__ import annotations

from ..oracle import CheckResult, check_agent_file, expect_deletion_captured
from . import drive

_PROMPT = ("Create a file named ephemeral.txt containing the word temp, then delete "
           "ephemeral.txt so it no longer exists. Then stop.")


class Ephemeral:
    name = "ephemeral"
    applies_to = {"claude", "agy"}

    def run(self, tool: str, ctx) -> list[CheckResult]:
        res, _events = drive(tool, _PROMPT, f"eph_{tool}", ctx)
        out: list[CheckResult] = []
        # agent-actual: the file must be gone (the agent really deleted it).
        out.append(check_agent_file(self.name, tool, res.workdir_files,
                                    "ephemeral.txt", present=False))
        # canonical: the #32 deletion-drop gate — deterministic parser probe (a live
        # deletion's syscall form is nondeterministic, so it can't gate reliably).
        out.append(expect_deletion_captured(self.name, tool))
        return out


SCENARIO = Ephemeral()
