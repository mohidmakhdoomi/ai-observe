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

## Prove src-layout package data from an installed wheel, not the checkout

A `src/` layout with on-disk static assets (`Path(__file__).parent/"static"`) can pass every checkout test while the *wheel* 404s on those assets, because `package-data` / `include-package-data` was never declared. "It imports" and "the checkout serves it" do not prove "the wheel serves it." Guard the seam with a smoke test that installs the built wheel into a clean venv, runs **outside** the checkout (no `PYTHONPATH=src`), and HTTP-GETs the real static routes. Make the same test the arbiter for whether any `importlib.resources` change is even needed — setuptools installs wheels unpacked, so filesystem reads usually suffice once the data is declared.

## Build each distribution kind in its own interpreter

Calling `setuptools.build_meta.build_sdist` and `build_meta.build_wheel` in the *same* Python process leaves the second artifact unwritten (in-process setuptools/distutils state carries over). When building artifacts programmatically (e.g. in smoke tests without the `build` frontend), run each kind in its own subprocess. Likewise, modern venvs ship without `setuptools`, so an install-from-sdist must pre-provision the build backend in the clean venv (or skip clearly) rather than assuming `--no-build-isolation` will find one.

## Anchor log-grep gates to tool-emitted markers, not bare words

A CI gate that greps test output for a bare word ("skipped") can false-positive on test
*names* or docstrings containing that word. Anchor the pattern to the exact markers the
tool emits (unittest: `... skipped '<reason>'` result lines and the `skipped=N` summary),
and validate the gate by running it against real captured output — both a clean run and a
forced-skip fixture — before shipping it.

## Stage new files immediately; know your orchestrator's commit sweep

Orchestrators like porch commit only staged files when they sweep the working tree at
build-complete/re-iter points. A new deliverable left untracked passes every local run
while producing a broken canonical diff (reviewers see imports of a file that isn't in the
commit). `git add` new files the moment they are created, and check `git status` for `??`
entries before signaling a build complete.

## Gate known bugs on deterministic reproductions, not live-agent nondeterminism

When a regression test must tolerate a known-but-open bug, do not gate it on a *live* trigger whose form varies run-to-run (an agent's deletion syscall shape, a sandbox's marker-probe volume): the annotation flaps between "reproduces" and "no longer reproduces" and either flags a phantom fix or masks a real one. Reproduce the bug deterministically through the real underlying component instead — feed the exact syscall forms through the actual `trace_parser`, or assert on a synthetic-but-realistic sidecar dict shaped like the real one — so the gate is stable and tool-free. Pair it with a *rot-proof* gate: while the bug is active, assert it STILL reproduces (a silent upstream fix then fails loudly, demanding the one-line flip); once flipped inactive, assert the corrected behavior (a regression fails loudly). Keep the whole thing an assertion path, never a `unittest.skip`, so it never interacts with a fail-loud-on-skip CI rule. Retain the noisy live signal separately as a non-gating `INFO` record so evidence is not lost.

## Scope import fallbacks to "package absent", not any ImportError

A shim that prefers an installed package but falls back to a checkout path should catch `ModuleNotFoundError` and fall back **only** when the top-level package itself is missing (`exc.name == "<package>"`). Catching broad `ImportError` (or any `ModuleNotFoundError`) makes a broken/incomplete install silently resolve to the checkout copy, masking the real failure. Test all three states — installed, absent→fallback, and present-but-broken→surface — and force the fallback hermetically (a `sys.meta_path` blocker, or `python -S`) so the test holds even where the package is installed.
