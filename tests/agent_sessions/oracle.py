"""The assertion layer: a three-view oracle + a rot-proof known-bug registry (Spec 38).

Each scenario compares three views of what happened and classifies each check:

* **agent-actual** — files/content left in the workdir. Always a HARD assertion
  (every experiment round confirmed the agent side worked; a failure here is a real
  product or scenario break).
* **canonical** — the events ai-observe recorded on disk. HARD, except where a
  known-bug gate applies.
* **viewer** — the sanitized events the browser viewer served. HARD for
  completeness/shape.

Known bugs (#32/#33/#36) are tolerated as **expected-and-annotated** signatures via
`known_bug_gate` until each fix merges, then flipped to a hard assertion by the
single edit `OPEN_BUGS[N].active = False`. The gate is rot-proof: while a bug is
`active` it asserts the bug *still reproduces*, so a fix that lands without flipping
the flag fails loudly ("flip the flag"). It is an assertion path end to end — never a
`unittest.skip` — so it does not interact with the no-silent-skip CI rule.

Stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------

PASS = "pass"
FAIL = "fail"
EXCLUDED = "excluded"


def known_bug_status(issue: int) -> str:
    return f"known-bug:#{issue}"


# ---------------------------------------------------------------------------
# Known-bug registry
# ---------------------------------------------------------------------------

@dataclass
class KnownBug:
    issue: int
    desc: str
    active: bool = True


# The three open bugs the graduated oracle must account for (Spec 38, req 3).
# Flip `active = False` (a one-line change) when the corresponding fix merges; the
# gate below then becomes a hard regression assertion for that bug.
OPEN_BUGS: dict[int, KnownBug] = {
    32: KnownBug(32, "annotated AT_FDCWD deletion dropped (claude/agy delete never reported)"),
    33: KnownBug(33, "codex /newroot mount-namespace marker-noise: unpaired delete events"),
    36: KnownBug(36, "sidecar labels snapshot-only .jsonl authoritative_complete after direct-parser failure"),
}


# ---------------------------------------------------------------------------
# Check result
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    scenario: str
    tool: str
    view: str          # "agent-actual" | "canonical" | "viewer" | "runner"
    status: str        # PASS | FAIL | EXCLUDED | known-bug:#N
    detail: str = ""

    @property
    def is_fail(self) -> bool:
        return self.status == FAIL

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# ---------------------------------------------------------------------------
# Tool usability (M4: installed-but-unusable / unauthenticated → loud, named)
# ---------------------------------------------------------------------------

class ToolUnusable(Exception):
    """A requested tool is present but produced nothing usable (no auth / crash).

    Carries the tool name so the runner can render a loud, named failure rather
    than a silent skip. Raised by `ensure_tool_usable` from a scenario's first
    agent invocation.
    """

    def __init__(self, tool: str, detail: str = ""):
        self.tool = tool
        self.detail = detail
        super().__init__(f"tool {tool!r} unusable: {detail}")


def ensure_tool_usable(tool: str, result) -> None:
    """Raise `ToolUnusable(tool)` if `result` shows the tool did nothing usable.

    `result` is a `harness.SessionResult` (duck-typed: `.returncode`,
    `.disk_events` with a `total`). A nonzero return code or zero watched-root
    events means the agent is missing auth or errored — a loud, named failure, not
    a silent skip (Spec 38, Decision 4 / req 6).
    """
    if getattr(result, "returncode", 0) != 0:
        raise ToolUnusable(tool, f"agent exited {result.returncode}")
    total = (getattr(result, "disk_events", None) or {}).get("total", 0)
    if not total:
        raise ToolUnusable(tool, "produced zero watched-root events")


# ---------------------------------------------------------------------------
# Hard-assert helpers (agent-actual / canonical / viewer)
# ---------------------------------------------------------------------------

def hard_check(scenario: str, tool: str, view: str, ok: bool, detail: str = "") -> CheckResult:
    return CheckResult(scenario, tool, view, PASS if ok else FAIL, detail)


def check_agent_file(scenario: str, tool: str, files: Iterable[str], name: str,
                     *, present: bool = True) -> CheckResult:
    have = name in set(files)
    return hard_check(scenario, tool, "agent-actual", have == present,
                      f"file {name!r} present={have} expected={present}")


def check_captured(scenario: str, tool: str, ok: bool, detail: str = "") -> CheckResult:
    return hard_check(scenario, tool, "canonical", ok, detail)


def check_viewer(scenario: str, tool: str, ok: bool, detail: str = "") -> CheckResult:
    return hard_check(scenario, tool, "viewer", ok, detail)


# ---------------------------------------------------------------------------
# The known-bug gate (rot-proof; one-line flip)
# ---------------------------------------------------------------------------

def known_bug_gate(scenario: str, tool: str, view: str, bug: int, *,
                   buggy_present: bool, correct_present: bool, detail: str = "",
                   registry: Mapping[int, KnownBug] = OPEN_BUGS) -> CheckResult:
    """Gate a check through a tracked bug.

    While the bug is **active**: assert the buggy signature still reproduces →
    `known-bug:#N` (pass, annotated). If it no longer reproduces, **fail** with a
    "flip the flag" message (rot-proof — a silent fix cannot pass unnoticed).

    When the bug is flipped **inactive** (the one-line change): assert the correct
    behavior → `pass`; a lingering bug → `fail` ("regressed").
    """
    kb = registry[bug]
    if kb.active:
        if buggy_present:
            return CheckResult(scenario, tool, view, known_bug_status(bug),
                               f"{kb.desc} (annotated; still reproduces). {detail}".strip())
        return CheckResult(
            scenario, tool, view, FAIL,
            f"bug #{bug} no longer reproduces — flip OPEN_BUGS[{bug}].active=False to "
            f"enable the hard assertion. {detail}".strip())
    if correct_present:
        return CheckResult(scenario, tool, view, PASS,
                           f"#{bug} fixed: correct behavior asserted. {detail}".strip())
    return CheckResult(scenario, tool, view, FAIL,
                       f"#{bug} regressed: {kb.desc}. {detail}".strip())


# ---------------------------------------------------------------------------
# Bug-specific gates (compute the buggy/correct signatures)
# ---------------------------------------------------------------------------

_MARKER_BASENAMES = {".git", ".agents", ".codex"}


def expect_deletion_captured(scenario: str, tool: str, events: list[dict], name: str,
                             *, bug: int = 32,
                             registry: Mapping[int, KnownBug] = OPEN_BUGS) -> CheckResult:
    """#32: an ephemeral file's deletion should appear as a canonical `delete`.

    While #32 is open (claude/agy), the delete is silently dropped → buggy.
    """
    captured = any(
        e.get("operation") == "delete"
        and (e.get("path") or "").rsplit("/", 1)[-1] == name
        for e in events
    )
    return known_bug_gate(scenario, tool, "canonical", bug,
                          buggy_present=not captured, correct_present=captured,
                          detail=f"delete({name}) captured={captured}", registry=registry)


def marker_noise_deletes(events: list[dict]) -> int:
    """Count codex's #33 marker-noise deletes (`.git`/`.agents`/`.codex` or /newroot)."""
    n = 0
    for e in events:
        if e.get("operation") != "delete":
            continue
        path = e.get("path") or ""
        base = path.rsplit("/", 1)[-1]
        if base in _MARKER_BASENAMES or "/newroot" in path:
            n += 1
    return n


