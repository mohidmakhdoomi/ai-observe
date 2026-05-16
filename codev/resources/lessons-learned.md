# Lessons Learned

## Keep replay state outside aggregators for client-side filter changes

When a browser UI needs reversible filtering over streamed data, keep an append-only event buffer at the UI boundary and treat the aggregator as rebuildable derived state. This keeps filter changes deterministic, avoids SSE reconnects, and makes replay equivalence testable with small pure helpers.

## Centralize UI mutations through pure helpers

For dynamic browser controls without browser-automation tests, factor validation, storage gating, selection transitions, and pattern proposal logic into exported pure helpers. Node-backed tests can then cover the risky behavior while the production UI remains plain DOM code.

## Specify synthesized tree-node semantics explicitly

Treemap/table directory rows may be synthesized from descendant files rather than emitted as literal event paths. Specs and context actions should distinguish exact path filters from subtree filters so users understand when `/dir` differs from `/dir/**`.

## Ship protocol changes with both producer and consumer support

When changing an internal streaming protocol, keep every committed phase independently compatible. Add the consumer for new frames in the same phase or before the producer starts emitting them, and retain legacy-frame handling until old tests and transitional clients are clearly obsolete.

## Prefer structural performance tests over timing thresholds

For CI-stable performance work, test the algorithmic shape and edge semantics directly: bounded batch sizes, exact-once event delivery, no tree walk for empty selections, or retained partial-line buffers. Use review notes or manual measurements for wall-clock claims instead of brittle hard timing gates.
