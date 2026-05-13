# Review: browser-visualizer-for-filesys

## Summary

Implemented a local browser visualizer for ai-observe filesystem-event JSONL streams. The feature adds a stdlib Python loopback server with Server-Sent Events, a browser-side aggregation model, a WinDirStat-style treemap, a linked sortable tree/table, metric/noise toggles, drill-down breadcrumbs, tests, and documentation.

Primary entry point for this repo checkout:

```bash
PYTHONPATH=src python3 -m ai_observe.viewer .codev/observe/<session-id>.jsonl
```

## Spec Compliance

- [x] Loopback-only server starts from `ai_observe.viewer`, validates the JSONL path, supports `--port`, and honors `--no-browser`.
- [x] Server tails the JSONL, buffers partial trailing lines, handles truncation/inode replacement, skips malformed/schema-mismatched lines with warnings, and emits sanitized SSE `append` frames.
- [x] Browser aggregation tracks Bytes, Events, Recent, `last_touched`, rename migration, tombstones, collisions, and resurrection after fresh source-path events.
- [x] Default noise filter implements the specified exclude list and retains noise data so `Show noise` can reveal it without reconnecting.
- [x] Treemap and table are rendered side-by-side with linked selection/hover.
- [x] Table columns are `Path`, `Bytes written`, `Events`, and `Last touched`; sort is sibling-local.
- [x] Top bar provides metric toggles, `Show noise`, event/filter counters, live/idle/shutdown badge, breadcrumb, and `▲ Up`.
- [x] Treemap directory clicks drill into subtrees; file clicks select without drilling; breadcrumb/Up navigates toward `/`.
- [x] Security posture is preserved: loopback only, no `raw_syscall`/`command` in SSE payloads, text rendering APIs instead of `innerHTML`, fixed document title, no path state in URL/history/title.
- [x] Tests cover tailer behavior, SSE framing/replay/live append, aggregation metrics/rename/filtering, JS parity where Node is available, treemap layout, breadcrumb helpers, table sorting, static lints, and page asset loading.
- [x] Documentation added in `docs/viewer.md`; `docs/observe.md` links to it.

## Deviations from Plan

- **Repo-local invocation**: The original user-facing spec says `python -m ai_observe.viewer`; because this repository has a `src/` layout without packaging metadata, docs use `PYTHONPATH=src python3 -m ai_observe.viewer` for checkout-local execution. This matches existing repo test conventions.
- **Node-backed JS checks**: The plan initially relied heavily on Python oracles. Review feedback pushed the implementation to execute real JS modules under Node when available. These checks are skipped if Node is absent to preserve the no-hard-CI-dependency constraint.
- **Manual walkthrough**: The builder environment is headless. I performed CLI/oracle/server smoke checks and recorded them below; final GUI click-through should be repeated by the architect/operator before merge.
- **Architecture docs**: `codev/resources/arch.md` does not exist in this repository, so no architecture file was updated.
- **Lessons learned docs**: `codev/resources/lessons-learned.md` does not exist in this repository, so no lessons-learned file was updated.

## Walkthrough Notes

Date: 2026-05-13. Environment: headless builder worktree `/home/user/code/ai-observe/.builders/spir-5`.

Commands run:

```bash
python3 -m unittest discover -s tests
```

Result: OK (`97` tests).

Reference-trace aggregation smoke check used `/home/user/code/ai-observe/.codev/observe/20260513T165110Z-16975-8f23.jsonl`:

- Parsed `8818` schema-v1 events.
- Default filter counted `8818` filtered events for this trace. The trace is effectively all paths under `/home/user/.codex`, `/dev`, or `/var/tmp`.
- `Show noise` mode exposes hidden roots in the retained aggregation state, validating the realistic-trace toggle path.

CLI start smoke check:

```bash
timeout 2s env PYTHONPATH=src python3 -m ai_observe.viewer --no-browser /home/user/code/ai-observe/.codev/observe/20260513T165110Z-16975-8f23.jsonl
```

The server printed a loopback URL successfully. The timeout interrupted replay before the large trace finished; a shutdown warning about an incomplete buffered fragment was expected from forced early termination.

