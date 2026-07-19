# Phase 3 — Rebuttal to impl iter 1 review

Verdicts: **Gemini APPROVE**, **Codex REQUEST_CHANGES**, **Claude APPROVE** (all HIGH
confidence; Claude re-run after a session-limit reset). Both Codex points accepted and
fixed. Reviewers also confirmed the architect-endorsed #32/#33 deterministic-parser-probe
deviation is correctly implemented (rot-proof, tool-free flip detection).

## Codex (REQUEST_CHANGES)

1. **S1 missing a hard content assertion (`check_single_write.py`).** *Accepted — fixed.*
   S1 now reads `hello.txt` from the run's workdir and hard-asserts `"hello" in content`
   (agent-actual), in addition to presence. Verified live: `content='hello'` passes for
   both claude and codex.

2. **Viewer checks too weak / missing (`check_ephemeral.py`, `check_modify.py`,
   `check_single_write.py`, `check_subprocess.py`).** *Accepted — fixed.* Added a shared
   `viewer_served_all(res)` helper that hard-asserts **viewer completeness** — the viewer
   served EXACTLY the canonical event count (`viewer_events_count == disk_events.total`),
   the round-2 "late attach loses nothing" invariant — replacing the weak `>= 1` check.
   All four scenarios now carry it (ephemeral and modify previously had none). Verified
   live: single_write claude `viewer 3/3`, codex `viewer 34/34` (completeness holds even
   at codex's marker-noise volume).

## Gemini / Claude (APPROVE)
No changes requested. Both confirmed S1–S4 wiring, the three-view oracle usage, the
deterministic #32 gate / rot-proof flip-home, and the tool-free self-tests.

## Net changes
`scenarios/__init__.py`: added `viewer_served_all`. `check_single_write.py`: +content
assertion, +completeness viewer check. `check_ephemeral.py` / `check_modify.py`: +viewer
completeness check. `check_subprocess.py`: upgraded viewer check to completeness. Live
re-verified across claude + codex; `--selftest` 40/40. No reviewer point declined.
