from pathlib import Path
import json
import os
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


def write_exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def env_path() -> str:
    return os.environ.get("PATH", "")


class ObserveCliIntegrationTests(unittest.TestCase):
    def make_fake_strace(self, root: Path) -> Path:
        fake = root / "strace"
        write_exe(fake, f"""
            #!{sys.executable}
            import os, subprocess, sys
            out = sys.argv[sys.argv.index('-o') + 1]
            trace = os.environ.get('FAKE_STRACE_TRACE', '')
            with open(out, 'w', encoding='utf-8') as fh:
                fh.write(trace)
            if os.environ.get('FAKE_STRACE_FAIL') == '1':
                print('ptrace denied', file=sys.stderr)
                sys.exit(1)
            idx = sys.argv.index('-e')
            cmd = sys.argv[idx + 2:]
            sys.exit(subprocess.run(cmd).returncode)
        """)
        return fake

    def make_fake_tool(self, path: Path, *, exit_code: int = 0) -> Path:
        write_exe(path, f"""
            #!{sys.executable}
            import json, os, sys
            out = os.environ.get('FAKE_TOOL_ARGV_OUT')
            if out:
                with open(out, 'w', encoding='utf-8') as fh:
                    json.dump(sys.argv, fh)
            marker = os.environ.get('FAKE_TOOL_MARKER')
            if marker:
                with open(marker, 'w', encoding='utf-8') as fh:
                    fh.write('ran')
            sys.exit({exit_code})
        """)
        return path

    def run_bin(self, name: str, env: dict, *args: str, cwd: Path | None = None):
        return __import__('subprocess').run(
            [sys.executable, str(ROOT / "bin" / name), *args],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_generic_wrapper_writes_schema_compatible_jsonl_and_preserves_exit(self):
        trace = '123 1714932000.000001 creat("x", 0600) = 3</tmp/work/x>\n'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_fake_strace(root)
            real = self.make_fake_tool(root / "real-tool", exit_code=7)
            argv_out = root / "argv.json"
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_BACKENDS": "strace",
                "AI_OBSERVE_DIR": str(root / "preferred-obs"),
                "CODEV_OBSERVE_DIR": str(root / "legacy-obs"),
                "AI_OBSERVE_SESSION_ID": "generic",
                "CODEV_OBSERVE_SESSION_ID": "legacy-session",
                "AI_OBSERVE_QUIET": "1",
                "CODEV_OBSERVE_QUIET": "0",
                "FAKE_STRACE_TRACE": trace,
                "FAKE_TOOL_ARGV_OUT": str(argv_out),
            })
            proc = self.run_bin("ai-observe", env, "--", str(real), "arg")
            self.assertEqual(proc.returncode, 7, proc.stderr)
            self.assertNotIn("trace/JSONL logs may contain secrets", proc.stderr)
            self.assertFalse((root / "legacy-obs").exists())
            events = [
                json.loads(line)
                for line in (root / "preferred-obs" / "generic.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["schema_version"], 2)
            self.assertEqual(events[0]["source"], "strace")
            self.assertEqual(events[0]["confidence"], "direct")
            self.assertEqual(events[0]["operation"], "create")
            self.assertEqual(events[0]["command"], [str(real.resolve()), "arg"])
            self.assertEqual(json.loads(argv_out.read_text(encoding="utf-8")), [str(real.resolve()), "arg"])

    def test_generic_real_command_replaces_only_requested_command_token(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_fake_strace(root)
            real = self.make_fake_tool(root / "real-tool")
            argv_out = root / "argv.json"
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_REAL_COMMAND": str(real),
                "AI_OBSERVE_DIR": str(root / "obs"),
                "AI_OBSERVE_SESSION_ID": "forced",
                "AI_OBSERVE_QUIET": "1",
                "FAKE_TOOL_ARGV_OUT": str(argv_out),
            })
            proc = self.run_bin("ai-observe", env, "--", "display-tool", "a", "b")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(argv_out.read_text(encoding="utf-8")), [str(real.resolve()), "a", "b"])

    def test_claude_named_shim_runs_with_fake_strace_and_records_command(self):
        trace = '123 1714932000.000001 creat("c", 0600) = 3</tmp/work/c>\n'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_fake_strace(root)
            real = self.make_fake_tool(root / "real-claude")
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_BACKENDS": "strace",
                "AI_OBSERVE_REAL_CLAUDE": str(real),
                "AI_OBSERVE_DIR": str(root / "obs"),
                "AI_OBSERVE_SESSION_ID": "claude-run",
                "AI_OBSERVE_QUIET": "1",
                "FAKE_STRACE_TRACE": trace,
            })
            proc = self.run_bin("claude", env, "-p", "hello")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            events = [json.loads(line) for line in (root / "obs" / "claude-run.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[0]["command"], [str(real.resolve()), "-p", "hello"])

    def test_codex_shim_runs_with_preferred_ai_real_codex(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_fake_strace(root)
            real = self.make_fake_tool(root / "real-codex")
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_BACKENDS": "strace",
                "AI_OBSERVE_REAL_CODEX": str(real),
                "AI_OBSERVE_DIR": str(root / "obs"),
                "AI_OBSERVE_SESSION_ID": "codex-ai",
                "AI_OBSERVE_QUIET": "1",
            })
            proc = self.run_bin("codex", env, "hello")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual((root / "obs" / "codex-ai.jsonl").read_text(encoding="utf-8"), "")

    def test_strict_parse_prefers_ai_observe_over_legacy_in_wrapper(self):
        trace = '123 1714932000.000001 creat("x", 0600) = 3</tmp/work/x>\n'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_fake_strace(root)
            real = self.make_fake_tool(root / "real-tool")
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_DIR": str(root / "obs"),
                "AI_OBSERVE_SESSION_ID": "strict",
                "AI_OBSERVE_QUIET": "1",
                "AI_OBSERVE_LIVE_PARSE": "0",
                "AI_OBSERVE_TEST_FAIL_AFTER": "1",
                "AI_OBSERVE_STRICT_PARSE": "1",
                "CODEV_OBSERVE_STRICT_PARSE": "0",
                "FAKE_STRACE_TRACE": trace,
            })
            proc = self.run_bin("ai-observe", env, "--", str(real))
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertTrue((root / "obs" / "strict.jsonl.partial").exists())

    def test_ai_observe_disable_bypasses_named_and_generic_without_strace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            named_real = self.make_fake_tool(root / "real-claude")
            generic_real = self.make_fake_tool(root / "real-generic")
            for bin_name, args, extra_env, marker_name in [
                ("claude", ["hello"], {"AI_OBSERVE_REAL_CLAUDE": str(named_real)}, "named-marker"),
                ("ai-observe", ["--", str(generic_real), "hello"], {}, "generic-marker"),
            ]:
                with self.subTest(bin_name=bin_name):
                    marker = root / marker_name
                    env = os.environ.copy()
                    env.update({
                        "PATH": "",
                        "AI_OBSERVE_DISABLE": "1",
                        "FAKE_TOOL_MARKER": str(marker),
                        **extra_env,
                    })
                    proc = self.run_bin(bin_name, env, *args)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(marker.read_text(encoding="utf-8"), "ran")

    def test_legacy_codex_disable_still_bypasses_without_strace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = self.make_fake_tool(root / "real-codex")
            marker = root / "marker"
            env = os.environ.copy()
            env.update({
                "PATH": "",
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_DISABLE": "1",
                "FAKE_TOOL_MARKER": str(marker),
            })
            proc = self.run_bin("codex", env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "ran")

    def test_missing_strace_exits_127_before_child_for_named_and_generic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            named_real = self.make_fake_tool(root / "real-claude")
            generic_real = self.make_fake_tool(root / "real-generic")
            for bin_name, args, extra_env, marker_name in [
                ("claude", [], {"AI_OBSERVE_REAL_CLAUDE": str(named_real)}, "named-marker"),
                ("ai-observe", ["--", str(generic_real)], {}, "generic-marker"),
            ]:
                with self.subTest(bin_name=bin_name):
                    marker = root / marker_name
                    env = os.environ.copy()
                    env.update({
                        "PATH": "",
                        "AI_OBSERVE_DIR": str(root / f"obs-{bin_name}"),
                        "AI_OBSERVE_SESSION_ID": bin_name,
                        "FAKE_TOOL_MARKER": str(marker),
                        **extra_env,
                    })
                    proc = self.run_bin(bin_name, env, *args)
                    self.assertEqual(proc.returncode, 127)
                    self.assertFalse(marker.exists())
                    self.assertIn("strace not found", proc.stderr)

    def test_invalid_backend_name_fails_before_launching_child(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tool = self.make_fake_tool(root / "real-tool")
            marker = root / "marker"
            env = os.environ.copy()
            env.update({
                "PATH": "",
                "AI_OBSERVE_BACKENDS": "snapshot,fanotify",
                "AI_OBSERVE_DIR": str(root / "obs"),
                "AI_OBSERVE_SESSION_ID": "invalid-backend",
                "AI_OBSERVE_QUIET": "1",
                "FAKE_TOOL_MARKER": str(marker),
            })
            proc = self.run_bin("ai-observe", env, "--", str(tool))
            self.assertEqual(proc.returncode, 2)
            self.assertIn("unsupported backend name", proc.stderr)
            self.assertFalse(marker.exists())

    def test_snapshot_only_mode_runs_without_strace_and_emits_inferred_events(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            snap_path = root / "snapshot.txt"
            tool = root / "writer"
            write_exe(tool, f"""
                #!{sys.executable}
                from pathlib import Path
                Path("snapshot.txt").write_text("snapshot", encoding="utf-8")
            """)
            env = os.environ.copy()
            env.update({
                "PATH": "",
                "AI_OBSERVE_BACKENDS": "snapshot",
                "AI_OBSERVE_SESSION_ID": "snapshot-only",
                "AI_OBSERVE_QUIET": "1",
            })
            proc = self.run_bin("ai-observe", env, "--", str(tool), cwd=root)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            obs = root / ".codev" / "observe"
            events = [
                json.loads(line)
                for line in (obs / "snapshot-only.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [(event["path"], event["source"], event["operation"]) for event in events],
                [(str(snap_path), "snapshot", "create")],
            )
            self.assertEqual((obs / "snapshot-only.trace").read_text(encoding="utf-8"), "")

    def test_strace_only_mode_disables_snapshot_reconciliation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            direct_path = root / "direct.txt"
            trace = f'123 1714932000.000001 creat("direct.txt", 0600) = 3<{direct_path}>\\n'
            self.make_fake_strace(root)
            tool = root / "writer"
            write_exe(tool, f"""
                #!{sys.executable}
                from pathlib import Path
                Path("direct.txt").write_text("direct", encoding="utf-8")
                Path("snapshot.txt").write_text("snapshot", encoding="utf-8")
            """)
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_BACKENDS": "strace",
                "AI_OBSERVE_SESSION_ID": "strace-only",
                "AI_OBSERVE_QUIET": "1",
                "AI_OBSERVE_LIVE_PARSE": "0",
                "FAKE_STRACE_TRACE": trace,
            })
            proc = self.run_bin("ai-observe", env, "--", str(tool), cwd=root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            events = [
                json.loads(line)
                for line in (root / ".codev" / "observe" / "strace-only.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [(event["path"], event["source"], event["operation"]) for event in events],
                [(str(direct_path), "strace", "create")],
            )

    def test_explicit_strace_snapshot_backend_setting_matches_default_layered_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            direct_path = root / "direct.txt"
            extra_path = root / "snapshot.txt"
            trace = f'123 1714932000.000001 creat("direct.txt", 0600) = 3<{direct_path}>\\n'
            self.make_fake_strace(root)
            tool = root / "writer"
            write_exe(tool, f"""
                #!{sys.executable}
                from pathlib import Path
                Path("direct.txt").write_text("direct", encoding="utf-8")
                Path("snapshot.txt").write_text("snapshot", encoding="utf-8")
            """)
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_BACKENDS": "strace,snapshot",
                "AI_OBSERVE_SESSION_ID": "explicit-layered",
                "AI_OBSERVE_QUIET": "1",
                "AI_OBSERVE_LIVE_PARSE": "0",
                "FAKE_STRACE_TRACE": trace,
            })
            proc = self.run_bin("ai-observe", env, "--", str(tool), cwd=root)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            events = [
                json.loads(line)
                for line in (root / ".codev" / "observe" / "explicit-layered.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                {(event["path"], event["source"], event["operation"]) for event in events},
                {
                    (str(direct_path), "strace", "create"),
                    (str(extra_path), "snapshot", "create"),
                },
            )

    def test_snapshot_default_root_merges_inferred_changes_and_excludes_observer_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            direct_path = root / "direct.txt"
            extra_path = root / "snapshot.txt"
            trace = f'123 1714932000.000001 creat("direct.txt", 0600) = 3<{direct_path}>\\n'
            self.make_fake_strace(root)
            tool = root / "writer"
            write_exe(tool, f"""
                #!{sys.executable}
                from pathlib import Path
                Path("direct.txt").write_text("direct", encoding="utf-8")
                Path("snapshot.txt").write_text("snapshot", encoding="utf-8")
            """)
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_SESSION_ID": "snap-default",
                "AI_OBSERVE_QUIET": "1",
                "AI_OBSERVE_LIVE_PARSE": "0",
                "FAKE_STRACE_TRACE": trace,
            })
            proc = self.run_bin("ai-observe", env, "--", str(tool), cwd=root)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            jsonl_path = root / ".codev" / "observe" / "snap-default.jsonl"
            events = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                {(event["path"], event["source"], event["operation"]) for event in events},
                {
                    (str(direct_path), "strace", "create"),
                    (str(extra_path), "snapshot", "create"),
                },
            )
            self.assertTrue(all("/.codev/observe/" not in event["path"] for event in events))

    def test_snapshot_explicit_roots_ignore_outside_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inside = root / "inside"
            outside = root / "outside"
            inside.mkdir()
            outside.mkdir()
            self.make_fake_strace(root)
            tool = root / "writer"
            write_exe(tool, f"""
                #!{sys.executable}
                from pathlib import Path
                Path({str(inside / "in.txt")!r}).write_text("in", encoding="utf-8")
                Path({str(outside / "out.txt")!r}).write_text("out", encoding="utf-8")
            """)
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_DIR": str(root / "obs"),
                "AI_OBSERVE_SESSION_ID": "roots",
                "AI_OBSERVE_ROOTS": str(inside),
                "AI_OBSERVE_QUIET": "1",
                "AI_OBSERVE_LIVE_PARSE": "0",
            })
            proc = self.run_bin("ai-observe", env, "--", str(tool), cwd=root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            events = [
                json.loads(line)
                for line in (root / "obs" / "roots.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([(event["path"], event["source"]) for event in events], [(str(inside / "in.txt"), "snapshot")])

    def test_snapshot_missing_roots_fail_when_no_roots_remain(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            missing = root / "missing"
            self.make_fake_strace(root)
            tool = self.make_fake_tool(root / "noop")
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_DIR": str(root / "obs"),
                "AI_OBSERVE_SESSION_ID": "missing-roots",
                "AI_OBSERVE_ROOTS": str(missing),
                "AI_OBSERVE_QUIET": "1",
                "AI_OBSERVE_LIVE_PARSE": "0",
            })
            proc = self.run_bin("ai-observe", env, "--", str(tool), cwd=root)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("snapshot missing_root", proc.stderr)
            self.assertIn("no usable snapshot roots remain", proc.stderr)
            meta = json.loads((root / "obs" / "missing-roots.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["parser"]["status"], "snapshot_root_error")
            self.assertEqual(meta["snapshot"]["complete"], False)
            codes = {diag["code"] for diag in meta["snapshot"]["diagnostics"]}
            self.assertEqual(codes, {"missing_root", "no_roots"})

    def test_snapshot_parser_failure_keeps_partial_direct_and_writes_inferred_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            direct_path = root / "direct.txt"
            extra_path = root / "snapshot.txt"
            trace = f'123 1714932000.000001 creat("direct.txt", 0600) = 3<{direct_path}>\\n'
            self.make_fake_strace(root)
            tool = root / "writer"
            write_exe(tool, f"""
                #!{sys.executable}
                from pathlib import Path
                Path("direct.txt").write_text("direct", encoding="utf-8")
                Path("snapshot.txt").write_text("snapshot", encoding="utf-8")
            """)
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "AI_OBSERVE_SESSION_ID": "pf-snapshot",
                "AI_OBSERVE_QUIET": "1",
                "AI_OBSERVE_LIVE_PARSE": "0",
                "AI_OBSERVE_TEST_FAIL_AFTER": "1",
                "FAKE_STRACE_TRACE": trace,
            })
            proc = self.run_bin("ai-observe", env, "--", str(tool), cwd=root)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            obs = root / ".codev" / "observe"
            partial_events = [
                json.loads(line)
                for line in (obs / "pf-snapshot.jsonl.partial").read_text(encoding="utf-8").splitlines()
            ]
            jsonl_events = [
                json.loads(line)
                for line in (obs / "pf-snapshot.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([(event["path"], event["source"]) for event in partial_events], [(str(direct_path), "strace")])
            self.assertTrue(any(event["path"] == str(extra_path) and event["source"] == "snapshot" for event in jsonl_events))
            self.assertTrue(all(event["source"] == "snapshot" for event in jsonl_events))


if __name__ == "__main__":
    unittest.main()
