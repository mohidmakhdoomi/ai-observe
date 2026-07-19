"""S7 degraded parse-failure (Exp 6): #36 sidecar-authority flip-home (Spec 38, Phase 5).

Forces ai-observe's direct parser to fail mid-stream via the in-tree
`AI_OBSERVE_TEST_FAIL_AFTER=N` hook while a paced multi-file claude task runs. The agent
still writes every file (the failure is in ai-observe's parser, not the agent), so
agent-actual is a HARD check. The oracle then reads the `.meta.json` and gates #36: while
#36 is open the sidecar labels the snapshot-only `.jsonl` `authoritative_complete` under
`parser_status = parser_failure_partial` — the bug reproduces, recorded as `known-bug:#36`;
the one-line flip (`OPEN_BUGS[36].active = False`) asserts the role is downgraded once
ai-observe is fixed. Ported from `experiments/6_degraded_recovery/degraded.py` (the
`parse_failure_partial` case only).

claude-only (Decision 9): the paced task + in-tree hook need no extra tools.

Stdlib only.
"""

from __future__ import annotations

import json

from ..harness import TOOLS, run_observed_command
from ..oracle import (
    CheckResult,
    check_agent_file,
    ensure_tool_usable,
    expect_authority_not_overstated,
    hard_check,
)
from . import session_dirs

# A paced multi-file task: enough events that the injected failure lands mid-stream and
# the snapshot fallback still has files to (net-)infer. Mirrors Exp 6 (N=5, fail after 2).
N_FILES = 5
FAIL_AFTER = 2


def _task(n: int) -> str:
    return (f"Run exactly this shell command in the current directory and nothing else: "
            f"for i in $(seq 1 {n}); do echo file$i > d$i.txt; sleep 0.6; done")


class Degraded:
    name = "degraded"
    applies_to = {"claude"}

    def run(self, tool: str, ctx) -> list[CheckResult]:
        session = f"deg_{tool}"
        workdir, outdir = session_dirs(ctx, session)
        # Force the direct-parser-failure path (Decision 9) via the in-tree hook. STRICT
        # is deliberately NOT set, so ai-observe (and the agent) still exit 0 and the
        # snapshot fallback writes net events to `.jsonl` — the exact #36 repro state.
        res = run_observed_command(
            TOOLS[tool](_task(N_FILES), workdir),
            tool=tool, session=session, workdir=workdir, outdir=outdir,
            timeout=ctx.timeout, monitor=False,
            extra_env={"AI_OBSERVE_TEST_FAIL_AFTER": str(FAIL_AFTER)})
        # M4 gate (Decision 4): an unauthenticated / errored / event-less run is a loud,
        # named ToolUnusable — never a silent skip. The snapshot fallback yields net
        # events on disk for an authenticated run, so only a broken tool trips this.
        ensure_tool_usable(tool, res)

        out: list[CheckResult] = []
        # agent-actual (always HARD): the agent wrote every file regardless of the parser
        # failure — read from the workdir on disk, not the degraded canonical.
        files = set(res.workdir_files)
        for i in range(1, N_FILES + 1):
            out.append(check_agent_file(self.name, tool, files, f"d{i}.txt"))

        # #36 canonical gate: read the sidecar and assert the authority label is not
        # overstated. The meta is written even on the parser-failure path.
        meta_path = outdir / f"{session}.meta.json"
        if not meta_path.exists():
            out.append(hard_check(self.name, tool, "canonical", False,
                                  f"no .meta.json at {meta_path.name} — cannot evaluate #36"))
            return out
        try:
            meta = json.loads(meta_path.read_text())
        except Exception as e:  # pragma: no cover - defensive
            out.append(hard_check(self.name, tool, "canonical", False,
                                  f".meta.json unreadable: {e}"))
            return out
        # Guard: if the forced failure didn't take, the #36 gate would misleadingly demand
        # a flag flip. Surface that mis-fire as its own explicit failure instead.
        parser_status = str((meta.get("parser") or {}).get("status") or "")
        if not parser_status.startswith("parser_failure"):
            out.append(hard_check(
                self.name, tool, "canonical", False,
                f"expected a forced parser_failure_* status "
                f"(AI_OBSERVE_TEST_FAIL_AFTER={FAIL_AFTER}) but meta parser_status="
                f"{parser_status!r}; #36 gate not evaluable"))
            return out
        out.append(expect_authority_not_overstated(self.name, tool, meta, bug=36))
        return out


SCENARIO = Degraded()
