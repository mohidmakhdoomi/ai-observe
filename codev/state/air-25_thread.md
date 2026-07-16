# air-25 — Initial release v0.1.0 (local, non-PyPI)

Protocol: AIR (strict). Issue #25.

## What this project is
Execute the `RELEASING.md` checklist against the release candidate and add a
`CHANGELOG.md` with a factual `0.1.0` entry. No product-code change.

## Release checklist results (RELEASING.md)
Ran on this worktree's HEAD (worktree branch `builder/air-25`), Python 3.14.4,
Node 22, strace present, `kernel.yama.ptrace_scope=1` (allows tracing direct
children — sufficient). Build tooling lives in a dedicated host venv
(`/tmp/aio-host-venv`) because the system python3.14 is PEP 668
externally-managed; this venv stands in for CI's host interpreter.

- **1 Version check** — `__version__ = "0.1.0"` in `src/ai_observe/__init__.py`. No bump needed. PASS.
- **2 Full test run** — `unittest discover -s tests`: 236 tests, `OK`, **zero skips**. PASS.
- **3 CI green** — architect/merge-time check on the final merged commit. Not verifiable pre-merge from here. DEFERRED (as issue specifies).
- **4 Build** — `python -m build` produced `ai_observe-0.1.0-py3-none-any.whl` and `ai_observe-0.1.0.tar.gz`. PASS.
- **5 Inspect artifacts** — wheel contains `viewer/static/` (6 assets) + `LICENSE`/`NOTICE` in dist-info; `tests/` absent from wheel; no stray `.codev/observe/` files. PASS.
- **6 Clean-venv install** — wheel installed `--no-index --no-deps` into fresh venv; both console scripts present; `test_packaging_smoke`: 21 tests `OK`. PASS.
- **7 E2E observed session** — installed `ai-observe` wrapped `bash -c 'echo hi > generated.txt'` outside checkout: got `modify` event (`source: strace`, `confidence: direct`) + snapshot `create` event + `.meta.json` sidecar. PASS.
- **8 Viewer static-asset smoke** — installed `ai-observe-viewer`: root page HTTP 200 (`<title>ai_observe viewer</title>`), `static/style.css` + `static/index.js` HTTP 200. PASS.

All executable steps (1,2,4-8) PASS. Sensitive e2e/venv temp dirs cleaned up.

## Deliverable
- `CHANGELOG.md` — Keep-a-Changelog format, single `0.1.0` entry grouped by
  layered observer / backends / browser viewer / packaging / CI, each line
  attributed to its merged spec. Purely declarative doc → no tests (per AIR
  implement guidance, config/doc-only changes are exempt).

## Notes
- No checklist step failed, so no in-scope fixes were needed.
- `dist/` is gitignored; build artifacts not committed.
- Tagging + GitHub Release are the architect's job post-merge (out of scope).
