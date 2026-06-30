# Specification: CI + test-reliability + docs/release checklist for ai-observe

## Summary

`ai-observe` is now an installable, Linux-first layered filesystem observer (SPIR A /
#20 merged via PR #22): it has a PEP 621/639 `pyproject.toml`, two console scripts
(`ai-observe`, `ai-observe-viewer`), resilient `bin/*` shims, viewer static shipped as
package data, and a packaging **smoke-test harness** (`tests/test_packaging_smoke.py`)
that builds a real wheel + sdist, installs into a clean venv, and exercises the installed
artifact outside the checkout. The full suite (~234 tests) is green locally.

What it still lacks — and what this spec (**SPIR B of 2**) delivers — is **continuous
validation**, **deterministic tests under a CI matrix**, and **user-facing
documentation + a local release checklist**:

1. **CI (workstream 3)** — a GitHub Actions workflow on standard `ubuntu-latest` VM
   runners across Python `3.10`, `3.12`, `3.13`, that installs `strace` and Node.js 20,
   runs the unittest suite, builds wheel + sdist, installs from the built artifacts into a
   clean venv, and runs the SPIR-A installed-artifact smoke tests **from the installed
   artifact** (not the source tree).
2. **Test reliability (workstream 4)** — replace timing-sensitive `time.sleep()`
   synchronization with polling/retry helpers where practical (or document remaining fixed
   sleeps as intentionally harmless), and make permission/mode tests deterministic under
   varying umasks.
3. **Documentation + release checklist (workstream 6)** — a prominent, security-forward
   root `README.md`; keep `docs/observe.md` and `docs/viewer.md` aligned with packaged
   usage; and a short, local (non-PyPI) release checklist.

This spec **does not** change core observation semantics, packaging metadata, the shim
refactor, or the smoke-test harness itself (those are SPIR A). It does not implement
periodic snapshot reconciliation (#18), alternative tracing backends, redaction, or PyPI
publishing.

## Background and current state

### What SPIR A established (the substrate this builds on)

- Installable package `ai_observe` (`src/` layout), `requires-python >=3.10`, dynamic
  version from `ai_observe.__version__`, Apache-2.0 via PEP 639 (`setuptools>=77`).
- Console scripts: exactly `ai-observe` and `ai-observe-viewer`. The named tool shims
  (`claude`/`codex`/`gemini`/`opencode`) are **checkout-only** `bin/*` shims, deliberately
  **not** entry points (they would shadow real tools).
- Viewer static assets ship as `package-data` and are served from disk; proven by a
  clean-venv, outside-checkout wheel smoke test.
- `tests/test_packaging_smoke.py` — builds wheel + sdist (PEP 517 backend directly, i.e.
  `--no-isolation` semantics, because `build` may be absent under PEP 668), installs into a
  clean venv (`--no-index --no-deps`), and exercises console scripts, `python -m`, viewer
  static serving (the hard criterion), the shim two-path matrix, simulated platform
  failure, and `tests/` exclusion. These are **capability-gated** (skip cleanly when
  offline / no backend provisioning).
- Linux/`strace` gating: install may succeed off-Linux, but live observation requires
  Linux + `strace`; missing `strace` / non-Linux raise clear runtime errors.

### Current gaps this spec closes

- **No CI.** There is no `.github/` directory; nothing validates the package, the suite,
  or the installed artifact automatically, on any Python version.
- **Untested flakiness surface.** The suite uses many `time.sleep()`-based
  synchronizations (viewer server/tailer, live-trace) that are timing-sensitive, and at
  least one umask-dependent permission assertion. Whether these actually flake is only
  revealed by the 3.10/3.12/3.13 matrix and CI's umask.
- **No root README.** `docs/observe.md` is reused as the package `readme`, but there is no
  top-level `README.md` orienting a new user to install, quick start, requirements,
  limitations, and — critically — the **sensitive-artifact risk**.
- **No release checklist.** There is no written, repeatable local procedure for cutting a
  release (version, tests, CI, build, inspect, clean-venv install, end-to-end smoke).

### Observed flakiness candidates (informing, not binding, the plan)

These were found during specify-phase recon. The plan/implement phase will confirm which
actually flake under the matrix; this list is evidence, not a fixed work order.

- **Umask-sensitive (workstream 4):** `tests/test_codex_observe.py` asserts
  `stat.S_IMODE(obs.stat().st_mode) == 0o755` on a directory the **test itself** created
  via `obs.mkdir()`. `mkdir` applies the process umask, so this assertion fails under any
  umask other than `022` (e.g. CI or a developer with `umask 077`). The adjacent `0o600`
  assertions on `.trace` / `.jsonl` are **code-set** (the product chmods them), so those
  are correct to keep and are the deterministic behavior we actually want to protect.
- **Timing-sensitive (workstream 4):** `time.sleep()` synchronizations in
  `tests/test_viewer_server.py`, `tests/test_viewer_tailer.py`,
  `tests/test_live_trace.py`, and `tests/test_viewer_smoke_e2e.py` wait a fixed interval
  for a poller/tailer to observe a change before asserting. These are the prime candidates
  to convert to bounded poll-until-condition helpers.
- **Intentional sleeps (document, do not convert):** `tests/test_codex_observe.py` uses
  `time.sleep(30)` inside a **fake-strace** helper that simulates a long-running child for
  signal-forwarding tests; it is terminated by the test's signal, not awaited. The product
  poll loops (`src/ai_observe/observe.py`, `src/ai_observe/viewer/server.py`) sleep on
  their poll interval by design. These are not flakiness and must not be "fixed".
- **Node-gated parity tests:** `tests/test_viewer_table_js.py`,
  `tests/test_viewer_index_js.py`, `tests/test_viewer_treemap.py`, and
  `tests/test_viewer_aggregator.py` call `shutil.which("node")` and `SkipTest` when absent.
  Without Node in CI they **silently skip**, hiding JS/Python parity regressions — hence
  the Node.js 20 requirement.
- **Loopback HTTP:** viewer/server tests already bind `port=0` (ephemeral) and connect on
  `127.0.0.1`. CI must permit loopback HTTP. The ephemeral-port discipline is already in
  place; the plan should verify no fixed-port server bind remains in test/smoke paths.

## Constraints (fixed by the architect / issue — do not relitigate)

The issue body pins the following decisions. They are treated as **baked** and copied
here verbatim in intent; the spec, plan, and implementation honor them and CMAP reviewers
should not propose alternatives unless the spec fails to satisfy them.

**CI shape:**
- GitHub Actions CI on **standard `ubuntu-latest` VM runners** (not containers).
- Python versions: **`3.10`, `3.12`, `3.13`** (note: `3.11` intentionally omitted).
- Install **`strace`** in CI.
- Install **Node.js 20** so JS helper/parity tests run instead of silently skipping.
- Ensure the environment **allows loopback HTTP** (viewer/smoke tests bind/connect on
  `127.0.0.1`).
- Run the Python **unittest** suite.
- Build **both wheel and sdist**.
- Install **from built artifacts** in a **clean virtualenv**.
- Run smoke tests **from the installed artifact**, not the source tree.
- Prefer standard VM runners for `strace` tests. If containerized runners are introduced
  later, document/configure ptrace support explicitly (`SYS_PTRACE` / seccomp).
- `ubuntu-latest` may restrict ptrace for `strace`; if `strace` fails to attach, set
  `kernel.yama.ptrace_scope=0` (e.g. `sudo sysctl kernel.yama.ptrace_scope=0`) in the
  workflow.
- Static viewer ports can collide under parallel CI; prefer binding an ephemeral port
  (`0`) over fixed ports in server/smoke tests.

**Test reliability:**
- Replace timing-sensitive `time.sleep()` synchronization with polling/retry helpers
  where practical, or document remaining fixed sleeps as intentionally harmless.
- Make permission/mode tests deterministic under varying umasks — either set umask
  explicitly in the test or avoid exact assertions on umask-dependent modes.

**Documentation must include** (workstream 6): a very prominent sensitive-data warning
near the top; what the tool does; install instructions; quick start; artifact locations;
packaged CLI usage; the checkout-only opt-in named-shim workflow (symlink/copy `bin/*`
into a user-controlled dir and prepend to `PATH`), documented **without** installing named
shims by default; Linux/`strace`/ptrace/container caveats; loopback-only viewer behavior;
watched-roots and snapshot limitations; the **#18 limitation** (snapshot reconciliation is
inferred/post-hoc and does not perfectly capture files created/deleted between snapshots);
and a **severe sensitive-data warning** for `.trace`, `.jsonl`, `.jsonl.partial`,
`.jsonl.rebuilt`, `.meta.json`. Security/privacy docs must warn that artifacts may contain:
absolute paths; command argv and prompts passed on the command line; raw syscall text;
file metadata; snapshot diagnostics and sidecar metadata. Recommend keeping
`.codev/observe/` out of commits, uploads, and public logs until reviewed. Explain that
install may succeed on non-Linux but default live observation requires Linux + `strace`.
Keep `docs/observe.md` and `docs/viewer.md` aligned with packaged usage.

**Release checklist** (workstream 6): version check/bump; full test run; CI status; wheel
+ sdist build; wheel/sdist content inspection; clean-venv install from built artifacts; one
end-to-end observed command; viewer static-asset serving smoke test.

**Process:** Plan phases ship as git commits within a **single PR** (not a PR per phase),
opened during/after the final implement phase unless the architect requests an earlier PR.
Never use `git add -A` / `git add .` — stage files explicitly.

## Stakeholders

- **Maintainer (the author):** wants a green CI gate on every push/PR, a deterministic
  suite that doesn't flake on the matrix, and a repeatable local release procedure.
- **New users / evaluators:** need a README that gets them installed and productive fast,
  and — non-negotiably — that makes the sensitive-data risk impossible to miss.
- **Contributors:** need the suite to pass deterministically regardless of their umask and
  whether they have Node/strace installed.
- **CMAP reviewers (Gemini/Codex/Claude):** verify the workflow is correct and the
  reliability fixes are behavior-preserving.

## Solution exploration

### Workstream 3 — CI

**Approach A (chosen): single workflow, matrix job, two ordered stages per matrix leg.**
One workflow file (`.github/workflows/ci.yml`) triggered on `push` and `pull_request`. A
matrix over `python-version: ["3.10", "3.12", "3.13"]` on `ubuntu-latest`. Each leg:

1. Checkout; `actions/setup-python` (matrixed); `actions/setup-node` (v20).
2. Install OS deps: `sudo apt-get update && sudo apt-get install -y strace`.
3. Lower ptrace restriction so `strace` can attach:
   `sudo sysctl kernel.yama.ptrace_scope=0` (with a guard/echo if the key is absent).
4. Provision the build/test toolchain in the job venv (e.g. `pip install build` +
   whatever the smoke harness needs to build without isolation).
5. **Run the unittest suite** from the source tree (`python -m unittest discover -s tests`
   or the repo's existing invocation) — this is where Node-gated parity tests and
   loopback/strace tests run for real.
6. **Build** wheel + sdist, **install from the built artifact into a clean venv**, and run
   the **installed-artifact smoke tests** (`tests/test_packaging_smoke.py`) so they
   execute against the real install rather than skipping. The smoke harness already builds
   internally; the workflow's job is to ensure its capability gates are *satisfied* (build
   tooling present, online or pre-provisioned) so the smoke assertions actually run on at
   least the matrix legs, rather than silently skipping.

- **Pros:** one source of truth; matrix gives the flakiness signal the issue wants;
  mirrors the real user path (build → install → run installed).
- **Cons:** must ensure the smoke tests don't *skip* in CI (the whole point is to run them
  against the artifact); ptrace/sysctl handling must be robust across runner images.