Final GUI checklist for architect/operator before merge:

```bash
PYTHONPATH=src python3 -m ai_observe.viewer --no-browser /home/user/code/ai-observe/.codev/observe/20260513T165110Z-16975-8f23.jsonl
```

Open the printed URL and verify metric toggles, `Show noise`, linked selection/hover, table sorting, treemap drill-down, breadcrumb navigation, and `▲ Up` behavior.

## Lessons Learned

### What Went Well

- The server/client split stayed clean: Python only tails and frames sanitized events, while aggregation and UI state live in the browser.
- The explicit SSE payload whitelist made security review straightforward and prevented accidental `raw_syscall`/`command` leakage.
- Synthetic fixtures plus golden snapshots caught rename, metric, and filter semantics without depending on sensitive real traces.
- Review feedback improved test credibility by adding real JS execution checks while keeping Node optional.

### Challenges Encountered

- Strict-mode consultation availability changed mid-session due Claude quota limits. The architect temporarily configured Codex-only consultations, allowing porch to continue without bypassing gates.
- Snapshot-time noise filtering initially drifted from the spec by dropping noise at ingest. This was corrected so one aggregation state supports both default and `Show noise` views.
- Browser interaction testing is constrained by the repo's no-extra-CI-deps posture. The compromise is static/server smoke tests plus pure-helper/JS parity tests; full DOM click testing remains manual.
- The checkout-local invocation needed explicit `PYTHONPATH=src` documentation because there is no package install metadata.

### What Would Be Done Differently

- Export small pure JS helpers from the start for Node-backed tests instead of retrofitting them after review.
- Add the review/walkthrough artifact earlier in Phase 4 so docs and manual verification notes are reviewed together.
- Consider adding minimal packaging metadata in a separate project so the spec's `python -m ai_observe.viewer` command works without `PYTHONPATH` from a checkout.

### Methodology Improvements

- When a plan relies on browser code but forbids browser automation, require real-JS pure-function tests up front for core layout/state helpers.
- For strict builders, porch review prompts should include untracked newly-created files in the changed-file list; several new Phase 3/4 files were not shown in consultation diffs but were present on disk.

## Architecture Updates

No architecture file was updated because `codev/resources/arch.md` does not exist in this repository. Architectural changes introduced by the project are documented here and in `docs/viewer.md`:

- new package `src/ai_observe/viewer/`;
- `JsonlTailer` reads/reopens JSONL and emits sanitized events;
- `ViewerServer` serves static assets and `/events` SSE on `127.0.0.1`;
- browser modules under `src/ai_observe/viewer/static/` perform aggregation, treemap layout/rendering, table rendering, and page orchestration.

## Lessons Learned Updates

No lessons-learned file was updated because `codev/resources/lessons-learned.md` does not exist in this repository. Generalizable lessons are captured in this review's `## Lessons Learned` section.

## Technical Debt

- There is no package metadata, so checkout-local use requires `PYTHONPATH=src`.
- Full browser DOM interaction coverage is manual; tests cover the pure helper/state/layout pieces but do not use Selenium/Playwright.
- The realistic reference trace available locally is almost entirely filtered as noise, so it is useful for scale and `Show noise` checks but not for demonstrating an interesting default filtered treemap.

## Consultation Feedback

### Specify Phase (Round 1)

#### Gemini
- **Concern**: Gemini consultation was skipped per project preference.
  - **N/A**: No Gemini feedback was produced.

#### Codex
- **Concern**: Rename semantics were underdefined across Bytes, Events, Recent, tombstoning, and collisions; live partial-line buffering and tree sort semantics needed precision; reference trace availability was unclear.
  - **Addressed**: Spec now defines rename migration/tombstones/collisions, partial trailing-line buffering, sibling-local sort semantics, and fixture/reference-trace expectations.

#### Claude
- **Concern**: Spec incorrectly implied rename events lacked `path`; actual parser sets `path == new_path`.
  - **Addressed**: Spec now explicitly states rename events carry `old_path`, `new_path`, and `path`, and detection must dispatch on `operation == "rename"`.

### Plan Phase (Round 1)

