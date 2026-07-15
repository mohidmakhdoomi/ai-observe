# Releasing ai-observe (local, non-PyPI)

Checklist for cutting a local release from a clean checkout on Linux with
`strace`, Node 20, and Python ≥ 3.10 available. It mirrors what CI does
(`.github/workflows/ci.yml`), so a green CI run on the release commit means
steps 2–6 should hold locally too. Publishing to PyPI is out of scope.

Provision the build tooling up front — the test run in step 2 includes the
packaging smoke tests, which need the PEP 517 backend in the running
interpreter:

```bash
python3 -m pip install --upgrade build "setuptools>=77"
```

Work through the steps in order.

## 1. Version check / bump

The version is single-sourced from `ai_observe.__version__`
(`src/ai_observe/__init__.py`); `pyproject.toml` reads it dynamically.

```bash
grep __version__ src/ai_observe/__init__.py
```

Bump it there (and only there) if this release needs a new version, and
commit the bump before building.

## 2. Full test run

```bash
python3 -m unittest discover -s tests
```

Everything must pass, with **zero skips** — a skip means your environment is
missing a capability the release validation depends on (Node, strace,
ptrace attachability, setuptools ≥ 77).

## 3. CI status

Confirm the CI workflow is green **on the release commit** for all three
matrix legs (3.10 / 3.12 / 3.13) — check the Actions tab or the README
badge. Do not release from a commit CI has not validated.

## 4. Build wheel + sdist

Using the tooling provisioned up front:

```bash
python3 -m build
```

Both `dist/ai_observe-<version>-py3-none-any.whl` and
`dist/ai_observe-<version>.tar.gz` must be produced, with the version from
step 1 in the filenames.

## 5. Inspect wheel / sdist contents

```bash
unzip -l dist/ai_observe-*.whl
tar -tzf dist/ai_observe-*.tar.gz
```

Verify:

- `ai_observe/viewer/static/` assets are present in the wheel (the viewer
  serves them from disk);
- `tests/` is **not** included in the wheel;
- the license files (`LICENSE`, `NOTICE`) are present in the dist-info;
- no stray files (e.g. `.codev/observe/` artifacts) leaked in.

## 6. Clean-venv install from built artifacts

Install the wheel into a fresh venv **outside the checkout** (so imports
can't silently resolve from `src/`), with no network and no deps (the
package has zero runtime dependencies):

```bash
python3 -m venv /tmp/aio-release-venv
/tmp/aio-release-venv/bin/pip install --no-index --no-deps dist/ai_observe-*.whl
```

The sdist path is exercised by the packaging smoke tests, which build and
install both artifacts in clean venvs:

```bash
python3 -m unittest -v tests.test_packaging_smoke
```

## 7. One end-to-end observed command

Using the venv from step 6, run a real observed session **from a directory
outside the checkout** and check the artifacts:

```bash
cd "$(mktemp -d)"
/tmp/aio-release-venv/bin/ai-observe --session release-check -- \
  bash -c 'echo hi > generated.txt'
cat .codev/observe/release-check.jsonl
```

Expect a `create`/`modify` event for `generated.txt` with `"source":
"strace", "confidence": "direct"` (plus snapshot events), and a
`release-check.meta.json` sidecar. Remember these artifacts are sensitive —
delete the temp directory when done.

## 8. Viewer static-asset serving smoke test

Still in the step 7 directory:

```bash
/tmp/aio-release-venv/bin/ai-observe-viewer --no-browser \
  .codev/observe/release-check.jsonl
```

Open the printed `http://127.0.0.1:<port>/` URL (or `curl` it) and confirm
the page and its static assets load (HTTP 200, rendered UI) — this proves
the installed wheel serves the packaged static files. Ctrl-C the server,
then clean up:

```bash
rm -rf /tmp/aio-release-venv
```

Done — tag/branch the release commit as desired.
