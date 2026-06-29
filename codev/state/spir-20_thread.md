# spir-20 thread — Packaging: make ai-observe installable

Builder: spir-20 | Protocol: SPIR (strict) | Issue #20

## Log

### Specify phase — start
- Project 20, strict mode. Spec file did not exist; writing it from issue #20 (which is
  highly detailed and carries a "License" + "Baked Decisions"-style decisions block).
- Codebase recon:
  - `src/` layout, package `ai_observe`, version in `src/ai_observe/__init__.py` = `0.1.0`.
  - Entry points: `ai_observe.observe:main_generic`, `ai_observe.viewer.__main__:main`.
  - `bin/*` shims (ai-observe, claude, codex, gemini, opencode) all do
    `sys.path.insert(0, ROOT/"src")` then import — checkout-only today.
  - Viewer serves static via `_STATIC_DIR = Path(__file__).resolve().parent / "static"`
    + `path.read_bytes()`. Filesystem-based read → works for wheel installs **iff**
    static files are declared as package data and installed unpacked. This is the
    classic src-layout footgun the hard acceptance criterion guards.
  - Static files: index.html, index.js, aggregator.js, table.js, treemap.js, style.css.
  - Linux/strace gating already exists in `backends/strace.py` (`requires_linux`,
    raises "Linux required for strace backend" / "strace not found").
  - Sensitive-data warning printed unless `AI_OBSERVE_QUIET` env flag set.
- No existing pyproject/setup/LICENSE/NOTICE/MANIFEST.
- Next: draft spec, commit, run `porch done 20` to trigger 3-way review.