#### Gemini
- **Concern**: Gemini consultation was skipped per project preference.
  - **N/A**: No Gemini feedback was produced.

#### Codex
- **Concern**: Plan added stale/conflicting `--host` and `--poll-ms`, under-specified `/events` replay/live handoff, did not pin sanitized SSE fields, omitted repo-local execution story, and undercovered empty-file/invalid-path/partial-fragment/filter-count behavior.
  - **Addressed**: Plan and implementation removed host/poll CLI flags, defined per-client replay using an append-only event log and condition variable, whitelisted SSE fields, documented `PYTHONPATH=src`, and added tests for those edge cases.

#### Claude
- **Concern**: Test framework mismatch (`pytest` vs repo `unittest`), SSE replay architecture under-specified, host/poll flags confusing, and `webbrowser.open()` fallback not explicit.
  - **Addressed**: Plan switched to `unittest`, clarified replay architecture, removed host/poll flags, and documented best-effort browser opening.

### Plan Phase (Round 2)

#### Codex
- **Concern**: Real JS was not executed, Phase 1 wording was stale for existing files, stale host criterion remained, and hover/live-indicator behavior needed explicit decomposition.
  - **Addressed**: Plan added optional Node-backed JS parity checks, updated Phase 1 wording, removed stale host criterion, and specified hover-linked highlighting plus live badge state machine.

#### Claude
- **Concern**: Mostly approved; noted table column list, stale host criterion, and a tailer buffer type inconsistency.
  - **Addressed**: Plan made table columns explicit, removed stale host criterion, and the tailer buffer reset was corrected during implementation.

### Phase 1 Implementation Review

#### Gemini
- **Concern**: Gemini consultation was skipped per project preference.
  - **N/A**: No Gemini feedback was produced.

#### Codex
- **Concern**: Some Phase 1 contracts were unverified and one CLI behavior did not match the approved plan.
  - **Addressed**: Added/kept tests for empty file, invalid path, directory path, shutdown partial fragments, exact SSE payload whitelist, and concurrent clients; CLI behavior was aligned with no `--host`/`--poll-ms`.

#### Claude
- **Concern**: Non-blocking test hygiene issues.
  - **Addressed**: Test style and assertions were kept consistent with repo `unittest` conventions.

### Phase 2 Implementation Review

#### Gemini
- **Concern**: Gemini consultation was skipped per project preference.
  - **N/A**: No Gemini feedback was produced.

#### Codex
- **Concern**: Noise events were dropped at ingest instead of filtered at snapshot time; Python oracle documented the wrong behavior; aggregator tests were not importable under standard `unittest`; golden snapshots were missing.
  - **Addressed**: Aggregator and oracle now retain noise events, `snapshot(include_noise=False)` filters paths, test imports use `ROOT/tests`, golden snapshots were added, and a noise fixture proves toggling without replay.

### Phase 3 Implementation Review

#### Codex
- **Concern**: Live badge initialized as live for the first ~2 seconds and dropped base CSS class; treemap/breadcrumb tests did not execute real JS; smoke test was too shallow for interactive behavior.
  - **Addressed**: `lastAppendAtMs` now starts as `null`, badge classes preserve `badge`, and Node-backed tests execute real `index.js`, `treemap.js`, and `table.js` helpers. Full DOM click testing remains manual per no-browser-automation constraint.

### Phase 4 Implementation Review

#### Codex
- **Concern**: Documentation used `python -m` without `PYTHONPATH=src`; review/walkthrough artifact was missing; status still showed phase in progress.
  - **Addressed**: `docs/viewer.md` now uses `PYTHONPATH=src python3 -m ...`, this review file captures walkthrough notes, and status is left to porch rather than edited manually.

## Flaky Tests

No flaky tests encountered. No tests were skipped as flaky.

## Follow-up Items

- Repeat the final browser GUI walkthrough before merge in an environment with a real browser.
- Consider adding package metadata so `python -m ai_observe.viewer` works from a checkout without `PYTHONPATH=src`.
- Consider a future browser automation test if the project later accepts a dev dependency such as Playwright.
- Consider a less-noisy committed medium-size fixture or generator for demonstrating the default filtered treemap with interesting non-noise paths.
