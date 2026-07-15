# maintain-VRNq thread

## Kickoff (2026-07-14)

MAINTAIN protocol, strict mode. First maintenance run for ai-observe (`codev/maintain/0001.md`).

- Project initialized in porch as `maintain-VRNq-codebase-maintenance`, phase `maintain`.
- Python project: build check skipped, tests via `python3 -m unittest discover -s tests`.
- No prior `codev/maintain/` runs — this is 0001, base = full history but focus on doc-sync + dead-code audit.

Plan: audit (dead code, unused deps, stale doc refs) → clean → sync governance docs (hot+cold tiers) + CLAUDE/AGENTS parity → final checks → PR with 3-way review.
