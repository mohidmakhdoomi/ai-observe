# lessons-critical.md — Always-On Engineering Wisdom (HOT tier)

<!-- HOT tier: capped lessons + a bounded map of lessons-learned.md. Always injected into
every porch phase prompt and into CLAUDE.md/AGENTS.md. CAP: <=10 lessons, <=12 map topics,
<=35 lines. To add a lesson, DEMOTE a weaker one into lessons-learned.md (displacement).
MAINTAIN polices the cap and keeps the map in sync with lessons-learned.md's sections. -->

## Critical lessons (consult before deciding)
- Check for existing work (PRs, git history) before building from scratch.
- "It compiled" / "tests pass" is not "it works" — verify the real user path before calling it done.
- When stuck (2 failed hypotheses or ~30 min), get an outside perspective instead of guessing.
- Stage new files the moment you create them: porch's commit sweep only commits staged files, so an untracked deliverable ships a broken canonical diff. Check `git status` for `??` before signaling build-complete.

## Map of lessons-learned.md (consult when…)
- Compatibility facades & alias matrices — consult when refactoring tool-specific code into a generic core, or promising `AI_OBSERVE_*` / `CODEV_OBSERVE_*` env aliases.
- Shim resolution & recursion — consult when changing PATH-shim real-executable resolution.
- Import fallback scoping — consult when a shim prefers an installed package but falls back to the checkout.
- Browser viewer / UI internals — consult when adding reversible client-side filters, aggregators, dynamic controls, or synthesized tree-node semantics.
- Streaming protocol evolution — consult when changing an internal streaming/SSE frame protocol.
- Backend-scope test pinning — consult when a new default backend can broaden single-source test assertions.
- Artifact authority & recovery — consult when a recovery flow can leave multiple valid artifacts.
- Packaging & wheel proof — consult when changing package-data, src-layout static assets, or building distributions programmatically.
- Performance testing — consult when writing CI-stable performance tests (shape over wall-clock).
- CI output-grep gates — consult when gating CI on test-output text (skips, tool markers).
- Orchestrator commit sweep — consult when creating new deliverable files under porch.
- Strace parsing, annotations & path identity — consult when comparing strace tokens literally, choosing parser test-input forms, or when one file appears under multiple path spellings (sandbox namespaces).
