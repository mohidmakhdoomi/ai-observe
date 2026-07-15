# Architecture Notes

## Layered observer architecture

`ai-observe` is a command-oriented observer with a **layered backend model**.
The default user-facing behavior is low-friction and no-root:

1. `strace` provides live, process-tree-scoped direct evidence.
2. session-boundary snapshots over watched roots provide inferred net-change reconciliation.
3. raw events carry provenance so direct and inferred evidence can coexist safely.
4. the browser viewer remains local-only and privacy-preserving.

This architecture exists to improve completeness without pretending that any single live Linux backend sees every mutation.

### Product boundary

The supported promise is limited to:

- net creates / modifies / deletes
- under configured watched roots
- during the observed session
- on the local filesystem

Out of scope:

- remote or hosted agents that do not touch the local watched filesystem
- byte-level attribution for `mmap`
- fanotify / inotify / eBPF in this release

## Backend abstraction

`src/ai_observe/backends/` defines the first concrete backend seam.

Key pieces:

- `BackendCapabilities`
- `BackendState`
- `BackendSession`
- `Backend` protocol (`prepare`, `stop`, `finalize`)

Current concrete backends:

- `StraceBackend`
- `SnapshotBackend`

### Ordering invariants

The orchestration order is deliberate:

- **prepare order**: `snapshot`, then `strace`
- **finalize order**: `strace`, then `snapshot`

Why:

- snapshot baseline must complete before the child command launches
- strace must finalize first so snapshot deduplication can compare inferred events against the authoritative direct stream and artifact choice already determined by parser recovery

### Backend selection

`AI_OBSERVE_BACKENDS` is the public selection surface.

Supported values:

- `strace,snapshot` (default)
- `strace`
- `snapshot`

Invalid names fail before child launch.

The abstraction is intentionally small: it is concrete enough to support future fanotify / inotify / eBPF implementations later without forcing the viewer or event pipeline to be rewritten now.

## Generic command observer core

`src/ai_observe/observe.py` owns the shared wrapper concerns around the backend layer:

- real-executable resolution for named shims and generic mode
- safe observe-directory and artifact creation
- signal forwarding and exit-code normalization
- parser recovery artifact handling
- compatibility env-var aliases
- final meta-sidecar emission

Important invariants:

- named shims in `bin/codex`, `bin/claude`, `bin/gemini`, and `bin/opencode` are thin launchers over the same generic core
- `src/ai_observe/codex_observe.py` remains a compatibility alias to the generic module so existing imports and monkeypatch-heavy tests still hit the real implementation
- public configuration prefers `AI_OBSERVE_*`, with `CODEV_OBSERVE_*` aliases preserved where documented
- resolver logic must avoid recursive execution of observer shims

## Artifact contract

The observer may produce these sibling artifacts:

- `<session>.trace`
- `<session>.jsonl`
- `<session>.jsonl.partial`
- `<session>.jsonl.rebuilt`
- `<session>.meta.json`

Sidecar responsibilities:

- record parser status
- record artifact roles / authoritative event path
- summarize warnings
- summarize snapshot completeness / diagnostic counts

This keeps session-wide diagnostics out of the event stream itself.

## Provenance model

Schema-v2 raw events add:

- `schema_version: 2`
- `source`
- `confidence`

Current provenance mapping:

- direct strace event → `source: "strace"`, `confidence: "direct"`
- inferred snapshot event → `source: "snapshot"`, `confidence: "inferred"`

Viewer consumers normalize schema-v1 events as `strace` / `direct` so existing artifacts remain usable.

## Browser viewer invariants

The browser viewer continues to prioritize privacy and local-only access.

Key invariants:

- bind only to `127.0.0.1`
- never expose raw syscall text, argv, PID/process details, session ids, or unsanitized snapshot manifests to the page
- treat provenance, artifact state, and warning counts as safe display metadata
- preserve existing path-filter, rename, metric, and backlog semantics while layering in source visibility toggles and session banners

The browser aggregation model keeps filtering client-side:

- sanitized events are retained in arrival order
- path-filter changes replay that buffer
- source-visibility changes replay that buffer
- rebuild / partial artifact switching reconnects to the selected artifact stream, not to raw trace data

## Packaging and distribution

`ai-observe` is an installable package (Spec 20), not only a checkout:

- PEP 621 metadata in `pyproject.toml`, `setuptools` backend, `src/` layout,
  `requires-python >=3.10`. Version is single-sourced from `ai_observe.__version__`
  via `[tool.setuptools.dynamic]`.
- License is **Apache-2.0** declared with PEP 639 (`license = "Apache-2.0"`,
  `license-files = ["LICENSE", "NOTICE"]`); the build backend is pinned `setuptools>=77`
  so the SPDX expression resolves. No legacy `License ::` classifier.
- Default console scripts are **only** `ai-observe` (`ai_observe.observe:main_generic`)
  and `ai-observe-viewer` (`ai_observe.viewer.__main__:main`). The named tool shims
  (`claude`/`codex`/`gemini`/`opencode`) are deliberately **not** entry points — they would
  shadow real tools — and remain checkout-only `bin/*` scripts.
- The checkout `bin/*` shims prefer the installed package and fall back to splicing the
  checkout `src/` onto `sys.path` only when the top-level package is absent
  (`except ModuleNotFoundError` with `exc.name == "ai_observe"`), so a broken/incomplete
  install surfaces instead of being masked.
- Viewer static assets (`viewer/static/*`) ship as `package-data`. Serving still reads them
  from disk via `Path(__file__).parent/"static"`; setuptools installs wheels unpacked, so
  this resolves in an installed wheel without `importlib.resources`. The guarantee is held by
  a smoke test that serves `/static/*` from a clean-venv wheel install **outside** the
  checkout (the src-layout `package_data` footgun).
- Installation may succeed off-Linux; live observation stays Linux+`strace`-only and fails
  with a clear runtime error (see Backend selection). `tests/` ships in the sdist but is
  excluded from the wheel.

## Continuous integration

`.github/workflows/ci.yml` (Spec 21) validates every push/PR on `ubuntu-latest` VM
runners across Python 3.10/3.12/3.13 (`fail-fast: false`):

- Provisions Node 20 (JS parity tests are Node-stdlib-only; no `npm install`), `strace`
  via apt, a guarded `kernel.yama.ptrace_scope=0` sysctl, and `build` + `setuptools>=77`
  (pyproject's PEP 639 backend; the smoke harness invokes `setuptools.build_meta` against
  the host interpreter without isolation).
- Runs tests in **two steps**: the main suite from `tests/` cwd via an explicit module
  list that excludes `test_packaging_smoke` (avoids double-building; keeps `tests/_util.py`
  resolvable without leaking `PYTHONPATH=src` into the smoke step's clean venvs), then the
  packaging smoke module alone after `python -m build`.
- **Fail-loud-on-skip**: both steps tee verbose unittest output and fail the job if it
  matches `\.\.\. skipped|skipped=[0-9]` — anchored to unittest's own markers because test
  *names* can legitimately contain the word "skipped". Every skip gate in the suite is a
  capability CI provisions, so any skip means silently lost coverage.
- Runners are standard VMs; containerized runners would additionally need `SYS_PTRACE`
  and a relaxed seccomp profile (documented in the workflow header).

## Deferred kernel backends

The backend seam is specifically meant to keep future options possible:

- fanotify
- inotify
- eBPF

They are deferred until there is a concrete reason to pay the privilege, portability, or kernel-version cost.
The default release remains `strace` plus snapshot reconciliation.
