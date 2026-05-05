from pathlib import Path
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_observe import codex_observe


def write_exe(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    path.chmod(0o755)


class CodexObserveTests(unittest.TestCase):
    def test_resolve_real_codex_env_and_path_skip_self(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shim = root / "shim" / "codex"
            real = root / "real" / "codex"
            shim.parent.mkdir()
            real.parent.mkdir()
            write_exe(shim, "#!/bin/sh\nexit 9\n")
            write_exe(real, "#!/bin/sh\nexit 0\n")
            env = {"PATH": f"{shim.parent}{os.pathsep}{real.parent}"}
            self.assertEqual(codex_observe.resolve_real_codex(env, shim), real.resolve())
            env["CODEV_OBSERVE_REAL_CODEX"] = str(real)
            self.assertEqual(codex_observe.resolve_real_codex(env, shim), real.resolve())
            env["CODEV_OBSERVE_REAL_CODEX"] = str(shim)
            with self.assertRaises(codex_observe.ObserveError):
                codex_observe.resolve_real_codex(env, shim)

    def test_normalize_signal_return_code(self):
        self.assertEqual(codex_observe.normalize_exit_code(-2), 130)
        self.assertEqual(codex_observe.normalize_exit_code(7), 7)

    def test_session_id_sanitization_and_rejects_empty(self):
        self.assertEqual(codex_observe.sanitize_session_id("a/b c"), "a_b_c")
        self.assertEqual(codex_observe.sanitize_session_id("_"), "_")
        for value in ["", ".", ".."]:
            with self.assertRaises(codex_observe.ObserveError):
                codex_observe.sanitize_session_id(value)

    def test_prepare_logs_collision_modes_and_env_dir(self):
        with tempfile.TemporaryDirectory() as td:
            env = {"CODEV_OBSERVE_DIR": "obs", "CODEV_OBSERVE_SESSION_ID": "sess"}
            old = os.getcwd()
            try:
                os.chdir(td)
                obs = Path(td) / "obs"
                obs.mkdir()
                (obs / "sess.trace").write_text("old", encoding="utf-8")
                logs = codex_observe.prepare_logs(env)
                self.assertEqual(logs.session_id, "sess-1")
                self.assertEqual(logs.observe_dir, obs.resolve())
                self.assertEqual(stat.S_IMODE(obs.stat().st_mode), 0o755)
                self.assertEqual(stat.S_IMODE(logs.trace_path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(logs.jsonl_path.stat().st_mode), 0o600)
            finally:
                os.chdir(old)

    def test_observe_dir_ancestor_fallback_and_symlink_reject(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".codev").mkdir()
            sub = root / "a" / "b"
            sub.mkdir(parents=True)
            old = os.getcwd()
            try:
                os.chdir(sub)
                self.assertEqual(codex_observe.resolve_observe_dir({}), (root / ".codev" / "observe").resolve())
            finally:
                os.chdir(old)

            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(codex_observe.ObserveError):
                codex_observe.prepare_logs({"CODEV_OBSERVE_DIR": str(link), "CODEV_OBSERVE_SESSION_ID": "s"})

    def make_fake_tools(self, root: Path, codex_body: str = "") -> tuple[Path, Path]:
        fake_strace = root / "strace"
        write_exe(fake_strace, r'''
            #!/usr/bin/env python3
            import os, subprocess, sys
            out = sys.argv[sys.argv.index('-o') + 1]
            trace = os.environ.get('FAKE_STRACE_TRACE', '')
            with open(out, 'w', encoding='utf-8') as fh:
                fh.write(trace)
            if os.environ.get('FAKE_STRACE_FAIL') == '1':
                print('ptrace denied', file=sys.stderr)
                sys.exit(1)
            if os.environ.get('FAKE_STRACE_SLEEP') == '1':
                import signal, time
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                time.sleep(30)
                sys.exit(0)
            idx = sys.argv.index('-e')
            cmd = sys.argv[idx + 2:]
            sys.exit(subprocess.run(cmd).returncode)
        ''')
        real = root / "real-codex"
        write_exe(real, f'''
            #!{sys.executable}
            import json, os, sys
            out = os.environ.get('FAKE_CODEX_OUT')
            if out:
                with open(out, 'w', encoding='utf-8') as fh:
                    json.dump(sys.argv, fh)
            {codex_body}
        ''')
        return fake_strace, real

    def run_wrapper(self, env, *args):
        cmd = [sys.executable, str(ROOT / "bin" / "codex"), *args]
        return subprocess.run(cmd, cwd=env.get("PWD_OVERRIDE"), env=env, text=True, capture_output=True)

    def test_bypass_execs_real_codex_and_preserves_argv(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, real = self.make_fake_tools(root)
            out = root / "argv.json"
            env = os.environ.copy()
            env.update({
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_DISABLE": "1",
                "FAKE_CODEX_OUT": str(out),
            })
            proc = self.run_wrapper(env, "hello")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), [str(real), "hello"])

    def test_missing_strace_exits_127_before_codex(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / "real-codex"
            marker = root / "ran"
            write_exe(real, f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")
            env = os.environ.copy()
            env.update({"CODEV_OBSERVE_REAL_CODEX": str(real), "PATH": ""})
            proc = self.run_wrapper(env)
            self.assertEqual(proc.returncode, 127)
            self.assertFalse(marker.exists())

    def test_fake_strace_empty_jsonl_and_exit_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, real = self.make_fake_tools(root, "sys.exit(7)")
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_DIR": str(root / "obs"),
                "CODEV_OBSERVE_SESSION_ID": "empty",
                "CODEV_OBSERVE_QUIET": "1",
            })
            proc = self.run_wrapper(env)
            self.assertEqual(proc.returncode, 7, proc.stderr)
            self.assertEqual((root / "obs" / "empty.jsonl").read_text(encoding="utf-8"), "")

    def test_fake_strace_process_tree_events_and_schema(self):
        trace = """
123 1714932000.000001 openat(AT_FDCWD, "child.txt", O_WRONLY|O_CREAT|O_EXCL, 0600) = 3</tmp/work/child.txt>
123 1714932000.000002 write(3</tmp/work/child.txt>, "x", 1) = 1
123 1714932000.000003 rename("child.txt", "renamed.txt") = 0
123 1714932000.000004 chmod("renamed.txt", 0600) = 0
123 1714932000.000005 unlink("renamed.txt") = 0
""".lstrip()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, real = self.make_fake_tools(root)
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_DIR": str(root / "obs"),
                "CODEV_OBSERVE_SESSION_ID": "run",
                "CODEV_OBSERVE_QUIET": "1",
                "FAKE_STRACE_TRACE": trace,
            })
            proc = self.run_wrapper(env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            events = [json.loads(line) for line in (root / "obs" / "run.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([e["operation"] for e in events], ["create", "modify", "rename", "chmod", "delete"])
            self.assertEqual(events[0]["command"], [str(real.resolve())])
            self.assertEqual(events[0]["session_id"], "run")

    def test_parser_failure_partial_default_and_strict(self):
        trace = '123 1714932000.000001 creat("x", 0600) = 3</tmp/x>\n'
        for strict, expected in [("0", 0), ("1", 1)]:
            with self.subTest(strict=strict), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _, real = self.make_fake_tools(root)
                env = os.environ.copy()
                env.update({
                    "PATH": f"{root}{os.pathsep}{env_path()}",
                    "CODEV_OBSERVE_REAL_CODEX": str(real),
                    "CODEV_OBSERVE_DIR": str(root / "obs"),
                    "CODEV_OBSERVE_SESSION_ID": "pf",
                    "CODEV_OBSERVE_QUIET": "1",
                    "CODEV_OBSERVE_TEST_FAIL_AFTER": "1",
                    "CODEV_OBSERVE_STRICT_PARSE": strict,
                    "FAKE_STRACE_TRACE": trace,
                })
                proc = self.run_wrapper(env)
                self.assertEqual(proc.returncode, expected, proc.stderr)
                self.assertTrue((root / "obs" / "pf.trace").exists())
                self.assertTrue((root / "obs" / "pf.jsonl.partial").exists())

    def test_ptrace_denied_fake_strace_still_reports(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, real = self.make_fake_tools(root)
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_DIR": str(root / "obs"),
                "CODEV_OBSERVE_SESSION_ID": "deny",
                "CODEV_OBSERVE_QUIET": "1",
                "FAKE_STRACE_FAIL": "1",
            })
            proc = self.run_wrapper(env)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("ptrace", proc.stderr)

    def test_safe_write_jsonl_rejects_symlink_swap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            obs = root / "obs"
            obs.mkdir()
            target = root / "target"
            path = obs / "s.jsonl"
            path.symlink_to(target)
            with self.assertRaises(codex_observe.ObserveError):
                codex_observe.safe_write_jsonl(path, [], obs)

    def test_signal_escalation_returns_conventional_signal_code(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_fake_tools(root)
            real = root / "real-codex"
            env = os.environ.copy()
            env.update({
                "PATH": f"{root}{os.pathsep}{env_path()}",
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_DIR": str(root / "obs"),
                "CODEV_OBSERVE_SESSION_ID": "sig",
                "CODEV_OBSERVE_QUIET": "1",
                "CODEV_OBSERVE_SIGNAL_GRACE": "0.05",
                "FAKE_STRACE_SLEEP": "1",
            })
            proc = subprocess.Popen([sys.executable, str(ROOT / "bin" / "codex")], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(0.3)
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 128 + signal.SIGTERM, stderr)

    @unittest.skipUnless(shutil.which("strace"), "strace unavailable")
    def test_live_strace_child_process_tree_when_available(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / "real-codex"
            work = root / "work"
            work.mkdir()
            child_code = "\n".join([
                "from pathlib import Path",
                "import os",
                "Path('made.txt').write_text('x')",
                "with open('made.txt', 'a') as fh: fh.write('y')",
                "os.rename('made.txt', 'renamed.txt')",
                "os.chmod('renamed.txt', 0o600)",
                "os.unlink('renamed.txt')",
            ])
            script = "\n".join([
                f"#!{sys.executable}",
                "import subprocess",
                "import sys",
                f"code = {child_code!r}",
                "sys.exit(subprocess.run([sys.executable, '-c', code]).returncode)",
                "",
            ])
            write_exe(real, script)
            env = os.environ.copy()
            env.update({
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_DIR": str(root / "obs"),
                "CODEV_OBSERVE_SESSION_ID": "live",
                "CODEV_OBSERVE_QUIET": "1",
            })
            proc = subprocess.run([sys.executable, str(ROOT / "bin" / "codex")], cwd=work, env=env, text=True, capture_output=True)
            if proc.returncode != 0 and "ptrace" in proc.stderr.lower():
                self.skipTest("ptrace denied")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            events = [json.loads(line) for line in (root / "obs" / "live.jsonl").read_text(encoding="utf-8").splitlines()]
            ops = [e["operation"] for e in events]
            for op in ["modify", "rename", "chmod", "delete"]:
                self.assertIn(op, ops)


def env_path():
    return os.environ.get("PATH", "")


if __name__ == "__main__":
    unittest.main()
