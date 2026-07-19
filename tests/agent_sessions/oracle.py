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
INFO = "info"      # a non-gating observation recorded for evidence (never fails)


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
    32: KnownBug(32, "annotated AT_FDCWD deletion dropped (claude/agy delete never reported)", active=False),
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

    `result` is a `harness.SessionResult` (duck-typed: `.returncode`, `.disk_events`
    with a `total`, and an optional `.meta` carrying a `stderr_tail`). A nonzero return
    code or zero watched-root events means the agent errored, is unauthenticated, or was
    misinvoked — a loud, named failure, not a silent skip (Spec 38, Decision 4 / req 6).

    The wrapper's stderr tail (persisted to the session outdir by the harness) is folded
    into the failure detail so the *reason* shows inline — a harness misinvocation (e.g.
    codex refusing a non-git workdir) reads as itself, not a bare "agent exited 1".
    """
    tail = str(((getattr(result, "meta", None) or {}).get("stderr_tail")) or "").strip()
    suffix = f"; stderr tail: {tail[-400:]}" if tail else ""
    if getattr(result, "returncode", 0) != 0:
        raise ToolUnusable(tool, f"agent exited {result.returncode}{suffix}")
    total = (getattr(result, "disk_events", None) or {}).get("total", 0)
    if not total:
        raise ToolUnusable(tool, f"produced zero watched-root events{suffix}")


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


def note(scenario: str, tool: str, view: str, detail: str) -> CheckResult:
    """A non-gating informational record (status INFO): retains live evidence
    without ever failing. Used e.g. to record which deletion form an agent
    actually emitted this run, without letting agent nondeterminism flap a gate."""
    return CheckResult(scenario, tool, view, INFO, detail)


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
# Bug-specific gates (#32, #33) — DETERMINISTIC parser probes.
#
# #32 and #33 are both `trace_parser` bugs. A LIVE agent is an unreliable trigger
# (the deletion syscall form and the sandbox's marker probing vary run-to-run), so
# gating on a live run's events makes the annotation flap. Instead we reproduce each
# bug the way the round-1 experiment actually verified it (FINDINGS F1/F2): feed the
# exact syscall forms through ai-observe's real `trace_parser` and assert on the
# result. Deterministic, tool-free, and rot-proof — the live scenarios still validate
# agent-actual + viewer, but the bug GATE no longer depends on agent nondeterminism.
# ---------------------------------------------------------------------------

_BUG32_ROOT = "/tmp/ai-observe-bug32-probe"
_BUG33_ROOT = "/tmp/ai-observe-bug33-probe"


def _parser_events(lines: list[str], root: str) -> list[dict]:
    from ai_observe.trace_parser import TraceParser

    parser = TraceParser(
        session_id="probe", invocation_id="probe", command=["probe"],
        initial_cwd=root, active_artifacts=set(), include_log_writes=False,
        watched_roots=(root,))
    return parser.parse_lines(lines).events


def bug32_signature() -> tuple[bool, bool]:
    """Deterministic #32 probe. Returns (annotated_deletion_dropped, plain_captured).

    #32 reproduces when the annotated `unlinkat(AT_FDCWD<dir>, "f", 0)` form (what
    claude/agy emit via libc) yields NO delete event, while the plain
    `unlinkat(AT_FDCWD, "<abs>", 0)` form still does — i.e. the drop is specific to
    the dirfd annotation, not a total break.
    """
    root = _BUG32_ROOT
    base = [
        f'1 1.0 openat(AT_FDCWD, "{root}/f.txt", O_WRONLY|O_CREAT, 0600) = 3<{root}/f.txt>',
        f'1 1.1 write(3<{root}/f.txt>, "x", 1) = 1',
        f'1 1.2 close(3<{root}/f.txt>) = 0',
    ]
    annotated = _parser_events(base + [f'1 1.3 unlinkat(AT_FDCWD<{root}>, "f.txt", 0) = 0'], root)
    plain = _parser_events(base + [f'1 1.3 unlinkat(AT_FDCWD, "{root}/f.txt", 0) = 0'], root)
    has_delete = lambda evs: any(e.get("operation") == "delete" for e in evs)
    return (not has_delete(annotated)), has_delete(plain)


def expect_deletion_captured(scenario: str, tool: str, *, bug: int = 32,
                             registry: Mapping[int, KnownBug] = OPEN_BUGS) -> CheckResult:
    """#32: the annotated-dirfd deletion should be captured as a canonical `delete`."""
    dropped, plain_ok = bug32_signature()
    return known_bug_gate(scenario, tool, "canonical", bug,
                          buggy_present=dropped and plain_ok,
                          correct_present=(not dropped) and plain_ok,
                          detail=f"annotated_deletion_dropped={dropped} plain_captured={plain_ok}",
                          registry=registry)


def bug33_unpaired_marker_delete() -> bool:
    """Deterministic #33 probe: True if a `/newroot` mkdir + canonical rmdir yields an
    unpaired `delete` (the marker-noise signature — mkdir dropped by watched-root
    filtering, rmdir kept)."""
    root = _BUG33_ROOT
    evs = _parser_events([
        f'1 1.0 mkdir("/newroot{root}/.git", 0755) = 0',
        f'1 1.1 rmdir("{root}/.git") = 0',
    ], root)
    creates = sum(1 for e in evs if e.get("operation") == "create")
    deletes = sum(1 for e in evs if e.get("operation") == "delete")
    return deletes > creates


def expect_no_marker_noise(scenario: str, tool: str, *, bug: int = 33,
                           registry: Mapping[int, KnownBug] = OPEN_BUGS) -> CheckResult:
    """#33: codex's `/newroot` marker probing should not leave unpaired deletes."""
    unpaired = bug33_unpaired_marker_delete()
    return known_bug_gate(scenario, tool, "canonical", bug,
                          buggy_present=unpaired, correct_present=not unpaired,
                          detail=f"unpaired_marker_delete={unpaired}", registry=registry)


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
