# Rebuttal: Plan iteration 1

## Review summary

- Gemini: APPROVE, no key issues.
- Claude: APPROVE, no critical issues; minor notes about Phase 2 sanitizer/oracle interactions and safe writes for `<session>.meta.json`.
- Codex: REQUEST_CHANGES, focused on secure handling for new artifacts and authoritative artifact precedence across timeout/rebuild flows.

I agree with the Codex request changes and Claude minor notes. I updated `codev/plans/15-layered-observer-with-snapshot.md` to make these implementation contracts explicit.

## Codex REQUEST_CHANGES points

### 1. Security/hardening for new artifacts

**Feedback:** The plan did not explicitly require equivalent path-safety, symlink, and permission protections for new artifacts such as `<session>.jsonl.rebuilt`, `<session>.meta.json`, and snapshot-related writes.

**Change made:** Updated Phase 1 and cross-phase notes to require extending `LogPaths` with `.jsonl.rebuilt` and `.meta.json` and protecting all new artifact writes with the same containment, symlink/no-follow, safe-write, and restrictive-permission posture used for existing `.trace`, `.jsonl`, and `.jsonl.partial` artifacts. Added tests for safe-write/permission behavior and symlink attack rejection for new artifacts. Added a risk item for security regression in new artifacts.

### 2. Artifact precedence across timeout/rebuild flows

**Feedback:** The plan did not say exactly which file is authoritative when `.jsonl`, `.partial`, `.rebuilt`, and `.meta.json` coexist, nor how snapshot events are merged in those recovery modes.

**Change made:** Added explicit recovery precedence:

- Normal sessions and successful non-timeout rebuilds use `<session>.jsonl` as the authoritative complete stream.
- Live-timeout sessions use `<session>.jsonl.rebuilt` as the authoritative complete stream; `<session>.jsonl` remains the non-authoritative partial live stream.
- Parser-failure sessions use `<session>.jsonl.partial` as the partial direct-event artifact, while `<session>.jsonl` may hold safe inferred snapshot events or remain an empty placeholder.
- Snapshot events merge into the authoritative event artifact: `<session>.jsonl` for normal/recovered sessions, `<session>.jsonl.rebuilt` for live-timeout rebuilt sessions, and `<session>.jsonl` as inferred-only data for parser-failure sessions.
- `<session>.meta.json` records artifact roles/precedence so CLI diagnostics and the viewer interpret sessions consistently.

Updated Phase 4 success criteria/tests and Phase 5 viewer requirements to follow this precedence and avoid silently merging partial direct evidence with inferred canonical data in the browser.

## Claude minor notes

### Phase 2 sanitizer/oracle interactions

**Feedback:** Adding provenance to sanitized events may affect aggregator/oracle tests before Phase 5.

**Change made:** Updated Phase 2 success criteria and tests to explicitly update the `sanitize_event` whitelist and run `tests.test_viewer_aggregator` during Phase 2 so oracle behavior is addressed early rather than deferred accidentally.

### Safe-write note

Covered by the Codex security changes above.

## Remaining disagreements

None. All REQUEST_CHANGES items were accepted and incorporated.
