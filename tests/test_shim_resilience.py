"""Tests for the resilient bin/* shims (Spec 20, Phase 2).

Each shim must PREFER the installed `ai_observe` package and fall back to the
checkout `src/` directory only when the package itself is unavailable, so the
same shim works in both installed and source-checkout workflows. Crucially the
fallback must be narrow: a deeper import error (a broken/incomplete install)
surfaces rather than being masked by silently importing the checkout copy.

Two complementary layers:

1. In-process branch detection (`exec` of the shim source under a non-``__main__``
   name, so only the import logic runs, not dispatch). By controlling
   ``sys.modules`` / ``sys.meta_path`` we assert exactly WHICH branch executes:
   - installed path: binds the entry point from the already-importable package,
     without prepending the checkout ``src/`` to ``sys.path``;
   - fallback path (forced hermetically): with the package made unavailable, the
     shim prepends ``src/`` and resolves the real entry point;
   - broken install: with ``ai_observe`` importable but ``ai_observe.observe``
     missing, the shim re-raises instead of falling back.

2. End-to-end subprocess matrix: actually run ``python bin/<shim>`` in both an
   "installed" env (``PYTHONPATH=src``) and a hermetic bare-checkout env
   (``-S`` to drop site-packages, no ``PYTHONPATH``, run from outside the repo),
   using the existing ``AI_OBSERVE_DISABLE`` + ``AI_OBSERVE_REAL_*`` passthrough
   so dispatch reaches a marker without needing a real target binary.
"""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BIN = ROOT / "bin"

# Maps each shim to the entry callable it imports from ai_observe.observe.
SHIMS = {
    "ai-observe": "main_generic",
    "claude": "main_shim",
    "codex": "main_shim",
    "gemini": "main_shim",
    "opencode": "main_shim",
    "agy": "main_shim",
}


def _exec_shim(name: str, namespace_name: str = "shim_under_test") -> dict:
    """Execute a shim's source with a non-__main__ name (skips dispatch).

    Returns the resulting global namespace so callers can inspect the bound
    entry callable. ``__file__`` is set to the real shim path so the fallback's
    ``Path(__file__).resolve().parents[1] / "src"`` resolves to the checkout.
    """
    src_path = BIN / name
    code = compile(src_path.read_text(encoding="utf-8"), str(src_path), "exec")
    ns: dict = {"__name__": namespace_name, "__file__": str(src_path)}
    exec(code, ns)  # noqa: S102 - executing first-party shim source under test
    return ns


class _AiObserveBlocker:
    """Meta-path finder that hides ``ai_observe*`` until the checkout ``src/`` is
    on ``sys.path``.

    This forces the shim's fallback branch deterministically regardless of
    whether ``ai_observe`` happens to be installed in the test interpreter: the
    first import attempt is blocked (so the shim takes the ``except`` branch and
    prepends ``src/``); once ``src/`` is present the finder defers, letting the
    real path finder resolve the checkout copy.
    """

    def find_spec(self, name, path=None, target=None):  # noqa: D401, ANN001
        if (name == "ai_observe" or name.startswith("ai_observe.")) and str(SRC) not in sys.path:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None  # defer to the remaining finders


