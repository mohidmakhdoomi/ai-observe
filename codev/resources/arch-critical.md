# arch-critical.md — Always-On System-Shape Facts (HOT tier)

<!-- HOT tier: capped facts + a bounded map of arch.md. Always injected into every porch
phase prompt and into CLAUDE.md/AGENTS.md. CAP: <=10 facts, <=12 map topics, <=35 lines.
To add a fact, DEMOTE a weaker one into arch.md (displacement). MAINTAIN polices the cap
and keeps the map in sync with arch.md's top-level sections. -->

## Critical facts (consult before deciding)
- CI fails loud on ANY unittest skip (main suite and packaging smoke): capability-gated skips are a local-dev affordance only — a test gated on a capability CI doesn't provision turns the matrix red, not silently green.

## Map of arch.md (consult when…)
- Layered observer architecture — consult when changing what the product promises to observe or report.
- Backend abstraction — consult when adding/selecting event backends or touching ordering invariants.
- Generic command observer core — consult when touching shims, resolvers, or the compatibility facade.
- Artifact contract — consult when changing session artifacts, sidecar authority, or recovery flows.
- Provenance model — consult when emitting or consuming events (schema/source/confidence fields).
- Browser viewer invariants — consult when changing the viewer server, sanitization, or UI data flow.
- Packaging and distribution — consult when touching pyproject, entry points, package data, or shims-vs-install behavior.
- Continuous integration — consult when changing the CI workflow, test invocation, or skip gating.
- Deferred kernel backends — consult when tempted to add fanotify/inotify/eBPF.