- **Risk:** runner image changes (apt, sysctl availability). Mitigated by guarding the
  sysctl call and pinning action major versions.

**Approach B (rejected): two separate workflows (lint/test vs build/package).** More
files, duplicated setup, and splits the "matrix reveals flakiness" signal. Rejected for
this scope; the issue asks for one coherent CI that does both.

**Decision points for the plan (not the spec):** exact unittest invocation (match repo
convention), whether to add a lightweight lint/format step (issue does not require it — out
of scope unless trivial), and how to make the smoke harness's capability gates evaluate to
"run" in CI without weakening their local skip behavior.

### Workstream 4 — test reliability

**Approach (chosen): a small shared `poll_until(...)` test helper + targeted umask
determinism.**

- Add a bounded retry/poll helper (in a shared test util, e.g. `tests/_util.py` or
  reuse/extend an existing helper if one exists) that repeatedly checks a predicate until
  true or a generous timeout elapses, sleeping a short interval between checks. Convert the
  timing-sensitive viewer/tailer/live-trace synchronizations from "sleep a fixed guess,
  then assert" to "poll until the condition holds (or fail with a clear timeout)". This is
  strictly more reliable (tolerates slow CI) and usually faster (returns as soon as the
  condition holds).
- For umask determinism: fix the `test_codex_observe.py` directory-mode assertion. Two
  acceptable strategies per the constraint — (a) set umask explicitly within the test
  around the `mkdir` so the asserted mode is deterministic, or (b) stop asserting the exact
  umask-dependent mode of a **test-created** directory (assert only the modes the product
  actually sets, i.e. the `0o600` artifact perms). The plan picks one; the bias is toward
  (b) for test-created dirs and (a) only where the test is genuinely validating
  product-set directory perms. Audit all `umask`/`st_mode`/`chmod` assertions to confirm
  none of the kept ones depend on ambient umask.
