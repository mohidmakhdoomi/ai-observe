"""Packaging smoke tests (Spec 20, Phase 3).

These tests operate on REAL built artifacts and the INSTALLED package — not the
checkout ``src/``. A module-scoped fixture builds the wheel + sdist once (from a
copy of the source inputs, standing in for a fresh clone), then installs the
wheel into a fresh virtualenv. Tests then exercise the installed console scripts,
``python -m ai_observe.viewer``, and the viewer's static-asset serving from
*outside* the checkout.

Network/offline: artifacts are built in the host (where the build backend is
present) and the wheel is installed with ``--no-index --no-deps`` (the project
has zero runtime dependencies), so no PyPI access is required.

Capability gates: tests that need Linux + ``strace`` skip with a clear reason
elsewhere; the unsupported-platform path is covered by a simulated unit test.
"""

from pathlib import Path
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
STATIC_FILES = {"index.html", "index.js", "aggregator.js", "table.js", "treemap.js", "style.css"}

# The in-process simulated-platform test imports ai_observe from the checkout.
# (Subprocess tests below use the installed venv / clean envs and are unaffected.)
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Populated by setUpModule(); torn down by tearDownModule().
_STATE: dict = {}


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], text=True, capture_output=True, **kw)


def _clean_env(**overrides) -> dict:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)  # prove no reliance on PYTHONPATH=src
    env.update({k: v for k, v in overrides.items()})
    return env


def _copy_source(dest: Path) -> None:
    """Copy the minimal build inputs (a stand-in for a fresh clone)."""
    dest.mkdir(parents=True)
    for f in ("pyproject.toml", "LICENSE", "NOTICE"):
        shutil.copy(ROOT / f, dest / f)
    shutil.copytree(
        ROOT / "src", dest / "src",
        ignore=shutil.ignore_patterns("*.egg-info", "__pycache__", "*.pyc"),
    )
    (dest / "docs").mkdir()
    shutil.copy(ROOT / "docs" / "observe.md", dest / "docs" / "observe.md")


def _build_one(srcdir: Path, distdir: Path, kind: str) -> None:
    # Build via the PEP 517 backend directly (equivalent to `python -m build
    # --no-isolation`); the `build` frontend is not assumed to be installed.
    # Each kind runs in its OWN subprocess: calling build_sdist and build_wheel
    # in a single interpreter leaves the second artifact unwritten due to
    # in-process setuptools/distutils state.
    snippet = (
        "import os, sys\n"
        "from setuptools import build_meta as b\n"
        "os.chdir(sys.argv[1])\n"
        f"print(b.build_{kind}(sys.argv[2]))\n"
    )
    proc = _run([sys.executable, "-c", snippet, srcdir, distdir])
    if proc.returncode != 0:
        raise RuntimeError(f"{kind} build failed:\n{proc.stdout}\n{proc.stderr}")


def _build_artifacts(srcdir: Path, distdir: Path) -> None:
    _build_one(srcdir, distdir, "sdist")
    _build_one(srcdir, distdir, "wheel")


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / "python"


def _venv_script(venv: Path, name: str) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / name


def setUpModule() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="aio-pkg-smoke-"))
    _STATE["tmp"] = tmp
    try:
        src = tmp / "source"
        _copy_source(src)
        dist = tmp / "dist"
        dist.mkdir()
        _build_artifacts(src, dist)
        _STATE["src"] = src
        _STATE["dist"] = dist
        _STATE["wheel"] = next(dist.glob("*.whl"))
        _STATE["sdist"] = next(dist.glob("*.tar.gz"))

        venv = tmp / "venv"
        r = _run([sys.executable, "-m", "venv", venv])
        if r.returncode != 0:
            raise RuntimeError(f"venv creation failed:\n{r.stdout}\n{r.stderr}")
        py = _venv_python(venv)
        r = _run([py, "-m", "pip", "install", "--no-index", "--no-deps", _STATE["wheel"]])
        if r.returncode != 0:
            raise RuntimeError(f"wheel install failed:\n{r.stdout}\n{r.stderr}")
        _STATE["venv"] = venv
        _STATE["py"] = py
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def tearDownModule() -> None:
    tmp = _STATE.get("tmp")
    if tmp and tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)


def _sample_jsonl(directory: Path) -> Path:
    p = directory / "sample.jsonl"
    p.write_text(
        json.dumps({"schema_version": 2, "path": "/tmp/x", "operation": "create", "source": "strace"}) + "\n",
        encoding="utf-8",
    )
    return p


