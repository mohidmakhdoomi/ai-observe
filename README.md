# ai-observe

[![CI](https://github.com/mohidmakhdoomi/ai-observe/actions/workflows/ci.yml/badge.svg)](https://github.com/mohidmakhdoomi/ai-observe/actions/workflows/ci.yml)

Linux-first layered filesystem observer for wrapped command sessions, with a
local browser viewer.

> ## ⚠️ Severe sensitive-data warning
>
> Every observer artifact — `.trace`, `.jsonl`, `.jsonl.partial`,
> `.jsonl.rebuilt`, `.meta.json` — may contain **absolute paths, the wrapped
> command's argv (including prompts passed on the command line), raw syscall
> text, file metadata, and snapshot diagnostics / sidecar metadata**.
> Redaction is **not** implemented.
>
> Keep `.codev/observe/` **out of commits, uploads, and public logs until you
> have reviewed its contents.** Treat every file in it as sensitive by
> default.

## What it does

`ai-observe` wraps a command and reports every **net** file create, modify,
or delete visible under configured watched roots during the session, by
layering two sources of evidence:

- **`strace`** supplies live, process-tree-scoped direct evidence.
- **Start/end snapshots** over watched roots backstop missed net changes.

Emitted events carry provenance (`source` + `confidence`) so directly
observed changes stay distinct from inferred ones. Activity outside watched
roots, and changes made by remote or hosted agents, are not observed. See
[docs/observe.md](docs/observe.md) for the full runtime model, event schema,
and environment variables.

## Requirements

- **Runtime**: Linux with `strace` installed. `pip install` may succeed on
  non-Linux platforms, but default live observation requires Linux +
  `strace`; missing `strace` or a non-Linux platform raises a clear runtime
  error.
- **Python**: 3.10 or newer.
- **ptrace**: `strace` needs permission to attach. Sandboxes, seccomp
  profiles, or Yama (`kernel.yama.ptrace_scope`) may block it — see
  [Platform and container caveats](#platform-and-container-caveats).

## Install

From a checkout:

```bash
pip install .
```

This installs the `ai_observe` package plus exactly two console scripts:
`ai-observe` and `ai-observe-viewer`. The named tool shims
(`claude`/`codex`/`gemini`/`opencode`/`agy`) are deliberately **not** installed —
see [Named shims](#named-shims-checkout-only-opt-in).

## Quick start

Observe any command:

```bash
ai-observe --session demo -- bash -c 'echo hi > generated.txt'
```

Artifacts land in `.codev/observe/` (relative to the launch cwd, unless
`AI_OBSERVE_DIR` overrides it):

```text
.codev/observe/demo.trace        # raw strace output
.codev/observe/demo.jsonl        # canonical event stream
.codev/observe/demo.jsonl.partial   # partial direct events (parse failure)
.codev/observe/demo.jsonl.rebuilt   # full-trace rebuild (live-timeout recovery)
.codev/observe/demo.meta.json    # warning/diagnostic sidecar + artifact roles
```

The `.partial` / `.rebuilt` files appear only in degraded sessions; the
sidecar records which event artifact is authoritative. **All of these files
are sensitive** — see the warning above.

View a session in the browser:

```bash
ai-observe-viewer .codev/observe/demo.jsonl
# equivalently:
python -m ai_observe.viewer .codev/observe/demo.jsonl
```

The viewer is read-only and binds **only to `127.0.0.1`** (loopback); there
is no remote-bind flag. It strips sensitive fields (raw syscall text, argv,
PIDs, session ids) before anything reaches the page. See
[docs/viewer.md](docs/viewer.md).

## Named shims (checkout-only, opt-in)

The repo ships PATH-interposition shims — `bin/claude`, `bin/codex`,
`bin/gemini`, `bin/opencode`, `bin/agy` — that observe an AI tool transparently under
its own name. They are **not installed by `pip install`** and never will be
by default: as console scripts they would shadow the real tools.

To opt in from a checkout, symlink or copy the shims you want into a
directory you control and prepend it to `PATH`:

```bash
mkdir -p ~/.local/ai-observe-shims
ln -s "$PWD/bin/codex" ~/.local/ai-observe-shims/codex
export AI_OBSERVE_REAL_CODEX="/absolute/path/to/real/codex"
export PATH="$HOME/.local/ai-observe-shims:$PATH"
codex "implement feature"   # now observed
```

Each shim resolves its real executable from `AI_OBSERVE_REAL_<PROGRAM>`
(e.g. `AI_OBSERVE_REAL_CLAUDE` for `bin/claude`). Set an **absolute** path
before prepending to `PATH`, or the shim may recurse into itself. Details in
[docs/observe.md](docs/observe.md#quick-start).

## Platform and container caveats

- **Linux-only runtime**: the default backend shells out to `strace`;
  install can succeed elsewhere but live observation cannot run.
- **ptrace restrictions**: Yama LSM (`kernel.yama.ptrace_scope`), seccomp,
  or sandboxing may deny `strace`. On hosts you control:
  `sudo sysctl kernel.yama.ptrace_scope=0`.
- **Containers**: grant the `SYS_PTRACE` capability and relax the seccomp
  profile (e.g. `docker run --cap-add=SYS_PTRACE --security-opt
  seccomp=unconfined`), in addition to the sysctl above.
- **Troubleshooting fallback**: `AI_OBSERVE_BACKENDS=snapshot` skips strace
  wrapping (inferred net changes only); `AI_OBSERVE_DISABLE=1` bypasses
  observation entirely.

## Limitations

- **Watched roots are the visibility boundary.** Snapshot reconciliation —
  and, in layered mode, direct event emission — only covers configured
  watched roots (`AI_OBSERVE_ROOTS`; defaults to the launch cwd).
- **Snapshot events are inferred and post-hoc**, not a real-time stream, and
  carry no process attribution.
- **Files created and deleted between snapshots can be missed** ([#18](https://github.com/mohidmakhdoomi/ai-observe/issues/18)):
  snapshot reconciliation compares session-boundary states, so an ephemeral
  file that leaves no direct strace event and is gone by the final snapshot
  is not reported. Periodic mid-session reconciliation is a separate,
  unimplemented issue.
- Remote or hosted agent changes that never occur locally are invisible; so
  are already-running helpers outside the traced process tree (snapshot only
  backstops their net effect under watched roots).
- No fanotify / inotify / eBPF backends, no macOS / Windows live tracing,
  and no redaction in this release.

The full list lives in
[docs/observe.md](docs/observe.md#limits-and-non-goals).

## Documentation

- [docs/observe.md](docs/observe.md) — runtime model, backends, watched
  roots, snapshot controls, artifacts, event schema, environment variables,
  troubleshooting.
- [docs/viewer.md](docs/viewer.md) — browser viewer usage, provenance
  badges, artifact banners, privacy posture.
- [docs/agent-sessions.md](docs/agent-sessions.md) — the opt-in live-agent
  testing suite (`tests/agent_sessions`): per-tool prerequisites and auth,
  running it locally, and the known-bug annotations.
- [RELEASING.md](RELEASING.md) — local release checklist.

## Development

```bash
python3 -m unittest discover -s tests
```

CI (GitHub Actions) runs the suite on Python 3.10/3.12/3.13, builds wheel +
sdist, and validates the installed artifact in a clean venv — see
[.github/workflows/ci.yml](.github/workflows/ci.yml).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
