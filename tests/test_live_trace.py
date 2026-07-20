"""Tests for live-mode tracer (Spec 3, phase 2)."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))  # tests/_util.py is here

from ai_observe import codex_observe
from ai_observe.trace_parser import ParserFailure, TraceParser, parse_trace_file
from _util import poll_until  # noqa: E402


SAMPLE_LINE_A = '123 1714932000.000001 openat(AT_FDCWD, "a.txt", O_WRONLY|O_CREAT|O_EXCL, 0600) = 3</tmp/work/a.txt>\n'
SAMPLE_LINE_B = '123 1714932000.000002 openat(AT_FDCWD, "b.txt", O_WRONLY|O_CREAT|O_EXCL, 0600) = 4</tmp/work/b.txt>\n'


def _make_parser():
    return TraceParser(
        session_id="s",
        invocation_id="s",
        command=["/real/codex"],
        initial_cwd="/tmp/work",
        active_artifacts=set(),
        include_log_writes=False,
    )


def _wrapper_env(td: Path, extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env.update({
        "PATH": f"{td}{os.pathsep}{os.environ.get('PATH', '')}",
        "CODEV_OBSERVE_DIR": str(td / "obs"),
        "CODEV_OBSERVE_QUIET": "1",
    })
    if not extra or not any(
        key in extra
        for key in (
            "AI_OBSERVE_BACKENDS",
            "CODEV_OBSERVE_BACKENDS",
            "AI_OBSERVE_ROOTS",
            "CODEV_OBSERVE_ROOTS",
        )
    ):
        env["AI_OBSERVE_BACKENDS"] = "strace"
    if extra:
        env.update(extra)
    return env


def _write_exe(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def _install_fake_strace(td: Path, trace_text: str = "", *, sleep_after_write: float = 0.0) -> Path:
    fake = td / "strace"
    _write_exe(fake, f"""
        #!/usr/bin/env python3
        import os, subprocess, sys, time
        out = sys.argv[sys.argv.index('-o') + 1]
        with open(out, 'w', encoding='utf-8') as fh:
            fh.write({trace_text!r})
        if {sleep_after_write!r}:
            time.sleep({sleep_after_write!r})
        idx = sys.argv.index('-e')
        cmd = sys.argv[idx + 2:]
        sys.exit(subprocess.run(cmd).returncode)
    """)
    return fake


def _install_real(td: Path, body: str = "") -> Path:
    real = td / "real-codex"
    body_block = textwrap.dedent(body).strip()
    if body_block:
        body_block = textwrap.indent(body_block, " " * 8)
    _write_exe(real, f"""
        #!{sys.executable}
        import os, sys
{body_block}
    """)
    return real


class EnvKnobValidationTests(unittest.TestCase):
    def test_poll_ms_env_validation(self):
        for value in ["0", "9999", "abc", ""]:
            self.assertAlmostEqual(codex_observe._live_poll_seconds({"CODEV_OBSERVE_LIVE_POLL_MS": value}), 0.200)
        self.assertAlmostEqual(codex_observe._live_poll_seconds({}), 0.200)
        self.assertAlmostEqual(codex_observe._live_poll_seconds({"CODEV_OBSERVE_LIVE_POLL_MS": "50"}), 0.050)
        self.assertAlmostEqual(codex_observe._live_poll_seconds({"CODEV_OBSERVE_LIVE_POLL_MS": "2000"}), 2.000)

    def test_join_timeout_env_validation(self):
        for value in ["0", "0.0", "601", "abc", ""]:
            self.assertAlmostEqual(codex_observe._live_join_timeout({"CODEV_OBSERVE_LIVE_JOIN_TIMEOUT": value}), 30.0)
        self.assertAlmostEqual(codex_observe._live_join_timeout({}), 30.0)
        self.assertAlmostEqual(codex_observe._live_join_timeout({"CODEV_OBSERVE_LIVE_JOIN_TIMEOUT": "0.5"}), 0.5)
        self.assertAlmostEqual(codex_observe._live_join_timeout({"CODEV_OBSERVE_LIVE_JOIN_TIMEOUT": "600"}), 600.0)

    def test_live_enabled_default_on(self):
        self.assertTrue(codex_observe._live_enabled({}))
        self.assertTrue(codex_observe._live_enabled({"CODEV_OBSERVE_LIVE_PARSE": "1"}))
        self.assertFalse(codex_observe._live_enabled({"CODEV_OBSERVE_LIVE_PARSE": "0"}))


class LiveTracerUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.obs = Path(self.tmp.name)
        self.trace_path = self.obs / "s.trace"
        self.jsonl_path = self.obs / "s.jsonl"
        self.trace_path.write_text("", encoding="utf-8")
        codex_observe.exclusive_touch(self.jsonl_path)

    def _new_tracer(self, poll_seconds: float = 0.01) -> codex_observe.LiveTracer:
        parser = _make_parser()
        return codex_observe.LiveTracer(self.trace_path, self.jsonl_path, self.obs, parser, poll_seconds)

    def test_incremental_emission_visible_before_close(self):
        tracer = self._new_tracer(poll_seconds=0.02)
        tracer.start()
        try:
            with self.trace_path.open("a", encoding="utf-8") as fh:
                fh.write(SAMPLE_LINE_A)
                fh.flush()
            self.assertTrue(poll_until(lambda: self.jsonl_path.read_text(encoding="utf-8").count("\n") >= 1))
            mid = self.jsonl_path.read_text(encoding="utf-8")
            self.assertEqual(mid.count("\n"), 1)
            self.assertIn("a.txt", mid)
            with self.trace_path.open("a", encoding="utf-8") as fh:
                fh.write(SAMPLE_LINE_B)
                fh.flush()
            self.assertTrue(poll_until(lambda: self.jsonl_path.read_text(encoding="utf-8").count("\n") >= 2))
        finally:
            tracer.request_stop()
            tracer.join(2.0)
        final = [json.loads(line) for line in self.jsonl_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["path"] for e in final], ["/tmp/work/a.txt", "/tmp/work/b.txt"])

    def test_unfinished_resumed_pair_across_poll_boundary(self):
        tracer = self._new_tracer(poll_seconds=0.01)
        tracer.start()
        try:
            with self.trace_path.open("a", encoding="utf-8") as fh:
                fh.write('123 1714932000.000001 openat(AT_FDCWD, "u.txt", O_WRONLY|O_CREAT|O_EXCL <unfinished ...>\n')
                fh.flush()
            # Intentional fixed sleep (negative check): wait past the poll
            # interval to assert nothing was emitted; "no event" has no
            # queryable completion condition to poll for.
            time.sleep(0.05)
            self.assertEqual(self.jsonl_path.read_text(encoding="utf-8"), "")
            with self.trace_path.open("a", encoding="utf-8") as fh:
                fh.write('123 1714932000.000002 <... openat resumed> , 0600) = 3</tmp/work/u.txt>\n')
                fh.flush()
            self.assertTrue(poll_until(lambda: self.jsonl_path.read_text(encoding="utf-8").count("\n") >= 1))
        finally:
            tracer.request_stop()
            tracer.join(2.0)
        events = [json.loads(line) for line in self.jsonl_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["operation"], "create")
        self.assertEqual(events[0]["path"], "/tmp/work/u.txt")

    def test_partial_trailing_line_buffered_then_emitted(self):
        tracer = self._new_tracer(poll_seconds=0.01)
        tracer.start()
        try:
            head, tail = SAMPLE_LINE_A[:30], SAMPLE_LINE_A[30:]
            with self.trace_path.open("a", encoding="utf-8") as fh:
                fh.write(head)
                fh.flush()
            # Intentional fixed sleep (negative check): see
            # test_unfinished_resumed_pair_across_poll_boundary.
            time.sleep(0.05)
            self.assertEqual(self.jsonl_path.read_text(encoding="utf-8"), "")
            with self.trace_path.open("a", encoding="utf-8") as fh:
                fh.write(tail)
                fh.flush()
            self.assertTrue(poll_until(lambda: self.jsonl_path.read_text(encoding="utf-8").count("\n") >= 1))
        finally:
            tracer.request_stop()
            tracer.join(2.0)

    def test_partial_trailing_line_flushed_at_stop(self):
        tracer = self._new_tracer(poll_seconds=0.01)
        tracer.start()
        try:
            with self.trace_path.open("a", encoding="utf-8") as fh:
                fh.write(SAMPLE_LINE_A.rstrip("\n"))  # no trailing newline
                fh.flush()
            # Intentional fixed sleep (negative check): see
            # test_unfinished_resumed_pair_across_poll_boundary.
            time.sleep(0.05)
            self.assertEqual(self.jsonl_path.read_text(encoding="utf-8"), "")
        finally:
            tracer.request_stop()
            tracer.join(2.0)
        events = [json.loads(line) for line in self.jsonl_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["path"], "/tmp/work/a.txt")

    def test_incremental_emission_filters_events_outside_watched_roots(self):
        parser = TraceParser(
            session_id="s",
            invocation_id="s",
            command=["/real/codex"],
            initial_cwd="/tmp/work",
            active_artifacts=set(),
            include_log_writes=False,
            watched_roots=["/tmp/work/inside"],
        )
        tracer = codex_observe.LiveTracer(self.trace_path, self.jsonl_path, self.obs, parser, 0.01)
        tracer.start()
        try:
            with self.trace_path.open("a", encoding="utf-8") as fh:
                fh.write('123 1714932000.000001 creat("/tmp/work/inside/in.txt", 0600) = 3</tmp/work/inside/in.txt>\n')
                fh.write('123 1714932000.000002 creat("/tmp/work/outside/out.txt", 0600) = 4</tmp/work/outside/out.txt>\n')
                fh.flush()
            self.assertTrue(poll_until(lambda: self.jsonl_path.read_text(encoding="utf-8").count("\n") >= 1))
        finally:
            tracer.request_stop()
            tracer.join(2.0)
        events = [json.loads(line) for line in self.jsonl_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(e["path"], e["source"]) for e in events], [("/tmp/work/inside/in.txt", "strace")])


class LiveModeWrapperTests(unittest.TestCase):
    def _trace_text(self) -> str:
        return SAMPLE_LINE_A + SAMPLE_LINE_B

    def _run_wrapper(self, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "codex")],
            env=env,
            text=True,
            capture_output=True,
        )

    def _run_in_process(self, env: dict):
        """Drive codex_observe.run() in-process so module-level monkeypatches apply.

        Captures stderr text and returns (returncode, stderr_text).
        """
        import io
        buf = io.StringIO()
        old_stderr = sys.stderr
        old_argv0 = sys.argv[0]
        sys.argv[0] = str(ROOT / "bin" / "codex")
        sys.stderr = buf
        try:
            try:
                rc = codex_observe.run([], env)
            except codex_observe.ObserveError as exc:
                print(f"codex-observe: {exc}", file=sys.stderr)
                rc = exc.code
        finally:
            sys.stderr = old_stderr
            sys.argv[0] = old_argv0
        return rc, buf.getvalue()

    def test_live_parse_disabled_no_thread_started(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _install_fake_strace(root, self._trace_text())
            real = _install_real(root)
            calls = []
            real_cls = codex_observe.LiveTracer

            class TrackedTracer(real_cls):
                def __init__(self, *a, **kw):
                    calls.append(("init",))
                    super().__init__(*a, **kw)

                def start(self):  # pragma: no cover - shouldn't run
                    calls.append(("start",))
                    super().start()

            codex_observe.LiveTracer = TrackedTracer
            try:
                env = _wrapper_env(root, {
                    "CODEV_OBSERVE_REAL_CODEX": str(real),
                    "CODEV_OBSERVE_SESSION_ID": "off",
                    "CODEV_OBSERVE_LIVE_PARSE": "0",
                })
                # Drive in-process so the monkeypatch is observable.
                rc, stderr = self._run_in_process(env)
            finally:
                codex_observe.LiveTracer = real_cls
            self.assertEqual(rc, 0, stderr)
            self.assertEqual(calls, [], "LiveTracer must not be constructed when disabled")
            events = [json.loads(line) for line in (root / "obs" / "off.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([e["path"] for e in events], ["/tmp/work/a.txt", "/tmp/work/b.txt"])

    def test_empty_session_produces_empty_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _install_fake_strace(root, "")
            real = _install_real(root)
            env = _wrapper_env(root, {
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_SESSION_ID": "e",
            })
            proc = self._run_wrapper(env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual((root / "obs" / "e.jsonl").read_text(encoding="utf-8"), "")

    def test_test_fail_after_under_live_mode_writes_partial_truncates_jsonl(self):
        trace = (
            '123 1714932000.000001 creat("x", 0600) = 3</tmp/work/x>\n'
            '123 1714932000.000002 creat("y", 0600) = 4</tmp/work/y>\n'
        )
        for strict, expected_code in [("0", 0), ("1", 1)]:
            with self.subTest(strict=strict), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _install_fake_strace(root, trace)
                real = _install_real(root)
                env = _wrapper_env(root, {
                    "CODEV_OBSERVE_REAL_CODEX": str(real),
                    "CODEV_OBSERVE_SESSION_ID": "pf",
                    "CODEV_OBSERVE_TEST_FAIL_AFTER": "1",
                    "CODEV_OBSERVE_STRICT_PARSE": strict,
                })
                proc = self._run_wrapper(env)
                self.assertEqual(proc.returncode, expected_code, proc.stderr)
                partial = (root / "obs" / "pf.jsonl.partial").read_text(encoding="utf-8")
                partial_events = [json.loads(line) for line in partial.splitlines()]
                self.assertEqual(len(partial_events), 1)
                self.assertEqual((root / "obs" / "pf.jsonl").stat().st_size, 0)
                self.assertIn("parser failed", proc.stderr)
                self.assertIn("original exit", proc.stderr)

                # Strace-only failure: no snapshot events, no promotion, so the
                # sidecar keeps the null authority + placeholder role and gains
                # no net-fallback warning (spec FR2 over-reach guard).
                meta = json.loads((root / "obs" / "pf.meta.json").read_text(encoding="utf-8"))
                self.assertEqual(meta["parser"]["status"], "parser_failure_partial")
                self.assertIsNone(meta["artifacts"]["authoritative_event_path"])
                self.assertEqual(meta["artifacts"]["jsonl"]["role"], "inferred_or_empty_placeholder")
                self.assertEqual(meta["artifacts"]["partial"]["role"], "partial_direct")
                self.assertFalse(
                    any("snapshot fallback: net events only" in warning for warning in meta["warnings"]),
                    meta["warnings"],
                )

    def test_test_fail_after_live_mode_with_snapshot_promotes_net_fallback_meta(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            work.mkdir()
            # Path arguments must be absolute and inside the watched root:
            # the parser resolves relative args against initial_cwd (not the
            # fd annotation), and out-of-root events are scope-dropped before
            # the fail-after threshold can trigger.
            trace = (
                f'123 1714932000.000001 creat("{work / "x"}", 0600) = 3<{work / "x"}>\n'
                f'123 1714932000.000002 creat("{work / "y"}", 0600) = 4<{work / "y"}>\n'
            )
            _install_fake_strace(root, trace)
            real = _install_real(root, f"""
                from pathlib import Path
                Path({str(work)!r}, "net.txt").write_text("net", encoding="utf-8")
            """)
            env = _wrapper_env(root, {
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_SESSION_ID": "pfsnap",
                "CODEV_OBSERVE_TEST_FAIL_AFTER": "1",
                "AI_OBSERVE_BACKENDS": "strace,snapshot",
                "AI_OBSERVE_ROOTS": str(work),
            })
            proc = self._run_wrapper(env)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            obs = root / "obs"
            partial_events = [
                json.loads(line)
                for line in (obs / "pfsnap.jsonl.partial").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(partial_events), 1)
            jsonl_events = [
                json.loads(line)
                for line in (obs / "pfsnap.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [(event["path"], event["source"], event["operation"]) for event in jsonl_events],
                [(str(work / "net.txt"), "snapshot", "create")],
            )

            # Live truncate-then-promote path: the sidecar keeps the promoted
            # .jsonl authoritative but must describe it honestly (spec FR1/FR3).
            meta = json.loads((obs / "pfsnap.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["parser"]["status"], "parser_failure_partial")
            self.assertEqual(meta["artifacts"]["authoritative_event_path"], "pfsnap.jsonl")
            self.assertEqual(meta["artifacts"]["jsonl"]["role"], "authoritative_net")
            self.assertEqual(meta["artifacts"]["partial"]["role"], "partial_direct")
            self.assertEqual(meta["artifacts"]["rebuilt"]["role"], "absent")
            self.assertTrue(
                any("snapshot fallback: net events only" in warning for warning in meta["warnings"]),
                meta["warnings"],
            )

    def test_live_parser_fallback_to_post_hoc(self):
        trace = self._trace_text()
        real_feed = TraceParser.feed_line
        state = {"calls": 0}

        def patched_feed(self, line):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("synthetic live-parser failure")
            return real_feed(self, line)

        for strict, expected_rc in [("0", 0), ("1", 1)]:
            state["calls"] = 0
            with self.subTest(strict=strict), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _install_fake_strace(root, trace)
                real = _install_real(root)
                env = _wrapper_env(root, {
                    "CODEV_OBSERVE_REAL_CODEX": str(real),
                    "CODEV_OBSERVE_SESSION_ID": f"fb{strict}",
                    "CODEV_OBSERVE_STRICT_PARSE": strict,
                })
                TraceParser.feed_line = patched_feed
                try:
                    rc, stderr = self._run_in_process(env)
                finally:
                    TraceParser.feed_line = real_feed
                self.assertEqual(rc, expected_rc, stderr)
                self.assertIn("live parser raised RuntimeError", stderr)
                self.assertIn("original exit", stderr)
                jsonl = (root / "obs" / f"fb{strict}.jsonl").read_text(encoding="utf-8")
                events = [json.loads(line) for line in jsonl.splitlines()]
                self.assertEqual([e["path"] for e in events], ["/tmp/work/a.txt", "/tmp/work/b.txt"])

    def test_live_error_then_post_hoc_parser_failure_truncates_jsonl(self):
        trace = self._trace_text()
        real_feed = TraceParser.feed_line
        state = {"calls": 0}

        def patched_feed(self, line):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("synthetic live-parser failure")
            return real_feed(self, line)

        for strict, expected_rc in [("0", 0), ("1", 1)]:
            state["calls"] = 0
            with self.subTest(strict=strict), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _install_fake_strace(root, trace)
                real = _install_real(root)
                env = _wrapper_env(root, {
                    "CODEV_OBSERVE_REAL_CODEX": str(real),
                    "CODEV_OBSERVE_SESSION_ID": f"epf{strict}",
                    "CODEV_OBSERVE_STRICT_PARSE": strict,
                    "CODEV_OBSERVE_TEST_FAIL_AFTER": "1",
                })
                TraceParser.feed_line = patched_feed
                try:
                    rc, stderr = self._run_in_process(env)
                finally:
                    TraceParser.feed_line = real_feed
                self.assertEqual(rc, expected_rc, stderr)
                self.assertIn("live parser raised RuntimeError", stderr)
                self.assertIn("parser failed", stderr)
                self.assertEqual((root / "obs" / f"epf{strict}.jsonl").stat().st_size, 0)
                partial = (root / "obs" / f"epf{strict}.jsonl.partial").read_text(encoding="utf-8")
                partial_events = [json.loads(line) for line in partial.splitlines()]
                self.assertEqual(len(partial_events), 1)

    def test_invalid_fail_after_env_does_not_raise(self):
        trace = self._trace_text()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _install_fake_strace(root, trace)
            real = _install_real(root)
            env = _wrapper_env(root, {
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_SESSION_ID": "badfa",
                "CODEV_OBSERVE_TEST_FAIL_AFTER": "not-a-number",
            })
            rc, stderr = self._run_in_process(env)
            self.assertEqual(rc, 0, stderr)
            self.assertNotIn("ValueError", stderr)
            events = [json.loads(line) for line in (root / "obs" / "badfa.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([e["path"] for e in events], ["/tmp/work/a.txt", "/tmp/work/b.txt"])

    def test_live_start_open_failure_falls_back_to_post_hoc(self):
        trace = self._trace_text()
        real_open = codex_observe.safe_open_trace_read

        def fail_open(path, observe_dir):
            raise codex_observe.ObserveError("simulated open failure", 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _install_fake_strace(root, trace)
            real = _install_real(root)
            env = _wrapper_env(root, {"CODEV_OBSERVE_REAL_CODEX": str(real), "CODEV_OBSERVE_SESSION_ID": "start"})
            codex_observe.safe_open_trace_read = fail_open
            try:
                rc, stderr = self._run_in_process(env)
            finally:
                codex_observe.safe_open_trace_read = real_open
            self.assertEqual(rc, 0, stderr)
            self.assertIn("live tracer failed to start", stderr)
            jsonl = (root / "obs" / "start.jsonl").read_text(encoding="utf-8")
            events = [json.loads(line) for line in jsonl.splitlines()]
            self.assertEqual([e["path"] for e in events], ["/tmp/work/a.txt", "/tmp/work/b.txt"])

    def test_join_timeout_warns_and_preserves_partial_jsonl(self):
        trace = self._trace_text()
        real_run = codex_observe.LiveTracer._run

        def hang_run(self):
            try:
                chunk = self._trace_fh.read(64 * 1024)
                if chunk:
                    parts = chunk.split("\n")
                    for line in parts[:-1]:
                        self._emit(self.parser.feed_line(line))
                while True:
                    time.sleep(0.05)
            except BaseException as exc:
                self.error = exc

        for strict, expected in [("0", 0), ("1", 0)]:
            with self.subTest(strict=strict), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _install_fake_strace(root, trace)
                real = _install_real(root)
                env = _wrapper_env(root, {
                    "CODEV_OBSERVE_REAL_CODEX": str(real),
                    "CODEV_OBSERVE_SESSION_ID": f"to{strict}",
                    "CODEV_OBSERVE_LIVE_JOIN_TIMEOUT": "0.2",
                    "CODEV_OBSERVE_STRICT_PARSE": strict,
                })
                codex_observe.LiveTracer._run = hang_run
                try:
                    rc, stderr = self._run_in_process(env)
                finally:
                    codex_observe.LiveTracer._run = real_run
                self.assertEqual(rc, expected, stderr)
                self.assertIn("did not exit within join timeout", stderr)
                self.assertIn("original exit", stderr)
                self.assertFalse((root / "obs" / f"to{strict}.jsonl.partial").exists())
                rebuilt = root / "obs" / f"to{strict}.jsonl.rebuilt"
                self.assertTrue(rebuilt.exists())
                rebuilt_events = [json.loads(line) for line in rebuilt.read_text(encoding="utf-8").splitlines()]
                self.assertEqual([e["path"] for e in rebuilt_events], ["/tmp/work/a.txt", "/tmp/work/b.txt"])
                meta = json.loads((root / "obs" / f"to{strict}.meta.json").read_text(encoding="utf-8"))
                self.assertEqual(meta["parser"]["status"], "live_timeout_rebuilt")
                self.assertEqual(meta["artifacts"]["authoritative_event_path"], f"to{strict}.jsonl.rebuilt")
                self.assertEqual(meta["artifacts"]["jsonl"]["role"], "partial_live")
                self.assertEqual(meta["artifacts"]["rebuilt"]["role"], "authoritative_complete")

    def test_join_timeout_rebuilt_merges_snapshot_events_into_rebuilt_artifact(self):
        real_run = codex_observe.LiveTracer._run

        def hang_run(self):
            try:
                chunk = self._trace_fh.read(64 * 1024)
                if chunk:
                    parts = chunk.split("\n")
                    for line in parts[:-1]:
                        self._emit(self.parser.feed_line(line))
                while True:
                    time.sleep(0.05)
            except BaseException as exc:
                self.error = exc

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            direct = root / "direct.txt"
            extra = root / "snapshot.txt"
            trace = f'123 1714932000.000001 creat("{direct}", 0600) = 3<{direct}>\\n'
            _install_fake_strace(root, trace)
            real = _install_real(
                root,
                body=(
                    f"from pathlib import Path\n"
                    f"Path({str(direct)!r}).write_text('direct', encoding='utf-8')\n"
                    f"Path({str(extra)!r}).write_text('snapshot', encoding='utf-8')\n"
                ),
            )
            env = _wrapper_env(root, {
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_SESSION_ID": "torebuilt",
                "CODEV_OBSERVE_LIVE_JOIN_TIMEOUT": "0.2",
                "AI_OBSERVE_ROOTS": str(root),
            })
            codex_observe.LiveTracer._run = hang_run
            try:
                rc, stderr = self._run_in_process(env)
            finally:
                codex_observe.LiveTracer._run = real_run
            self.assertEqual(rc, 0, stderr)
            rebuilt = root / "obs" / "torebuilt.jsonl.rebuilt"
            events = [json.loads(line) for line in rebuilt.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                {(event["path"], event["source"], event["operation"]) for event in events},
                {
                    (str(direct), "strace", "create"),
                    (str(extra), "snapshot", "create"),
                },
            )

    def test_join_timeout_rebuilt_filters_direct_events_outside_watched_roots(self):
        real_run = codex_observe.LiveTracer._run

        def hang_run(self):
            try:
                chunk = self._trace_fh.read(64 * 1024)
                if chunk:
                    parts = chunk.split("\n")
                    for line in parts[:-1]:
                        self._emit(self.parser.feed_line(line))
                while True:
                    time.sleep(0.05)
            except BaseException as exc:
                self.error = exc

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inside = root / "inside"
            outside = root / "outside"
            inside.mkdir()
            outside.mkdir()
            direct_inside = inside / "direct.txt"
            direct_outside = outside / "out.txt"
            extra = inside / "snapshot.txt"
            trace = (
                f'123 1714932000.000001 creat("{direct_inside}", 0600) = 3<{direct_inside}>\\n'
                f'123 1714932000.000002 creat("{direct_outside}", 0600) = 4<{direct_outside}>\\n'
            )
            _install_fake_strace(root, trace)
            real = _install_real(
                root,
                body=(
                    f"from pathlib import Path\n"
                    f"Path({str(direct_inside)!r}).write_text('direct', encoding='utf-8')\n"
                    f"Path({str(direct_outside)!r}).write_text('outside', encoding='utf-8')\n"
                    f"Path({str(extra)!r}).write_text('snapshot', encoding='utf-8')\n"
                ),
            )
            env = _wrapper_env(root, {
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_SESSION_ID": "toscope",
                "CODEV_OBSERVE_LIVE_JOIN_TIMEOUT": "0.2",
                "AI_OBSERVE_ROOTS": str(inside),
            })
            codex_observe.LiveTracer._run = hang_run
            try:
                rc, stderr = self._run_in_process(env)
            finally:
                codex_observe.LiveTracer._run = real_run
            self.assertEqual(rc, 0, stderr)
            rebuilt = root / "obs" / "toscope.jsonl.rebuilt"
            events = [json.loads(line) for line in rebuilt.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                {(event["path"], event["source"], event["operation"]) for event in events},
                {
                    (str(direct_inside), "strace", "create"),
                    (str(extra), "snapshot", "create"),
                },
            )

    def test_join_timeout_rebuild_failure_labels_live_jsonl_as_partial(self):
        trace = self._trace_text()
        real_run = codex_observe.LiveTracer._run
        real_parse = codex_observe.parse_trace_file

        def hang_run(self):
            try:
                chunk = self._trace_fh.read(64 * 1024)
                if chunk:
                    parts = chunk.split("\n")
                    for line in parts[:-1]:
                        self._emit(self.parser.feed_line(line))
                while True:
                    time.sleep(0.05)
            except BaseException as exc:
                self.error = exc

        def fail_parse(*_args, **_kwargs):
            raise OSError("synthetic rebuild failure")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _install_fake_strace(root, trace)
            real = _install_real(root)
            env = _wrapper_env(root, {
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_SESSION_ID": "tofail",
                "CODEV_OBSERVE_LIVE_JOIN_TIMEOUT": "0.2",
                "CODEV_OBSERVE_STRICT_PARSE": "1",
            })
            codex_observe.LiveTracer._run = hang_run
            codex_observe.parse_trace_file = fail_parse
            try:
                rc, stderr = self._run_in_process(env)
            finally:
                codex_observe.LiveTracer._run = real_run
                codex_observe.parse_trace_file = real_parse
            self.assertEqual(rc, 1, stderr)
            self.assertIn("timeout rebuild failed", stderr)
            meta = json.loads((root / "obs" / "tofail.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["parser"]["status"], "live_timeout_rebuild_failed")
            self.assertIsNone(meta["artifacts"]["authoritative_event_path"])
            self.assertEqual(meta["artifacts"]["jsonl"]["role"], "partial_live")
            self.assertFalse((root / "obs" / "tofail.jsonl.rebuilt").exists())

    def test_new_artifact_safe_writes_reject_symlink_and_use_private_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            obs = root / "obs"
            obs.mkdir()
            rebuilt = obs / "s.jsonl.rebuilt"
            meta = obs / "s.meta.json"
            codex_observe.safe_write_jsonl(rebuilt, [], obs)
            codex_observe.safe_write_meta(meta, {"ok": True}, obs)
            self.assertEqual(rebuilt.stat().st_mode & 0o777, 0o600)
            self.assertEqual(meta.stat().st_mode & 0o777, 0o600)

            rebuilt.unlink()
            meta.unlink()
            outside_rebuilt = root / "outside.rebuilt"
            outside_meta = root / "outside.meta.json"
            rebuilt.symlink_to(outside_rebuilt)
            meta.symlink_to(outside_meta)
            with self.assertRaises(codex_observe.ObserveError):
                codex_observe.safe_write_jsonl(rebuilt, [], obs)
            with self.assertRaises(codex_observe.ObserveError):
                codex_observe.safe_write_meta(meta, {"ok": True}, obs)

    def test_traced_child_receives_nested_env(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _install_fake_strace(root, "")
            out = root / "nested.txt"
            real = _install_real(
                root,
                f"open({str(out)!r}, 'w', encoding='utf-8').write(os.environ.get('AI_OBSERVE_NESTED', ''))",
            )
            env = _wrapper_env(root, {
                "CODEV_OBSERVE_REAL_CODEX": str(real),
                "CODEV_OBSERVE_SESSION_ID": "nested",
            })
            proc = self._run_wrapper(env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(out.read_text(encoding="utf-8"), "1")

    def test_live_append_failure_falls_back_to_post_hoc(self):
        trace = self._trace_text()
        real_open = codex_observe.safe_append_jsonl_handle

        class _BadHandle:
            def __init__(self, real):
                self._real = real
                self._writes = 0

            def write(self, data):
                self._writes += 1
                if self._writes >= 2:
                    raise OSError("ENOSPC: synthetic")
                return self._real.write(data)

            def flush(self):
                self._real.flush()

            def close(self):
                try:
                    self._real.close()
                except OSError:
                    pass

        def wrap_open(path, observe_dir):
            return _BadHandle(real_open(path, observe_dir))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _install_fake_strace(root, trace)
            real = _install_real(root)
            env = _wrapper_env(root, {"CODEV_OBSERVE_REAL_CODEX": str(real), "CODEV_OBSERVE_SESSION_ID": "lw"})
            codex_observe.safe_append_jsonl_handle = wrap_open
            try:
                rc, stderr = self._run_in_process(env)
            finally:
                codex_observe.safe_append_jsonl_handle = real_open
            self.assertEqual(rc, 0, stderr)
            self.assertIn("live parser raised OSError", stderr)
            jsonl = (root / "obs" / "lw.jsonl").read_text(encoding="utf-8")
            events = [json.loads(line) for line in jsonl.splitlines()]
            self.assertEqual([e["path"] for e in events], ["/tmp/work/a.txt", "/tmp/work/b.txt"])

    def test_double_write_failure_cascade(self):
        trace = self._trace_text()
        real_open = codex_observe.safe_append_jsonl_handle
        real_write = codex_observe.safe_write_jsonl

        class _BadHandle:
            def __init__(self, real):
                self._real = real

            def write(self, data):
                raise OSError("ENOSPC: synthetic")

            def flush(self):
                pass

            def close(self):
                try:
                    self._real.close()
                except OSError:
                    pass

        def wrap_open(path, observe_dir):
            return _BadHandle(real_open(path, observe_dir))

        def fail_write(path, events, observe_dir):
            # Allow writes to partial path to actually succeed so the
            # wrapper can record events; only block the jsonl rewrite.
            if str(path).endswith(".jsonl.partial"):
                return real_write(path, events, observe_dir)
            raise OSError("EROFS: synthetic")

        for strict, expected in [("0", 0), ("1", 1)]:
            with self.subTest(strict=strict), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _install_fake_strace(root, trace)
                real = _install_real(root)
                env = _wrapper_env(root, {
                    "CODEV_OBSERVE_REAL_CODEX": str(real),
                    "CODEV_OBSERVE_SESSION_ID": f"dw{strict}",
                    "CODEV_OBSERVE_STRICT_PARSE": strict,
                })
                codex_observe.safe_append_jsonl_handle = wrap_open
                codex_observe.safe_write_jsonl = fail_write
                try:
                    rc, stderr = self._run_in_process(env)
                finally:
                    codex_observe.safe_append_jsonl_handle = real_open
                    codex_observe.safe_write_jsonl = real_write
                self.assertEqual(rc, expected, stderr)
                self.assertIn("live parser raised OSError", stderr)
                self.assertIn("parser failed", stderr)
                self.assertIn("original exit", stderr)


if __name__ == "__main__":
    unittest.main()
