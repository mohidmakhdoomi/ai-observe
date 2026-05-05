from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_observe.trace_parser import ParserFailure, parse_trace_file


class TraceParserTests(unittest.TestCase):
    def parse(self, text, cwd="/tmp/work", artifacts=(), **kw):
        with tempfile.TemporaryDirectory() as td:
            trace = Path(td) / "trace.txt"
            out = Path(td) / "out.jsonl"
            trace.write_text(text, encoding="utf-8")
            result = parse_trace_file(
                trace,
                out,
                session_id="s1",
                invocation_id="s1",
                command=["/real/codex", "arg"],
                initial_cwd=cwd,
                active_artifacts=artifacts,
                **kw,
            )
            lines = out.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), len(result.events))
            return result.events

    def ops(self, events):
        return [e["operation"] for e in events]

    def test_committed_fixture_files_parse(self):
        fixtures = {
            "basic.strace": ["create", "modify"],
            "single.strace": ["create"],
            "unfinished.strace": ["create"],
            "fd_yy.strace": ["create"],
            "openat2_pwritev2.strace": ["create", "modify"],
        }
        fixture_dir = ROOT / "tests" / "fixtures" / "strace"
        for name, expected_ops in fixtures.items():
            with self.subTest(name=name):
                text = (fixture_dir / name).read_text(encoding="utf-8")
                self.assertIn("strace", text.splitlines()[0])
                events = self.parse(text)
                self.assertEqual(self.ops(events), expected_ops)

    def test_create_requires_excl_plain_creat_not_create(self):
        events = self.parse("""
123 1714932000.000001 openat(AT_FDCWD, "plain.txt", O_WRONLY|O_CREAT, 0666) = 3</tmp/work/plain.txt>
123 1714932000.000002 openat(AT_FDCWD, "new.txt", O_WRONLY|O_CREAT|O_EXCL, 0600) = 4</tmp/work/new.txt>
""")
        self.assertEqual(self.ops(events), ["create"])
        self.assertEqual(events[0]["path"], "/tmp/work/new.txt")

    def test_modify_write_zero_write_and_truncate_open(self):
        events = self.parse("""
123 1714932000.000001 openat(AT_FDCWD, "file.txt", O_WRONLY, 0666) = 3</tmp/work/file.txt>
123 1714932000.000002 write(3</tmp/work/file.txt>, "", 0) = 0
123 1714932000.000003 write(3</tmp/work/file.txt>, "x", 1) = 1
123 1714932000.000004 openat(AT_FDCWD, "trunc.txt", O_WRONLY|O_TRUNC) = 4</tmp/work/trunc.txt>
""")
        self.assertEqual(self.ops(events), ["modify", "modify"])
        self.assertEqual(events[0]["path"], "/tmp/work/file.txt")
        self.assertEqual(events[1]["path"], "/tmp/work/trunc.txt")

    def test_delete_rename_chmod_metadata(self):
        events = self.parse("""
123 1714932000.000001 unlink("gone.txt") = 0
123 1714932000.000002 rename("old.txt", "new.txt") = 0
123 1714932000.000003 chmod("new.txt", 0600) = 0
123 1714932000.000004 utimensat(AT_FDCWD, "new.txt", NULL, 0) = 0
""")
        self.assertEqual(self.ops(events), ["delete", "rename", "chmod", "metadata"])
        self.assertEqual(events[1]["old_path"], "/tmp/work/old.txt")
        self.assertEqual(events[1]["new_path"], "/tmp/work/new.txt")

    def test_openat2_and_pwritev2(self):
        text = (
            '123 1714932000.000001 openat2(AT_FDCWD, "n", {flags=O_WRONLY|O_CREAT|O_EXCL, mode=0600}, 24) = 3</tmp/work/n>\n'
            '123 1714932000.000002 pwritev2(3</tmp/work/n>, [{iov_base="x", iov_len=1}], 1, 0, 0) = 1\n'
        )
        events = self.parse(text)
        self.assertEqual(self.ops(events), ["create", "modify"])

    def test_dotdot_paths_are_normalized(self):
        events = self.parse('123 1714932000.000001 creat("../work/./x", 0600) = 3</tmp/work/x>\n')
        self.assertEqual(events[0]["path"], "/tmp/work/x")

    def test_failed_syscalls_ignored(self):
        events = self.parse('123 1714932000.000001 unlink("missing") = -1 ENOENT (No such file or directory)\n')
        self.assertEqual(events, [])

    def test_single_process_line_and_schema_fields(self):
        events = self.parse('1714932000.000001 creat("x", 0600) = 3</tmp/work/x>\n')
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["schema_version"], 1)
        self.assertTrue(e["timestamp"].endswith("Z"))
        self.assertEqual(e["session_id"], "s1")
        self.assertEqual(e["invocation_id"], "s1")
        self.assertIsNone(e["pid"])
        self.assertEqual(e["process"]["pid"], None)
        self.assertEqual(e["command"], ["/real/codex", "arg"])
        self.assertIn("creat", e["raw_syscall"])
        self.assertEqual(e["result"], 3)

    def test_unfinished_resumed_and_malformed_skip(self):
        events = self.parse("""
123 1714932000.000001 openat(AT_FDCWD, "u.txt", O_WRONLY|O_CREAT|O_EXCL <unfinished ...>
999 1714932000.000001 <... nope resumed> ) = 0
123 1714932000.000002 <... openat resumed> , 0600) = 3</tmp/work/u.txt>
""")
        self.assertEqual(self.ops(events), ["create"])

    def test_chdir_fchdir_relative_and_dirfd_at(self):
        events = self.parse("""
123 1714932000.000001 chdir("/tmp/work/sub") = 0
123 1714932000.000002 creat("rel.txt", 0600) = 3</tmp/work/sub/rel.txt>
123 1714932000.000003 openat(AT_FDCWD, "/tmp/work/dir", O_RDONLY|O_DIRECTORY) = 4</tmp/work/dir>
123 1714932000.000004 fchdir(4</tmp/work/dir>) = 0
123 1714932000.000005 creat("after-fchdir.txt", 0600) = 5</tmp/work/dir/after-fchdir.txt>
123 1714932000.000006 mkdirat(4</tmp/work/dir>, "child", 0777) = 0
123 1714932000.000007 unlinkat(99, "unknown", 0) = 0
""")
        self.assertEqual([e["path"] for e in events], [
            "/tmp/work/sub/rel.txt",
            "/tmp/work/dir/after-fchdir.txt",
            "/tmp/work/dir/child",
            None,
        ])

    def test_rename_partial_resolution(self):
        events = self.parse('123 1714932000.000001 renameat(99, "old", AT_FDCWD, "new") = 0\n')
        self.assertEqual(events[0]["operation"], "rename")
        self.assertIsNone(events[0]["old_path"])
        self.assertEqual(events[0]["new_path"], "/tmp/work/new")
        self.assertEqual(events[0]["path"], "/tmp/work/new")

    def test_artifact_exclusion_only_active_paths(self):
        events = self.parse("""
123 1714932000.000001 creat("/tmp/work/.codev/observe/session.trace", 0600) = 3</tmp/work/.codev/observe/session.trace>
123 1714932000.000002 creat("/tmp/work/.codev/observe/other", 0600) = 4</tmp/work/.codev/observe/other>
""", artifacts=["/tmp/work/.codev/observe/session.trace"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["path"], "/tmp/work/.codev/observe/other")

    def test_parser_failure_carries_partial_events(self):
        with tempfile.TemporaryDirectory() as td:
            trace = Path(td) / "trace.txt"
            trace.write_text('123 1714932000.000001 creat("x", 0600) = 3</tmp/work/x>\n', encoding="utf-8")
            with self.assertRaises(ParserFailure) as ctx:
                parse_trace_file(trace, None, session_id="s1", command=[], initial_cwd="/tmp/work", fail_after_events=1)
            self.assertEqual(len(ctx.exception.events), 1)


if __name__ == "__main__":
    unittest.main()
