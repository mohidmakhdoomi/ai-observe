# Lessons Learned

## Preserve compatibility facades during generic refactors

When extracting a tool-specific implementation into a generic core, keep the
old module path as a facade that still exposes the test-facing and caller-facing
helpers. In this project, aliasing `ai_observe.codex_observe` to the generic
observer module preserved monkeypatch behavior for live-trace tests and avoided
subtle divergence between the compatibility shim and the real code path.

## Make recursion-avoidance tests cross-installation, not just same-directory

PATH shims can recurse even when the recursive wrapper lives in a different
installation directory. Resolver tests should include cross-directory observer
shim cases and direct wrapper-name resolution, not only "skip my own file"
checks beside the currently invoked script.

## Turn broad compatibility promises into explicit matrix tests

Alias support such as `AI_OBSERVE_*` preferred over `CODEV_OBSERVE_*` is easy to
state but easy to under-test. Convert each promised shared variable class
(disable, directory, session id, quiet mode, parser strictness, symlink policy,
signal grace, live parsing) into direct precedence tests or end-to-end wrapper
tests before calling a compatibility phase complete.

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

## Pin legacy tests when a new default backend changes observation scope

When a product gains a second default event source, old tests that were written for a single-source world can become latently flaky even if they still pass most of the time. Tests that are intentionally exercising one backend or one observation scope should pin that backend explicitly (for example `AI_OBSERVE_BACKENDS=strace`) or isolate their watched root/cwd so later architectural defaults do not broaden the assertion surface accidentally.

## Make artifact authority explicit when recovery can yield multiple valid outputs

If a recovery flow can leave behind canonical, partial, rebuilt, and diagnostic artifacts at the same time, encode authority in a machine-readable sidecar rather than relying on filename conventions or UI guesses. A small explicit metadata contract keeps CLI behavior, viewer selection, and follow-on tests aligned even when timeout rebuilds and parser-failure modes differ.
