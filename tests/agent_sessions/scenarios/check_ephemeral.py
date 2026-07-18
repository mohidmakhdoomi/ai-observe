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

from ..harness import writes_onto
from ..oracle import (
    CheckResult,
    check_agent_file,
    check_captured,
    check_viewer,
    expect_deletion_captured,
    note,
)
from . import drive, viewer_served_all

_PROMPT = ("Create a file named ephemeral.txt containing the word temp, then delete "
           "ephemeral.txt so it no longer exists. Then stop.")


class Ephemeral:
    name = "ephemeral"
    applies_to = {"claude", "agy"}

    def run(self, tool: str, ctx) -> list[CheckResult]:
        res, events = drive(tool, _PROMPT, f"eph_{tool}", ctx)
        out: list[CheckResult] = []
        # canonical: the create actually happened this run (HARD) — proves the
        # create-then-delete scenario ran, so a run that never created the file
        # cannot pass on final-absence alone.
        out.append(check_captured(self.name, tool, writes_onto(events, "ephemeral.txt") >= 1,
                                  f"writes_onto(ephemeral.txt)={writes_onto(events, 'ephemeral.txt')} (create captured live)"))
        # agent-actual: the file must be gone (the agent really deleted it).
        out.append(check_agent_file(self.name, tool, res.workdir_files,
                                    "ephemeral.txt", present=False))
        # informational (non-gating): did ai-observe's DIRECT layer capture the
        # agent's ACTUAL deletion this run? The deletion syscall form is
        # agent-nondeterministic (sometimes the annotated dirfd form #32 drops,
        # sometimes a captured form), so this is recorded as live evidence but must
        # NOT gate — the #32 gate below is the deterministic parser probe instead.
        live_direct_delete = any(
            e.get("source") == "strace" and e.get("operation") == "delete"
            and (e.get("path") or "").rsplit("/", 1)[-1] == "ephemeral.txt"
            for e in events)
        out.append(note(self.name, tool, "canonical",
                        f"live-run direct-layer deletion captured this run: "
                        f"{live_direct_delete} (informational; syscall form is "
                        f"agent-nondeterministic — see the #32 gate)"))
        # canonical: the #32 deletion-drop gate — deterministic parser probe.
        out.append(expect_deletion_captured(self.name, tool))
        # viewer: served all canonical events (HARD completeness).
        ok, detail = viewer_served_all(res)
        out.append(check_viewer(self.name, tool, ok, detail))
        return out


SCENARIO = Ephemeral()
