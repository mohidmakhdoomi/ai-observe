# Rebuttal: Specify iteration 1

## Review summary

- Gemini: APPROVE, with plan-phase suggestions around symlink traversal, synchronous start snapshots, and truncated trace recovery.
- Codex: REQUEST_CHANGES, focused on underspecified warning/artifact semantics, snapshot metadata-only semantics, concrete excludes, deterministic deduplication, and rebuilt/partial artifact naming.
- Claude: COMMENT, focused on `O_CREAT` heuristics, `AI_OBSERVE_NESTED`, schema-v2 tailer acceptance, overlapping roots, and resolving important open questions.

I agree with the substantive feedback. I updated `codev/specs/15-layered-observer-with-snapshot.md` to make these areas deterministic enough for planning and implementation.

## Codex REQUEST_CHANGES points

### 1. Warning/status semantics were ambiguous

**Feedback:** The spec required warnings/status for skipped roots, cap exceedance, unreadable paths, hash errors, and partial/rebuilt artifacts without defining whether these are JSONL records, sidecar metadata, or viewer-derived state.

**Change made:** Added a concrete sidecar contract:

- `<session>.jsonl` is the canonical event stream.
- `<session>.jsonl.partial` contains parser-failure partial events.
- `<session>.jsonl.rebuilt` contains a full-trace rebuild after live timeout leaves canonical JSONL potentially partial.
- `<session>.meta.json` records warnings, skipped/overlapping roots, cap exceedance, hash errors, and artifact relationships.

Session diagnostics now belong in `<session>.meta.json`, not as fake filesystem mutation events. The viewer should read the sidecar and show non-sensitive banners.

### 2. Snapshot metadata-only changes were underspecified

**Feedback:** The manifest includes mode/ctime/xattr-related inputs, but snapshot requirements only mandated create/modify/delete.

**Change made:** Defined snapshot event classification:

- `modify` for content indicators: size, mtime_ns, or enabled hash changes.
- `metadata` for type, mode, symlink target, or comparable non-content stat changes.
- `ctime` alone is not sufficient unless paired with a known metadata/content field change.

### 3. Built-in excludes were subjective

**Feedback:** “lock files where appropriate” was not testable and could hide real project artifacts.

**Change made:** Replaced subjective language with concrete defaults: path segments `.git`, `node_modules`, `__pycache__`; subtree `.codev/observe/**`; suffixes `**/*.pyc`, `**/*.pyo`, `**/*.swp`, `**/*.swo`, `**/*~`; basenames `.DS_Store`, `.nfs*`. The spec now explicitly says not to exclude project lockfiles such as `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `Cargo.lock`, `Pipfile.lock`, or generic `*.lock` by default.

### 4. Deduplication/correlation was too loose

**Feedback:** “same normalized operation/path” and “operation class differs materially” were not deterministic.

**Change made:** Added concrete dedup rules:

- Normalize paths as absolute lexical paths after watched-root resolution.
- Operation groups are `create`, `modify`, `delete`, `metadata`, `rename`.
- Suppress snapshot `create`, `delete`, or `metadata` only when a direct same-operation/same-path event exists.
- Suppress snapshot `modify` when direct `modify` or direct `create` exists for the same path.
- Suppress snapshot `rename` only for a direct rename with identical old/new paths.
- Do not suppress snapshot deletes because of direct modify/create.
- Rename detection requires strong object identity or a direct rename; otherwise represent delete/create.

### 5. Rebuilt/partial artifact naming was incomplete

**Feedback:** Existing `.jsonl`, `.jsonl.partial`, and rebuild behavior needed one consistent contract.

**Change made:** Added the artifact contract listed above and required viewer discovery of sibling `.partial`, `.rebuilt`, and `.meta.json` artifacts. Acceptance criteria now require tests for this exact state and truncated final-line/unfinished-syscall tolerance.

## Claude COMMENT points addressed

### `schema_version: 2` tailer acceptance

**Change made:** The schema section now explicitly says the current viewer tailer must accept `schema_version: 2` events rather than rejecting every non-1 event, while continuing to normalize v1/missing provenance as `strace/direct`.

### `O_CREAT` without `O_EXCL`

**Change made:** Tightened the parser requirement to avoid false direct creates. Direct strace create events are required for `creat` and `O_CREAT|O_EXCL` when the target is known. For non-`O_EXCL` `O_CREAT`, strace alone must not emit a direct create unless implementation has a reliable pre-open existence signal; otherwise snapshot reconciliation emits the inferred create when manifests show absent→present.

### `AI_OBSERVE_NESTED=1` semantics

**Change made:** Clarified that the outer observer sets `AI_OBSERVE_NESTED=1` in the traced child environment automatically. Inner shims seeing it direct-exec the resolved binary. This is different from user-facing `AI_OBSERVE_DISABLE=1`, which disables observation for a user-invoked command.

### Overlapping roots

**Change made:** Added canonicalization/de-duplication of overlapping roots. If both `/repo` and `/repo/src` are configured, keep the ancestor, skip the descendant, and record a warning in `<session>.meta.json`.

### Open questions resolved

**Change made:** Resolved the key open questions directly in the spec:

- Exclude syntax: newline- or colon-separated glob patterns over root-relative paths plus basename/segment matching.
- Baseline timing: synchronous start manifest before child launch.
- Backend selection: prefer `AI_OBSERVE_BACKENDS`; CLI `--backend` can be deferred.

## Gemini suggestions addressed

- Symlink traversal is now covered by root canonicalization without following arbitrary symlink subtrees, plus the existing requirement not to follow symlink loops.
- Start snapshot race is resolved by requiring synchronous baseline capture before child launch.
- Partial trace recovery now explicitly must tolerate truncated final lines or unfinished syscalls.

## Remaining open questions

No critical open questions remain. The only remaining important/nice-to-know items are UI/product refinements that can be decided during planning without changing architecture: source-composition display location, hashing recommendations, and future positioning of snapshot-only mode.