- Document the intentional sleeps (`fake-strace` long-runner; product poll loops) inline
  or in the review's reliability notes so they aren't mistaken for flakiness later.

**Behavior-preservation requirement:** these are test-and-harness changes only. No change
to product timing semantics, observation behavior, or what the suite *asserts about the
product* (other than removing assertions that were testing the test's own umask rather than
the product).

### Workstream 6 — documentation + release checklist

**Approach (chosen): new root `README.md` as the user front door; align existing docs;
add `RELEASING.md` (or a checklist section) for the release procedure.**

- `README.md`: lead with the severe sensitive-data warning (near the very top, visually
  prominent), then what the tool does, install, quick start (packaged CLI usage:
  `ai-observe ... -- <cmd>`, `ai-observe-viewer <jsonl>`, `python -m ai_observe.viewer`),
  artifact locations, the **checkout-only opt-in named-shim workflow** (symlink/copy
  `bin/*` into a user dir + prepend `PATH`; explicitly note shims are not installed by
  default), Linux/`strace`/ptrace/container caveats, loopback-only viewer behavior,
  watched-roots + snapshot limitations including the #18 caveat, and the full
  security/privacy artifact-contents warning with the "keep `.codev/observe/` out of
  commits/uploads/public logs" recommendation and the off-Linux-install /
  Linux-only-runtime note.
