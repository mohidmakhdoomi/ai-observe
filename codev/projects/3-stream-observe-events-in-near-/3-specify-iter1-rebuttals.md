# Spec 3 — Rebuttal to iter1 reviews

## Setup

- gemini: APPROVE (placeholder — architect override per 2026-05-13T01:38:05.940Z directed codex+claude only after gemini capacity exhaustion)
- codex: REQUEST_CHANGES
- claude: APPROVE

After three further iterations (iter2/iter3/iter4) reaching final state:
- claude iter4: APPROVE
- codex iter4: COMMENT (non-blocking, comments addressed)

## Codex iter1 issues

### 1. ".jsonl on parser failure" contradiction (`prepare_logs` pre-creates the file)

**Addressed in iter2.** Success criterion 5 now reads: "The `.jsonl` file
(pre-created by `prepare_logs`) ends as a zero-byte file after the run —
even if the live parser had already streamed events to it before raising
`ParserFailure`, the wrapper truncates it via `safe_write_jsonl` with an
empty event list." Single canonical contract.

### 2. Live writer underspecified (`O_NOFOLLOW`, symlink hardening)

**Addressed in iter2.** "Writing JSONL incrementally" now mandates a
`safe_append_jsonl` helper (or equivalent `verify_log_path_safe` +
`O_WRONLY | O_APPEND | O_NOFOLLOW` open). Iter3 added a test
requirement parallel to `test_safe_write_jsonl_rejects_symlink_swap`
for the live append path. Iter4 also added the same hardening to the
live `.trace` reopen.

### 3. EOF / partial-line handling vs post-hoc equivalence

**Addressed in iter2.** "Tailing the trace file" now requires: "If
strace has exited and a final read also yields empty, flush any
buffered trailing fragment to the parser as if a newline had arrived."
This matches `parse_lines` semantics on a file-object that yields the
last line even without a terminal `\n`.

### 4. Wrapper-level integration test missing

**Addressed in iter2.** Test scenarios now include "End-to-end
wrapper integration": fake `strace` shim appends trace lines in
stages with sleeps, test drives via the real `bin/codex` entrypoint,
asserts mid-run visibility and final equivalence. Exercises thread
startup/join, env-knob wiring, full `run()` lifecycle.

## Subsequent codex iter2/iter3/iter4 changes (for trail)

- iter2: live writes reuse canonical
  `json.dumps(..., sort_keys=True, separators=(",", ":"))` helper;
  daemon thread + bounded join + post-hoc-or-skip fallback;
  added `CODEV_OBSERVE_LIVE_JOIN_TIMEOUT` env knob; reconciled
  parser-failure contract; defined cascade for double write failure.
- iter3: explicit acceptance criteria + tests for join timeout and
  live-append symlink-swap; "What changes in code" mentions both new
  hardening helpers.
- iter4 (COMMENT, non-blocking): clarified that timeout branch leaves
  `.jsonl` in its partial state and does **not** write a misleading
  empty `.jsonl.partial`; applied the same path-safety hardening to
  the live `.trace` reopen; fixed success-criteria numbering
  collision (9/10 → 11/12 in non-functional section).

Final state: codex COMMENT (non-blocking), claude APPROVE. Both
custom-override reviewers approve.
