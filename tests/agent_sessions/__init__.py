"""Maintained, opt-in live-agent testing capability for ai-observe (Spec 38).

This package graduates the round-1/round-2 experiment harness
(`experiments/1_driving_mechanism/harness.py`) into a maintained test module. It
drives a *real* AI coding agent (claude / agy / codex) in non-interactive mode
under ai-observe and triangulates three views of what happened: the agent's
actual filesystem effects, the canonical `.jsonl` ai-observe recorded, and the
sanitized events the browser viewer served.

Two tiers, one hard CI rule:

* **Live tier** (`scenarios/check_*.py`) needs installed+authenticated agents and
  is **excluded from CI by construction** — it lives here, not in a top-level
  `tests/test_*.py` module, so CI's `ls test_*.py` glob and unittest's default
  `test*.py` discovery never enumerate it. There are no `unittest.skip` calls, so
  the fail-loud-on-skip CI gate is never engaged.
* **Self-test tier** (`selftest/selftest_*.py`) is deterministic and **tool-free**;
  it exercises the harness plumbing and oracle logic and is runnable anywhere via
  `python -m tests.agent_sessions --selftest`.

Run from the **repo root** (`python -m tests.agent_sessions ...`). `tests/` has no
`__init__.py`, so this resolves as a PEP 420 namespace package from the root; it
will not resolve from inside `tests/`.

Importing this package puts the checkout `src/` on `sys.path` (the sanctioned
test convention used by sibling `tests/` modules) so `ai_observe` is importable
in-process — deliberately NOT a `sys.path.insert` into a sibling `experiments/`
folder (Spec 38, N1). No live work happens at import time.
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/agent_sessions/__init__.py -> parents[2] is the repo root.
ROOT = Path(__file__).resolve().parents[2]
_SRC = ROOT / "src"
if _SRC.is_dir():
    src_str = str(_SRC)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)
