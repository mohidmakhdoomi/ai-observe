# Rebuttal — Phase 3 (docs + release checklist), iteration 1

Verdicts: Gemini APPROVE (HIGH), Claude APPROVE (HIGH), Codex REQUEST_CHANGES
(HIGH). Both Codex issues are accepted and fixed (staged in this iteration).

## Codex issue 1 — `docs/observe.md` missing the keep-out-of-commits warning

**Accepted, fixed.** The spec requires the security/privacy docs themselves —
not just the README — to recommend keeping `.codev/observe/` out of commits,
uploads, and public logs until reviewed. `docs/observe.md`'s "Severe
sensitive-data risk" section previously stopped at "Store `.codev/observe/`
carefully."

Fix: that section now reads "Store `.codev/observe/` carefully, and keep it
**out of commits, uploads, and public logs until you have reviewed its
contents**. Redaction is not implemented." This matters doubly because
`docs/observe.md` is the `pyproject.toml` readme, so it is the warning a PyPI
-style consumer would see.

## Codex issue 2 — `RELEASING.md` prerequisite/order mismatch

**Accepted, fixed.** Step 2 (full test run) demands zero skips and names
`setuptools>=77` as a capability, but the tooling was only provisioned inside
step 4 — so on a clean machine matching the intro prerequisites, the
packaging smoke tests would skip during step 2 and the checklist would
contradict itself.

Fix: the intro now provisions the build tooling up front
(`python3 -m pip install --upgrade build "setuptools>=77"`, with a sentence
explaining that step 2's smoke tests need the PEP 517 backend in the running
interpreter), and step 4 references the already-provisioned tooling and runs
only `python3 -m build`. Step ordering is otherwise unchanged.

## Verification

- Link/anchor/CLI-name consistency check re-run mentally against the edits:
  no links or anchors touched; both edits are prose/command-ordering only.
- `RELEASING.md` now has exactly one provisioning point, before the first
  step that depends on it; step 2's zero-skip requirement is satisfiable on a
  machine that follows the document top-to-bottom.
