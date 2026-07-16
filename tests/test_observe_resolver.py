from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_observe import observe


def write_exe(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def write_observer_shim(path: Path, program: str = "codex") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name == "ai-observe":
        body = "from ai_observe.observe import main_generic\nraise SystemExit(main_generic())\n"
    else:
        body = f"from ai_observe.observe import main_shim\nraise SystemExit(main_shim({program!r}))\n"
    path.write_text(f"#!/usr/bin/env python3\n{body}", encoding="utf-8")
    path.chmod(0o755)


class NamedResolverTests(unittest.TestCase):
    def test_named_programs_use_ai_observe_real_env(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for program in ("codex", "claude", "gemini", "opencode", "agy"):
                with self.subTest(program=program):
                    shim = root / "bin" / program
                    real = root / "real" / program
                    write_exe(shim)
                    write_exe(real)
                    env = {f"AI_OBSERVE_REAL_{program.upper()}": str(real)}
                    self.assertEqual(observe.resolve_real_program(program, env, wrapper_argv0=shim), real.resolve())

    def test_codex_prefers_ai_real_over_legacy_and_accepts_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shim = root / "bin" / "codex"
            preferred = root / "preferred" / "codex"
            legacy = root / "legacy" / "codex"
            for path in (shim, preferred, legacy):
                write_exe(path)
            env = {"AI_OBSERVE_REAL_CODEX": str(preferred), "CODEV_OBSERVE_REAL_CODEX": str(legacy)}
            self.assertEqual(observe.resolve_real_codex(env, shim), preferred.resolve())
            self.assertEqual(observe.resolve_real_codex({"CODEV_OBSERVE_REAL_CODEX": str(legacy)}, shim), legacy.resolve())

    def test_non_codex_does_not_accept_legacy_real_env(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shim = root / "bin" / "claude"
            real = root / "legacy" / "claude"
            write_exe(shim)
            write_exe(real)
            env = {"CODEV_OBSERVE_REAL_CLAUDE": str(real), "PATH": ""}
            with self.assertRaises(observe.ObserveError):
                observe.resolve_real_program("claude", env, wrapper_argv0=shim)

    def test_path_lookup_skips_current_shim_for_all_named_programs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for program in ("codex", "claude", "gemini", "opencode", "agy"):
                with self.subTest(program=program):
                    shim = root / "shim" / program
                    real = root / "real" / program
                    write_exe(shim)
                    write_exe(real)
                    env = {"PATH": f"{shim.parent}{os.pathsep}{real.parent}"}
                    self.assertEqual(observe.resolve_real_program(program, env, wrapper_argv0=shim), real.resolve())

    def test_adjacent_real_and_bin_fallbacks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shim = root / "bin" / "gemini"
            real = root / "bin" / "gemini.real"
            write_exe(shim)
            write_exe(real)
            self.assertEqual(observe.resolve_real_program("gemini", {"PATH": ""}, wrapper_argv0=shim), real.resolve())

            real.unlink()
            bin_fallback = root / "bin" / "gemini.bin"
            write_exe(bin_fallback)
            self.assertEqual(observe.resolve_real_program("gemini", {"PATH": ""}, wrapper_argv0=shim), bin_fallback.resolve())

    def test_explicit_real_rejects_current_shim(self):
        with tempfile.TemporaryDirectory() as td:
            shim = Path(td) / "claude"
            write_exe(shim)
            with self.assertRaises(observe.ObserveError):
                observe.resolve_real_program("claude", {"AI_OBSERVE_REAL_CLAUDE": str(shim)}, wrapper_argv0=shim)

    def test_path_lookup_skips_observer_shim_in_other_directory_for_named_program(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            current_shim = root / "current" / "claude"
            other_shim = root / "other-shims" / "claude"
            real = root / "real" / "claude"
            write_observer_shim(current_shim, "claude")
            write_observer_shim(other_shim, "claude")
            write_exe(real)
            env = {"PATH": f"{other_shim.parent}{os.pathsep}{real.parent}"}
            self.assertEqual(
                observe.resolve_real_program("claude", env, wrapper_argv0=current_shim),
                real.resolve(),
            )

    def test_explicit_real_rejects_observer_shim_in_other_directory_for_named_program(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            current_shim = root / "current" / "opencode"
            other_shim = root / "other-shims" / "opencode"
            write_observer_shim(current_shim, "opencode")
            write_observer_shim(other_shim, "opencode")
            env = {"AI_OBSERVE_REAL_OPENCODE": str(other_shim)}
            with self.assertRaises(observe.ObserveError):
                observe.resolve_real_program("opencode", env, wrapper_argv0=current_shim)


class GenericCliResolverTests(unittest.TestCase):
    def test_parse_generic_args_requires_separator_and_command(self):
        for argv in ([], ["python"], ["--"], ["--session", "s"]):
            with self.subTest(argv=argv), self.assertRaises(observe.ObserveError):
                observe.parse_generic_args(list(argv))
        self.assertEqual(observe.parse_generic_args(["--session", "s", "--", "tool", "arg"]), ("s", ["tool", "arg"]))

    def test_parse_generic_help_exits_zero(self):
        with self.assertRaises(observe.ObserveError) as cm:
            observe.parse_generic_args(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_generic_explicit_path_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wrapper = root / "bin" / "ai-observe"
            tool = root / "tool"
            write_exe(wrapper)
            write_exe(tool)
            self.assertEqual(
                observe.resolve_command_argv([str(tool), "arg"], {}, wrapper_argv0=wrapper),
                [str(tool.resolve()), "arg"],
            )

    def test_generic_path_lookup_skips_observer_shim_and_finds_real(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wrapper = root / "shim" / "ai-observe"
            shim_codex = root / "shim" / "codex"
            real_codex = root / "real" / "codex"
            for path in (wrapper, shim_codex, real_codex):
                write_exe(path)
            env = {"PATH": f"{shim_codex.parent}{os.pathsep}{real_codex.parent}"}
            self.assertEqual(
                observe.resolve_command_argv(["codex", "arg"], env, wrapper_argv0=wrapper),
                [str(real_codex.resolve()), "arg"],
            )

    def test_generic_path_lookup_skips_observer_shim_in_other_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wrapper = root / "current" / "ai-observe"
            other_shim = root / "other-shims" / "codex"
            real_codex = root / "real" / "codex"
            write_observer_shim(wrapper)
            write_observer_shim(other_shim, "codex")
            write_exe(real_codex)
            env = {"PATH": f"{other_shim.parent}{os.pathsep}{real_codex.parent}"}
            self.assertEqual(
                observe.resolve_command_argv(["codex", "arg"], env, wrapper_argv0=wrapper),
                [str(real_codex.resolve()), "arg"],
            )

    def test_generic_rejects_path_resolved_ai_observe_shim_in_other_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wrapper = root / "current" / "ai-observe"
            other_wrapper = root / "other-shims" / "ai-observe"
            write_observer_shim(wrapper)
            write_observer_shim(other_wrapper)
            env = {"PATH": str(other_wrapper.parent)}
            with self.assertRaises(observe.ObserveError):
                observe.resolve_command_argv(["ai-observe"], env, wrapper_argv0=wrapper)

    def test_generic_rejects_explicit_observer_shim(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wrapper = root / "bin" / "ai-observe"
            codex_shim = root / "bin" / "codex"
            write_exe(wrapper)
            write_exe(codex_shim)
            with self.assertRaises(observe.ObserveError):
                observe.resolve_command_argv([str(codex_shim)], {}, wrapper_argv0=wrapper)

    def test_generic_real_command_replaces_only_argv0(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wrapper = root / "bin" / "ai-observe"
            forced = root / "real" / "tool"
            write_exe(wrapper)
            write_exe(forced)
            env = {"AI_OBSERVE_REAL_COMMAND": str(forced)}
            self.assertEqual(
                observe.resolve_command_argv(["display-tool", "a", "b"], env, wrapper_argv0=wrapper),
                [str(forced.resolve()), "a", "b"],
            )


if __name__ == "__main__":
    unittest.main()
