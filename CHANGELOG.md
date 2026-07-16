# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-16

Initial release of `ai-observe`: a Linux-first layered filesystem observer for
wrapped command sessions, with a local browser viewer. Cut via the local
(non-PyPI) release process in [`RELEASING.md`](RELEASING.md).

### Layered observer

- Wrap a command and report every net file create, modify, or delete visible
  under configured watched roots during the session (specs 1, 15).
- Generic command-observer core that wraps arbitrary commands, with optional
  checkout-only named shims (`claude`/`codex`/`gemini`/`opencode`); the shims
  are deliberately not installed by the package (spec 11).
- Provenance model: every event carries `source` and `confidence` fields so
  directly observed changes stay distinct from inferred ones, alongside a
  versioned event schema and per-session JSONL artifacts plus a `.meta.json`
  sidecar (spec 15).

### Event backends

- `strace` backend supplying live, process-tree-scoped direct evidence of
  filesystem syscalls (specs 1, 15).
- Start/end snapshot backend over watched roots that backstops net changes the
  live trace missed (spec 15).
- Clear runtime error when `strace` is unavailable or the platform is
  unsupported, rather than silent no-op observation (specs 11, 20).

### Browser viewer

- Local browser viewer (`ai-observe-viewer`) that renders filesystem-event
  JSONL, serving its own packaged static assets from disk (spec 5).
- Near-real-time streaming of observe events into the viewer as a session runs
  (spec 3).
- Configurable, reversible client-side filters for the event view (spec 7).
- Performance work on the viewer's event-processing path (spec 9).

### Packaging and distribution

- `pyproject.toml` src-layout package with the version single-sourced from
  `ai_observe.__version__`; installs the `ai_observe` package and exactly two
  console scripts, `ai-observe` and `ai-observe-viewer`, with zero runtime
  dependencies (spec 20).
- Wheel bundles the viewer's `viewer/static/` assets and the `LICENSE` /
  `NOTICE` files; `tests/` is excluded from the wheel (spec 20).
- Packaging smoke tests that build and install both wheel and sdist into clean
  venvs outside the checkout and exercise the installed artifact (spec 20).

### Continuous integration and release

- CI workflow across Python 3.10 / 3.12 / 3.13 running the full unit suite and
  the packaging smoke tests, provisioning Node, `strace`, and `setuptools`, and
  failing loud on any test skip so a lost capability cannot pass silently
  (spec 21).
- `RELEASING.md` local (non-PyPI) release checklist covering version check,
  full test run, CI confirmation, build, artifact inspection, clean-venv
  install, an end-to-end observed session, and a viewer static-asset smoke test
  (spec 21).

[0.1.0]: https://github.com/mohidmakhdoomi/ai-observe/releases/tag/v0.1.0