class ShimImportBranchTests(unittest.TestCase):
    """Precise, in-process detection of which import branch each shim takes."""

    def setUp(self) -> None:
        self._saved_path = list(sys.path)
        self._saved_meta = list(sys.meta_path)
        self._saved_modules = {
            k: v for k, v in sys.modules.items()
            if k == "ai_observe" or k.startswith("ai_observe.")
        }

    def tearDown(self) -> None:
        sys.path[:] = self._saved_path
        sys.meta_path[:] = self._saved_meta
        for k in [k for k in sys.modules if k == "ai_observe" or k.startswith("ai_observe.")]:
            del sys.modules[k]
        sys.modules.update(self._saved_modules)

    def _drop_ai_observe_modules(self) -> None:
        for k in [k for k in sys.modules if k == "ai_observe" or k.startswith("ai_observe.")]:
            del sys.modules[k]

    def _drop_src_from_path(self) -> None:
        sys.path[:] = [p for p in sys.path if Path(p).resolve() != SRC.resolve()]

    def test_prefers_installed_package_without_touching_checkout_src(self):
        """When ai_observe is already importable, the shim uses it and does NOT
        fall back to splicing the checkout src/ onto sys.path."""
        for name, attr in SHIMS.items():
            with self.subTest(shim=name):
                # A sentinel "installed" package whose entry callable is identifiable.
                sentinel = lambda *a, **k: 0  # noqa: E731 - identity marker only
                fake_pkg = types.ModuleType("ai_observe")
                fake_pkg.__path__ = []  # mark as a package
                fake_obs = types.ModuleType("ai_observe.observe")
                setattr(fake_obs, attr, sentinel)
                fake_pkg.observe = fake_obs

                self._drop_ai_observe_modules()
                sys.modules["ai_observe"] = fake_pkg
                sys.modules["ai_observe.observe"] = fake_obs
                self._drop_src_from_path()
                before = list(sys.path)

                ns = _exec_shim(name)

                # Bound the entry point from the (sentinel) installed package...
                self.assertIs(ns[attr], sentinel)
                # ...and never executed the fallback (src/ not prepended; path intact).
                self.assertNotIn(str(SRC), sys.path)
                self.assertEqual(sys.path, before)

    def test_falls_back_to_checkout_src_when_package_absent(self):
        """When ai_observe is NOT importable, the shim splices in the checkout
        src/ and resolves the real entry point. Forced hermetically via a
        meta-path blocker so it holds even if ai_observe is installed."""
        for name, attr in SHIMS.items():
            with self.subTest(shim=name):
                self._drop_ai_observe_modules()
                self._drop_src_from_path()
                blocker = _AiObserveBlocker()
                sys.meta_path.insert(0, blocker)

                self.assertNotIn(str(SRC), sys.path)
                ns = _exec_shim(name)

                # Fallback prepended the checkout src/ and import succeeded.
                self.assertIn(str(SRC), sys.path)
                import ai_observe.observe as real_obs  # importable now via the fallback
                self.assertIs(ns[attr], getattr(real_obs, attr))

    def test_broken_installed_package_is_not_masked_by_fallback(self):
        """If ai_observe imports but ai_observe.observe is missing (a broken or
        incomplete install), the shim must re-raise rather than silently fall
        back to the checkout copy."""
        for name, attr in SHIMS.items():
            with self.subTest(shim=name):
                # Top-level package present, but it exposes no submodules.
                fake_pkg = types.ModuleType("ai_observe")
                fake_pkg.__path__ = []
                self._drop_ai_observe_modules()
                sys.modules["ai_observe"] = fake_pkg
                self._drop_src_from_path()
                before = list(sys.path)

                with self.assertRaises(ModuleNotFoundError) as cm:
                    _exec_shim(name)

                self.assertEqual(cm.exception.name, "ai_observe.observe")
                # The fallback branch did NOT run.
                self.assertNotIn(str(SRC), sys.path)
                self.assertEqual(sys.path, before)


def _write_exe(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    path.chmod(0o755)
    return path


class ShimSubprocessMatrixTests(unittest.TestCase):
    """End-to-end: each shim actually runs and dispatches in both the installed
    and bare-checkout environments."""

    def _marker_tool(self, root: Path) -> Path:
        return _write_exe(root / "marker-tool", f"""
            #!{sys.executable}
            import os
            with open(os.environ["SHIM_MARKER"], "w", encoding="utf-8") as fh:
                fh.write("ran")
        """)

    def _run(self, name: str, env: dict, *args: str, cwd: Path, isolated: bool) -> subprocess.CompletedProcess:
        # ``-S`` drops site-packages so an installed ai_observe cannot satisfy the
        # try-branch; the only way the import succeeds is the checkout fallback.
        cmd = [sys.executable, *(["-S"] if isolated else []), str(BIN / name), *args]
        return subprocess.run(cmd, cwd=str(cwd), env=env, text=True, capture_output=True)

    def _base_env(self, marker: Path) -> dict:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.update({
            "PATH": "",
            "AI_OBSERVE_DISABLE": "1",  # passthrough: exec the resolved real target
            "SHIM_MARKER": str(marker),
        })
        return env

    def _assert_dispatches(self, name: str, *, installed: bool) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / "marker.txt"
            tool = self._marker_tool(root)
            env = self._base_env(marker)
            if installed:
                # Simulate the package being importable (try-branch succeeds).
                env["PYTHONPATH"] = str(SRC)
            if name == "ai-observe":
                args = ("--", str(tool))
            else:
                env[f"AI_OBSERVE_REAL_{name.upper()}"] = str(tool)
                args = ()
            # Run from outside the checkout to prove cwd-independence. Fallback
            # mode is isolated with -S so it cannot accidentally use an installed
            # copy; installed mode must NOT use -S (it relies on PYTHONPATH).
            proc = self._run(name, env, *args, cwd=root, isolated=not installed)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "ran")
            self.assertNotIn("ModuleNotFoundError", proc.stderr)

    def test_fallback_checkout_mode_dispatches(self):
        for name in SHIMS:
            with self.subTest(shim=name):
                self._assert_dispatches(name, installed=False)

    def test_installed_mode_dispatches(self):
        for name in SHIMS:
            with self.subTest(shim=name):
                self._assert_dispatches(name, installed=True)


if __name__ == "__main__":
    unittest.main()