- Keep `docs/observe.md` and `docs/viewer.md` aligned with packaged usage (they are
  already largely accurate; reconcile any checkout-only phrasing — e.g. `bin/ai-observe`
  examples — with the installed `ai-observe` console script, while preserving the
  checkout-shim instructions where they're the intended path).
- Release checklist: a short, ordered, **local** (non-PyPI) procedure covering version
  check/bump, full test run, CI status, wheel + sdist build, wheel/sdist content
  inspection, clean-venv install from built artifacts, one end-to-end observed command, and
  the viewer static-asset serving smoke test. Location (`RELEASING.md` vs a README section
  vs `docs/`) is a plan-phase decision; a dedicated `RELEASING.md` is the bias.
- **Note on `pyproject.toml` `readme`:** it currently points at `docs/observe.md`. If the
  plan decides the new root `README.md` should be the package long-description, that is a
  one-line metadata change — but changing packaging metadata edges into SPIR A's territory,
  so the default is to **leave `readme` pointing at `docs/observe.md`** unless the architect
  approves repointing it. Flag, don't silently change.

## Open questions

**Critical (block progress) — none.** The issue is fully specified; defaults below resolve
the rest.

**Important (affect design):**
1. Should CI **fail** if the installed-artifact smoke tests *skip* (e.g. build tooling
   unexpectedly missing) rather than run? The intent of the issue is that they *run* in CI.
   Default: make the CI environment satisfy the smoke harness's capability gates so they
   run; if they cannot, surface it loudly (don't let a silent skip count as success).
   Resolve in plan.
2. Where does the release checklist live — `RELEASING.md`, a `README.md` section, or
   `docs/`? Default: `RELEASING.md`. Easily changed.
3. Should `pyproject.toml`'s `readme` be repointed to the new root `README.md`? Default:
   **no** (stay in SPIR-B scope); flag to architect if it seems clearly right.

**Nice-to-know:**
4. Add an optional lint/format/type step to CI? Default: **no** (not required by the
   issue; out of scope unless trivial and uncontroversial).
5. Add a CI status badge to the README? Default: yes if it's a trivial, accurate addition
   after the workflow name is fixed.

## Success criteria (acceptance)

Derived directly from the issue's acceptance criteria. Each is verifiable.

**CI (must):**
- [ ] A GitHub Actions workflow runs on standard `ubuntu-latest` VM runners with Python
      `3.10`, `3.12`, and `3.13` (matrix).
- [ ] CI installs `strace` and Node.js 20.
- [ ] The CI environment supports loopback HTTP tests (viewer/smoke bind/connect on
      `127.0.0.1`).
- [ ] CI builds wheel + sdist, installs from the built artifacts into a clean venv, and
      runs the **installed-artifact smoke tests from SPIR A** — actually executing them
      (not skipping) on the matrix.
- [ ] CI passes the unittest suite **and** the packaging smoke tests across the matrix.
- [ ] ptrace is handled so `strace` tests run (sysctl set when needed); the workflow
      documents the container/`SYS_PTRACE` caveat for future containerized runners.

**Test reliability (must):**
- [ ] Timing-sensitive test sleeps are replaced with polling/retry helpers where
      practical, **or** remaining fixed sleeps are documented as justified/harmless.
- [ ] Umask-sensitive permission tests are made deterministic (explicit umask) or avoid
      exact umask-dependent assertions; specifically, the `test_codex_observe.py`
      directory-mode assertion no longer depends on ambient umask.
- [ ] No reduction in meaningful product coverage; product-set-permission assertions
      (`0o600` artifacts) are preserved.

**Documentation + release (must):**
- [ ] Root `README.md` exists and clearly explains install/use, requirements, limitations,
      and sensitive-artifact risk, with a very prominent sensitive-data warning near the
      top and the full security/privacy artifact-contents warning.
- [ ] README documents the checkout-only opt-in named-shim workflow without implying shims
      are installed by default.
- [ ] README covers Linux/`strace`/ptrace/container caveats, loopback-only viewer
      behavior, watched-roots + snapshot limitations, and the #18 limitation.
- [ ] `docs/observe.md` and `docs/viewer.md` are aligned with packaged usage.
- [ ] A local release checklist exists covering all the listed steps.

**Regression (must):**
- [ ] Existing tests continue to pass (all ~234, plus any new helper-based tests), with no
      change to the product promise around snapshot limitations (#18 stays separate).

### Test scenarios

- Push a branch / open a PR → CI runs all three matrix legs green, including Node-gated
  parity tests and `strace`-backed tests actually executing (visible in logs, not skipped).
- The packaging smoke job builds wheel + sdist, installs into a clean venv, and the
  installed-artifact smoke assertions run and pass.
- Run the suite locally under `umask 077` and `umask 022` → identical pass result (the fix
  is verified by the very condition that exposed the bug).
- A new user follows only the README: installs, runs an observed command, opens the viewer,
  and is warned about sensitive artifacts before producing any.

## Non-goals

- Anything in SPIR A: packaging metadata, console scripts, the shim refactor, or the
  smoke-test harness itself. (Repointing `pyproject` `readme` is explicitly flagged, not
  assumed.)
- Periodic snapshot reconciliation (#18).
- Replacing `strace` with fanotify / inotify / eBPF.
- Redaction / safe telemetry export.
- Publishing to PyPI (the release checklist is **local**).
- Containerized CI runners (only documented as a future caveat, not implemented).
- New product features or changes to observation semantics.

## Consultation Log

_(Populated during the 3-way consultation that porch runs after this draft. Reviewer
feedback from Gemini / Codex / Claude and the resulting changes will be summarized here.)_