class _ViewerProc:
    """Start a viewer subprocess, parse its bound URL from stderr, allow HTTP
    GETs, and always shut it down."""

    _URL_RE = re.compile(r"at (http://127\.0\.0\.1:\d+)")

    def __init__(self, argv: list, cwd: Path):
        self.proc = subprocess.Popen(
            [str(a) for a in argv],
            cwd=str(cwd),
            env=_clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.url = self._read_url(deadline=15.0)

    def _read_url(self, deadline: float) -> str:
        end = time.time() + deadline
        seen = ""
        while time.time() < end:
            if self.proc.poll() is not None:
                seen += self.proc.stderr.read() or ""
                raise RuntimeError(f"viewer exited early (rc={self.proc.returncode}):\n{seen}")
            ready, _, _ = select.select([self.proc.stderr], [], [], 0.25)
            if ready:
                line = self.proc.stderr.readline()
                seen += line
                m = self._URL_RE.search(line)
                if m:
                    return m.group(1)
        self.stop()
        raise RuntimeError(f"viewer did not report a URL within {deadline}s:\n{seen}")

    def get(self, path: str):
        last = None
        for _ in range(20):  # bounded retry while the server finishes binding
            try:
                with urllib.request.urlopen(self.url + path, timeout=5) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()
            except urllib.error.URLError as exc:  # connection not ready yet
                last = exc
                time.sleep(0.1)
        raise RuntimeError(f"GET {path} failed: {last}")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


class ArtifactContentTests(unittest.TestCase):
    def test_both_artifacts_built(self):
        self.assertTrue(_STATE["wheel"].is_file())
        self.assertTrue(_STATE["sdist"].is_file())

    def test_wheel_contains_all_static_assets(self):
        with zipfile.ZipFile(_STATE["wheel"]) as z:
            names = set(z.namelist())
        for asset in STATIC_FILES:
            self.assertIn(f"ai_observe/viewer/static/{asset}", names)

    def test_wheel_excludes_tests(self):
        with zipfile.ZipFile(_STATE["wheel"]) as z:
            names = z.namelist()
        offenders = [n for n in names if n == "tests" or n.startswith("tests/") or "/tests/" in n]
        self.assertEqual(offenders, [], f"wheel unexpectedly ships tests: {offenders}")

    def test_wheel_declares_only_expected_console_scripts(self):
        with zipfile.ZipFile(_STATE["wheel"]) as z:
            entry = next(n for n in z.namelist() if n.endswith(".dist-info/entry_points.txt"))
            text = z.read(entry).decode()
        self.assertIn("ai-observe = ai_observe.observe:main_generic", text)
        self.assertIn("ai-observe-viewer = ai_observe.viewer.__main__:main", text)
        for shadow in ("claude", "codex", "gemini", "opencode"):
            self.assertNotRegex(text, rf"(?m)^{shadow} =")

    def test_wheel_includes_license_and_notice(self):
        with zipfile.ZipFile(_STATE["wheel"]) as z:
            names = z.namelist()
        self.assertTrue(any(n.endswith(".dist-info/licenses/LICENSE") for n in names), names)
        self.assertTrue(any(n.endswith(".dist-info/licenses/NOTICE") for n in names), names)

    def test_wheel_license_is_apache_spdx_without_legacy_classifier(self):
        with zipfile.ZipFile(_STATE["wheel"]) as z:
            meta = z.read(next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))).decode()
        self.assertIn("License-Expression: Apache-2.0", meta)
        self.assertNotIn("License :: OSI Approved :: Apache Software License", meta)

    def test_sdist_includes_static_license_and_notice(self):
        with tarfile.open(_STATE["sdist"]) as t:
            names = t.getnames()
        self.assertTrue(any(n.endswith("/LICENSE") for n in names), names)
        self.assertTrue(any(n.endswith("/NOTICE") for n in names), names)
        for asset in STATIC_FILES:
            self.assertTrue(
                any(n.endswith(f"src/ai_observe/viewer/static/{asset}") for n in names),
                f"sdist missing static asset {asset}",
            )

    def test_sdist_build_path_is_valid(self):
        """Unpack the sdist and rebuild a wheel from it — proves the sdist is a
        valid, buildable source distribution (the architect's accepted check
        alongside the best-effort install-from-sdist below)."""
        work = _STATE["tmp"] / "sdist-unpack"
        work.mkdir(exist_ok=True)
        with tarfile.open(_STATE["sdist"]) as t:
            t.extractall(work)
        top = next(work.iterdir())
        self.assertTrue((top / "pyproject.toml").is_file())
        self.assertTrue((top / "LICENSE").is_file())
        self.assertTrue((top / "NOTICE").is_file())
        out = work / "rebuilt"
        out.mkdir(exist_ok=True)
        _build_artifacts(top, out)
        self.assertTrue(any(out.glob("*.whl")))

    def test_install_from_sdist_best_effort(self):
        """Best-effort install-from-sdist (architect-preferred). Uses a
        system-site venv so the build backend is available offline; skips if the
        environment cannot build/install the sdist."""
        venv = _STATE["tmp"] / "sdist-venv"
        r = _run([sys.executable, "-m", "venv", "--system-site-packages", venv])
        if r.returncode != 0:
            self.skipTest(f"could not create system-site venv: {r.stderr}")
        py = _venv_python(venv)
        r = _run([py, "-m", "pip", "install", "--no-build-isolation", "--no-index",
                  "--no-deps", _STATE["sdist"]])
        if r.returncode != 0:
            self.skipTest(f"install-from-sdist not feasible offline here:\n{r.stdout}\n{r.stderr}")
        check = _run([py, "-c", "import ai_observe; print(ai_observe.__version__)"],
                     cwd=_STATE["tmp"], env=_clean_env())
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(check.stdout.strip(), "0.1.0")


