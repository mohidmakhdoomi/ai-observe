# maintain-VRNq thread

## Kickoff (2026-07-14)

MAINTAIN protocol, strict mode. First maintenance run for ai-observe (`codev/maintain/0001.md`).

- Project initialized in porch as `maintain-VRNq-codebase-maintenance`, phase `maintain`.
- Python project: build check skipped, tests via `python3 -m unittest discover -s tests`.
- No prior `codev/maintain/` runs — this is 0001, base = full history but focus on doc-sync + dead-code audit.

Plan: audit (dead code, unused deps, stale doc refs) → clean → sync governance docs (hot+cold tiers) + CLAUDE/AGENTS parity → final checks → PR with 3-way review.

## Maintain phase done (2026-07-14)

Audit outcome — cold tiers already current (SPIR builders kept them fresh); no cuts to
arch.md / lessons-learned.md, all path refs resolve. Real defect was the **hot tier**:
`lessons-critical.md` still shipped STARTER placeholders (empty cold-doc map + placeholder
lesson line).

Changes (3 commits):
1. Filled lessons-critical.md map (11 thematic topics over 15 cold sections), promoted the
   porch commit-sweep rule into hot lessons, removed placeholder line, trimmed stale STARTER
   comment sentence from both hot files. Hot caps respected (4 lessons/11 map/25 lines;
   arch-critical 20 lines).
2. .gitignore: ignore afx builder session scaffolding (mirrors `.architect-role.md`).
3. Run file `codev/maintain/0001.md` + this thread.

No dead code removed (no vulture/pyflakes; zero third-party deps; 236 tests green, 0 skips —
didn't manufacture removals without tooling). CLAUDE.md == AGENTS.md except intentional
title/note. `porch check` green. Next: `porch done` → review phase → PR + 3-way consult.

## Review phase / PR (2026-07-14)

3-way maintain-phase consult: Gemini / Codex / Claude all **APPROVE**, HIGH confidence,
zero issues. Final validation green (236 tests, 0 skips), arch.md doc links all resolve.
Pushed branch, opened **PR #24**. Not tied to a GitHub issue → no `Closes`. Recording PR
with porch, then signaling review build-complete. Awaiting architect review/merge.
