# Lessons Learned

## Keep replay state outside aggregators for client-side filter changes

When a browser UI needs reversible filtering over streamed data, keep an append-only event buffer at the UI boundary and treat the aggregator as rebuildable derived state. This keeps filter changes deterministic, avoids SSE reconnects, and makes replay equivalence testable with small pure helpers.

## Centralize UI mutations through pure helpers

For dynamic browser controls without browser-automation tests, factor validation, storage gating, selection transitions, and pattern proposal logic into exported pure helpers. Node-backed tests can then cover the risky behavior while the production UI remains plain DOM code.

## Specify synthesized tree-node semantics explicitly

Treemap/table directory rows may be synthesized from descendant files rather than emitted as literal event paths. Specs and context actions should distinguish exact path filters from subtree filters so users understand when `/dir` differs from `/dir/**`.
