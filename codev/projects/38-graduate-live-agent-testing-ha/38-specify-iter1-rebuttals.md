# Spec 38 — Rebuttal to Specify iter 1 review

Verdicts: **Gemini REQUEST_CHANGES**, **Codex REQUEST_CHANGES**, **Claude APPROVE**
(all HIGH confidence). I accepted every substantive point; nothing was rejected.
Summary of disposition below.

## Gemini (REQUEST_CHANGES)

1. **CLI resolution mismatch (Decision 8).** *Accepted — changed.* A test suite must
   observe the working tree it imports from; preferring an installed script risks a
   stale global `ai-observe` observing while assertions target local code.
   **Decision 8 flipped to checkout-first** (`bin/ai-observe`), installed script only
   as fallback when the checkout shim is absent. Rationale recorded in the decision.

2. **Temp-dir leakage.** *Accepted — changed.* `mkdtemp` does not auto-clean and would
   leak dirs across frequent developer runs. **Decision 7 now mandates an
   auto-cleaning temp dir** (`tempfile.TemporaryDirectory` / explicit `shutil.rmtree`
   in `finally`); `mkdtemp` is explicitly rejected.

3. **#36 scope (open question 1).** *Accepted — changed.* Made unconditional (see
   Codex #1 / Claude below); **new Decision 9**.

4. **Auth-probe heuristic could mask crashes (minor).** *Accepted — changed.*
   **Decision 4's failure message now hints `--keep-artifacts`** so the developer can
   read the persisted agent stderr and distinguish a real auth failure from an
   unexpected agent/observer crash, rather than the runner guessing.

## Codex (REQUEST_CHANGES)

1. **#36 internal inconsistency (in-scope vs. deferred).** *Accepted — changed.* This
   was a genuine contradiction: Summary/Constraints/acceptance treated #36 as in-scope
   while Open Questions deferred the scenario. Resolved by **Decision 9: the
   `check_degraded.py` / S7 forced parse-failure scenario is unconditionally in v1**
   (claude-only, in-tree `AI_OBSERVE_TEST_FAIL_AFTER` hook, no extra tools). Updated
   M3, S7, and Open Questions to drop all conditional language.

2. **`--keep-artifacts` retention boundary.** *Accepted — changed (Codex option c).*
   **Decision 7 now refuses tracked in-repo destinations**, accepting only paths
   outside the repo or under the suite's known git-ignored subtree. Raw artifacts stay
   out of git by construction, not merely by a `.gitignore` a developer could sidestep
   by pointing `--keep-artifacts` at some other tracked subtree.

## Claude (APPROVE) — minor suggestions folded in

- **Report output format.** *Accepted — added Decision 10:* human-readable summary to
  stderr + `--json` structured output to stdout; nonzero exit on any `fail`
  (`known-bug:#N` is not a fail while active).
- **`--scenarios` naming convention.** *Accepted — Decision 10:* short-name selection
  (module basename minus `check_` prefix), composes with `--tools`.
- **Driver sequencing (start session → attach viewer).** *Accepted — added
  Decision 11:* F5's ordering is now part of the documented harness contract, so no
  future driver attaches a viewer before the artifact exists.

## Net changes to the spec

New Decisions **9** (#36 in scope), **10** (runner output / `--scenarios`), **11**
(driver sequencing); revised Decisions **4** (auth hint), **7** (auto-clean +
keep-artifacts boundary), **8** (checkout-first). Updated M3, S7, Open Questions, and
added a Consultation Log entry. No reviewer point was declined.
