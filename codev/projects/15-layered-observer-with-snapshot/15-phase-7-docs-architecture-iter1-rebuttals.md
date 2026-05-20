# Phase 7 rebuttal — iteration 1

## Reviews

- Gemini: `APPROVE`
- Claude: `APPROVE`
- Codex: `REQUEST_CHANGES`

## Codex feedback

### 1. `AI_OBSERVE_SNAPSHOT_EXCLUDE` matching semantics were not documented precisely enough

**Response:** Addressed.

The current documentation already included the required matching model in `docs/observe.md`:

- patterns are matched against normalized root-relative paths
- separators may be `:` or newlines
- `foo/**` matches a root-relative subtree
- `**/*.pyc` matches suffixes anywhere under the watched root
- bare segments/basenames such as `node_modules` match any path segment with that name

To make this even harder to miss, I tightened the environment-variable table entry so the `AI_OBSERVE_SNAPSHOT_EXCLUDE` row now explicitly points readers to the documented matching rules below the table.

### 2. Sensitive-data warning should explicitly mention snapshot/manifest-derived metadata

**Response:** Addressed.

The warning already mentioned manifest-derived metadata, which was intended to cover snapshot metadata. I clarified the wording to say `snapshot/manifest-derived metadata` explicitly so the sensitivity guidance directly matches the phase acceptance language.

## Changes made

- Updated `docs/observe.md` sensitive-data warning to say `snapshot/manifest-derived metadata`.
- Updated the `AI_OBSERVE_SNAPSHOT_EXCLUDE` environment-variable row to explicitly reference the documented matching rules.

## Summary

No functional/code changes were required for this feedback. The requested documentation details are now explicit in `docs/observe.md`.