class InstalledPackageTests(unittest.TestCase):
    def test_console_scripts_installed(self):
        self.assertTrue(_venv_script(_STATE["venv"], "ai-observe").exists())
        self.assertTrue(_venv_script(_STATE["venv"], "ai-observe-viewer").exists())

    def test_ai_observe_usage_path_works(self):
        # No args -> our usage error, proving the console script dispatches into
        # ai_observe.observe.main_generic.
        proc = _run([_venv_script(_STATE["venv"], "ai-observe")], cwd=_STATE["tmp"], env=_clean_env())
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("usage", (proc.stdout + proc.stderr).lower())

    def test_import_works_outside_checkout_without_pythonpath(self):
        snippet = "import ai_observe; print(ai_observe.__version__); print(ai_observe.__file__)"
        proc = _run([_STATE["py"], "-c", snippet], cwd=_STATE["tmp"], env=_clean_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        version, location = proc.stdout.split()
        self.assertEqual(version, "0.1.0")
        # Resolves to the installed copy in the venv, NOT the checkout src/.
        self.assertIn(str(_STATE["venv"]), location)
        self.assertNotIn(str(ROOT / "src"), location)

    def test_installed_package_exposes_all_six_static_files(self):
        snippet = (
            "import os, json; from pathlib import Path; import ai_observe.viewer as v; "
            "d = Path(v.__file__).parent / 'static'; print(json.dumps(sorted(os.listdir(d)))); "
            "print(str(d))"
        )
        proc = _run([_STATE["py"], "-c", snippet], cwd=_STATE["tmp"], env=_clean_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        listing, static_dir = proc.stdout.splitlines()[:2]
        self.assertEqual(set(json.loads(listing)), STATIC_FILES)
        self.assertIn(str(_STATE["venv"]), static_dir)


class ViewerServingTests(unittest.TestCase):
    def test_module_invocation_serves_after_install(self):
        jsonl = _sample_jsonl(_STATE["tmp"])
        viewer = _ViewerProc([_STATE["py"], "-m", "ai_observe.viewer", jsonl, "--no-browser"], cwd=_STATE["tmp"])
        try:
            status, body = viewer.get("/")
            self.assertEqual(status, 200)
            self.assertTrue(body)
            status, body = viewer.get("/static/style.css")
            self.assertEqual(status, 200)
            self.assertTrue(body)
        finally:
            viewer.stop()

    def test_installed_viewer_serves_static_outside_checkout(self):
        """Hard criterion: clean-venv wheel install, OUTSIDE the checkout, the
        viewer serves / and the static assets (guards the src-layout package_data
        footgun)."""
        jsonl = _sample_jsonl(_STATE["tmp"])
        script = _venv_script(_STATE["venv"], "ai-observe-viewer")
        viewer = _ViewerProc([script, jsonl, "--no-browser"], cwd=_STATE["tmp"])
        try:
            for path in ("/", "/static/index.html", "/static/index.js", "/static/style.css"):
                status, body = viewer.get(path)
                self.assertEqual(status, 200, f"GET {path} -> {status}")
                self.assertTrue(body, f"GET {path} returned empty body")
        finally:
            viewer.stop()


class RuntimeErrorPathTests(unittest.TestCase):
    def test_unsupported_platform_raises_clear_error(self):
        """Simulated unit test: on a non-Linux platform the strace backend raises
        a clear, actionable error (no native non-Linux runner required)."""
        import ai_observe.backends.strace as strace_mod
        from ai_observe.backends.strace import StraceBackend
        from ai_observe.observe import ObserveError

        backend = StraceBackend(
            error_factory=lambda message, code: ObserveError(message, code),
            trace_parser_cls=None,
            live_tracer_cls=None,
            parse_trace_file=None,
            safe_write_jsonl=None,
            env_flag=None,
            env_value=None,
            live_enabled=None,
            live_poll_seconds=None,
            live_join_timeout=None,
        )
        original = strace_mod.sys.platform
        strace_mod.sys.platform = "darwin"
        try:
            with self.assertRaises(ObserveError) as cm:
                backend.prepare(session=None)  # raises before touching session
        finally:
            strace_mod.sys.platform = original
        self.assertIn("Linux required", str(cm.exception))

    def test_installed_backend_unavailable_fails_clearly(self):
        """Through the INSTALLED CLI: with strace unavailable, the runtime fails
        with a clear, actionable message rather than a cryptic crash."""
        env = _clean_env(PATH="", AI_OBSERVE_BACKENDS="strace", AI_OBSERVE_DIR=str(_STATE["tmp"] / "obs-err"))
        proc = _run(
            [_venv_script(_STATE["venv"], "ai-observe"), "--", sys.executable, "-c", "pass"],
            cwd=_STATE["tmp"],
            env=env,
        )
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertTrue(
            ("strace not found" in combined) or ("Linux required" in combined),
            f"expected a clear strace/platform error, got:\n{combined}",
        )


@unittest.skipUnless(sys.platform.startswith("linux"), "live observation requires Linux")
@unittest.skipUnless(shutil.which("strace"), "live observation requires the strace binary")
class LiveObservationSmokeTests(unittest.TestCase):
    def test_observed_command_runs_and_warns_by_default(self):
        work = _STATE["tmp"] / "live-work"
        work.mkdir(exist_ok=True)
        obs = _STATE["tmp"] / "live-obs"
        env = _clean_env(AI_OBSERVE_DIR=str(obs), AI_OBSERVE_BACKENDS="strace")
        proc = _run(
            [_venv_script(_STATE["venv"], "ai-observe"), "--session", "demo", "--",
             sys.executable, "-c", "from pathlib import Path; Path('hello.txt').write_text('hi')"],
            cwd=work,
            env=env,
        )
        combined = proc.stdout + proc.stderr
        if proc.returncode != 0 and re.search(r"ptrace|seccomp|Yama|denied", combined, re.IGNORECASE):
            self.skipTest(f"ptrace not permitted in this environment:\n{combined}")
        self.assertEqual(proc.returncode, 0, combined)
        self.assertTrue((work / "hello.txt").exists())
        jsonl = obs / "demo.jsonl"
        self.assertTrue(jsonl.exists(), f"no session jsonl produced; stderr:\n{combined}")
        # Sensitive-data warning is on by default (quiet mode not enabled).
        self.assertIn("may contain secrets", combined)

    def test_quiet_mode_suppresses_warning(self):
        work = _STATE["tmp"] / "live-work-quiet"
        work.mkdir(exist_ok=True)
        obs = _STATE["tmp"] / "live-obs-quiet"
        env = _clean_env(AI_OBSERVE_DIR=str(obs), AI_OBSERVE_BACKENDS="strace", AI_OBSERVE_QUIET="1")
        proc = _run(
            [_venv_script(_STATE["venv"], "ai-observe"), "--session", "q", "--",
             sys.executable, "-c", "pass"],
            cwd=work,
            env=env,
        )
        combined = proc.stdout + proc.stderr
        if proc.returncode != 0 and re.search(r"ptrace|seccomp|Yama|denied", combined, re.IGNORECASE):
            self.skipTest(f"ptrace not permitted in this environment:\n{combined}")
        self.assertNotIn("may contain secrets", combined)


if __name__ == "__main__":
    unittest.main()