def expect_no_marker_noise(scenario: str, tool: str, events: list[dict],
                           *, bug: int = 33,
                           registry: Mapping[int, KnownBug] = OPEN_BUGS) -> CheckResult:
    """#33: codex should not emit `/newroot` marker-noise deletes.

    While #33 is open, dozens of unpaired marker deletes appear → buggy.
    """
    noise = marker_noise_deletes(events)
    return known_bug_gate(scenario, tool, "canonical", bug,
                          buggy_present=noise > 0, correct_present=noise == 0,
                          detail=f"marker_noise_deletes={noise}", registry=registry)


def authority_overstated(meta: Mapping) -> bool:
    """True when the sidecar overstates authority on the parse-failure path (#36).

    The `.meta.json` reports `parser.status = parser_failure_*` yet still labels an
    event artifact `authoritative_complete` — the snapshot-only `.jsonl` masquerading
    as a complete authoritative record.
    """
    parser_status = str((meta.get("parser") or {}).get("status") or "")
    if not parser_status.startswith("parser_failure"):
        return False
    for entry in (meta.get("artifacts") or {}).values():
        role = entry.get("role") if isinstance(entry, Mapping) else entry
        if role == "authoritative_complete":
            return True
    return False


def expect_authority_not_overstated(scenario: str, tool: str, meta: Mapping,
                                    *, bug: int = 36,
                                    registry: Mapping[int, KnownBug] = OPEN_BUGS) -> CheckResult:
    """#36: after a direct-parser failure the sidecar must not label a snapshot-only
    `.jsonl` `authoritative_complete`. While #36 is open, it does → buggy."""
    overstated = authority_overstated(meta)
    parser_status = str((meta.get("parser") or {}).get("status") or "")
    return known_bug_gate(scenario, tool, "canonical", bug,
                          buggy_present=overstated, correct_present=not overstated,
                          detail=f"parser_status={parser_status!r} authority_overstated={overstated}",
                          registry=registry)
