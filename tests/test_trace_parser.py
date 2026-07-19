from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_observe.trace_parser import ParserFailure, TraceParser, dump_event, parse_trace_file


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
        self.assertEqual(events[1]["result"], 0)

    def test_splice_to_dup_writable_fd_counts_bytes(self):
        events = self.parse("""
123 1714932000.000001 openat(AT_FDCWD, "largefile.csv", O_WRONLY|O_CREAT|O_TRUNC, 0666) = 3</tmp/work/largefile.csv>
123 1714932000.000002 dup2(3</tmp/work/largefile.csv>, 1</tmp/work/stdout>) = 1</tmp/work/largefile.csv>
123 1714932000.000003 close(3</tmp/work/largefile.csv>) = 0
123 1714932000.000004 splice(0<pipe:[123]>, NULL, 1</tmp/work/largefile.csv>, NULL, 12, 0 <unfinished ...>
123 1714932000.000005 <... splice resumed>) = 7
123 1714932000.000006 splice(0<pipe:[123]>, NULL, 1</tmp/work/largefile.csv>, NULL, 5, 0) = 5
""")
        file_events = [e for e in events if e["path"] == "/tmp/work/largefile.csv"]
        self.assertEqual(self.ops(file_events), ["modify", "modify", "modify"])
        self.assertEqual([e["result"] for e in file_events], [0, 7, 5])
        self.assertEqual(sum(e["result"] for e in file_events if e["result"] > 0), 12)

    def test_copy_file_range_and_sendfile_modify_known_destination(self):
        events = self.parse("""
123 1714932000.000001 openat(AT_FDCWD, "src", O_RDONLY) = 3</tmp/work/src>
123 1714932000.000002 openat(AT_FDCWD, "dst", O_WRONLY|O_CREAT, 0666) = 4</tmp/work/dst>
123 1714932000.000003 copy_file_range(3</tmp/work/src>, NULL, 4</tmp/work/dst>, NULL, 11, 0) = 11
123 1714932000.000004 sendfile(4</tmp/work/dst>, 3</tmp/work/src>, NULL, 7) = 7
123 1714932000.000005 copy_file_range(3</tmp/work/src>, NULL, 99, NULL, 11, 0) = 11
""")
        self.assertEqual(self.ops(events), ["modify", "modify"])
        self.assertEqual([e["path"] for e in events], ["/tmp/work/dst", "/tmp/work/dst"])
        self.assertEqual([e["result"] for e in events], [11, 7])

    def test_xattr_operations_emit_metadata_when_target_known(self):
        events = self.parse("""
123 1714932000.000001 openat(AT_FDCWD, "x", O_RDWR) = 3</tmp/work/x>
123 1714932000.000002 setxattr("x", "user.k", "v", 1, 0) = 0
123 1714932000.000003 lsetxattr("link", "user.k", "v", 1, 0) = 0
123 1714932000.000004 fsetxattr(3</tmp/work/x>, "user.k", "v", 1, 0) = 0
123 1714932000.000005 removexattr("x", "user.k") = 0
123 1714932000.000006 lremovexattr("link", "user.k") = 0
123 1714932000.000007 fremovexattr(3</tmp/work/x>, "user.k") = 0
""")
        self.assertEqual(self.ops(events), ["metadata"] * 6)
        self.assertEqual(
            [e["path"] for e in events],
            ["/tmp/work/x", "/tmp/work/link", "/tmp/work/x", "/tmp/work/x", "/tmp/work/link", "/tmp/work/x"],
        )

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
        self.assertEqual(e["schema_version"], 2)
        self.assertEqual(e["source"], "strace")
        self.assertEqual(e["confidence"], "direct")
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

    def test_truncated_final_lines_are_safe_false_negatives(self):
        parser = self._new_parser()
        result = parser.parse_lines([
            '123 1714932000.000001 creat("ok", 0600) = 3</tmp/work/ok>\n',
            '123 1714932000.000002 openat(AT_FDCWD, "unfinished", O_WRONLY|O_CREAT|O_EXCL <unfinished ...>\n',
            '123 1714932000.000003 creat("truncated"',
        ])
        self.assertEqual(self.ops(result.events), ["create"])
        self.assertEqual(result.events[0]["path"], "/tmp/work/ok")
        self.assertTrue(any("unparsed body" in err for err in result.errors))

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

    def test_unlinkat_annotated_at_fdcwd_emits_delete(self):
        events = self.parse(
            '123 1714932000.000001 unlinkat(AT_FDCWD</tmp/work>, "f.txt", 0) = 0\n',
            watched_roots=["/tmp/work"],
        )
        self.assertEqual(self.ops(events), ["delete"])
        self.assertEqual(events[0]["path"], "/tmp/work/f.txt")

    def test_annotated_at_fdcwd_wins_over_tracked_cwd(self):
        events = self.parse('123 1714932000.000001 unlinkat(AT_FDCWD</elsewhere>, "f.txt", 0) = 0\n')
        self.assertEqual(self.ops(events), ["delete"])
        self.assertEqual(events[0]["path"], "/elsewhere/f.txt")

    def test_empty_at_fdcwd_annotation_falls_back_to_tracked_cwd(self):
        events = self.parse('123 1714932000.000001 unlinkat(AT_FDCWD<>, "f.txt", 0) = 0\n')
        self.assertEqual(self.ops(events), ["delete"])
        self.assertEqual(events[0]["path"], "/tmp/work/f.txt")

    def test_absolute_path_ignores_annotated_at_fdcwd_dirfd(self):
        events = self.parse('123 1714932000.000001 unlinkat(AT_FDCWD</elsewhere>, "/tmp/work/abs.txt", 0) = 0\n')
        self.assertEqual(self.ops(events), ["delete"])
        self.assertEqual(events[0]["path"], "/tmp/work/abs.txt")

    def test_watched_roots_drop_outside_and_cross_boundary_direct_events(self):
        events = self.parse("""
123 1714932000.000001 creat("/tmp/work/inside/keep.txt", 0600) = 3</tmp/work/inside/keep.txt>
123 1714932000.000002 creat("/tmp/work/outside/drop.txt", 0600) = 4</tmp/work/outside/drop.txt>
123 1714932000.000003 rename("/tmp/work/inside/move-out.txt", "/tmp/work/outside/move-out.txt") = 0
123 1714932000.000004 rename("/tmp/work/outside/move-in.txt", "/tmp/work/inside/move-in.txt") = 0
123 1714932000.000005 rename("/tmp/work/inside/old.txt", "/tmp/work/inside/new.txt") = 0
""", watched_roots=["/tmp/work/inside"])
        self.assertEqual(
            [(event["operation"], event["path"]) for event in events],
            [
                ("create", "/tmp/work/inside/keep.txt"),
                ("rename", "/tmp/work/inside/new.txt"),
            ],
        )

    def test_artifact_exclusion_only_active_paths(self):
        events = self.parse("""
123 1714932000.000001 creat("/tmp/work/.codev/observe/session.trace", 0600) = 3</tmp/work/.codev/observe/session.trace>
123 1714932000.000002 creat("/tmp/work/.codev/observe/other", 0600) = 4</tmp/work/.codev/observe/other>
""", artifacts=["/tmp/work/.codev/observe/session.trace"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["path"], "/tmp/work/.codev/observe/other")

    def test_dump_event_matches_writer_output(self):
        events = self.parse('123 1714932000.000001 creat("x", 0600) = 3</tmp/work/x>\n')
        self.assertEqual(len(events), 1)
        expected = json.dumps(events[0], sort_keys=True, separators=(",", ":")) + "\n"
        self.assertEqual(dump_event(events[0]), expected)

    def _new_parser(self, cwd="/tmp/work"):
        return TraceParser(
            session_id="s1",
            invocation_id="s1",
            command=["/real/codex"],
            initial_cwd=cwd,
            active_artifacts=set(),
            include_log_writes=False,
        )

    def test_feed_line_emits_one_event(self):
        parser = self._new_parser()
        new = parser.feed_line('123 1714932000.000001 creat("x", 0600) = 3</tmp/work/x>\n')
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["operation"], "create")
        self.assertEqual(new[0]["path"], "/tmp/work/x")

    def test_feed_line_skips_blank_and_partial(self):
        parser = self._new_parser()
        self.assertEqual(parser.feed_line("\n"), [])
        self.assertEqual(parser.feed_line("   \n"), [])
        # Unfinished line stashes; no event yet.
        self.assertEqual(
            parser.feed_line('123 1714932000.000001 openat(AT_FDCWD, "u.txt", O_WRONLY|O_CREAT|O_EXCL <unfinished ...>\n'),
            [],
        )
        # Resumed line lands one stitched event.
        new = parser.feed_line('123 1714932000.000002 <... openat resumed> , 0600) = 3</tmp/work/u.txt>\n')
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["operation"], "create")
        self.assertEqual(new[0]["path"], "/tmp/work/u.txt")

    def test_feed_line_propagates_parser_failure(self):
        parser = TraceParser(
            session_id="s1",
            invocation_id="s1",
            command=[],
            initial_cwd="/tmp/work",
            active_artifacts=set(),
            include_log_writes=False,
            fail_after_events=1,
        )
        with self.assertRaises(ParserFailure):
            parser.feed_line('123 1714932000.000001 creat("x", 0600) = 3</tmp/work/x>\n')

    def test_feed_line_equivalent_to_parse_lines(self):
        text = (
            '123 1714932000.000001 openat(AT_FDCWD, "n", O_WRONLY|O_CREAT|O_EXCL, 0600) = 3</tmp/work/n>\n'
            '123 1714932000.000002 write(3</tmp/work/n>, "x", 1) = 1\n'
            '123 1714932000.000003 unlink("n") = 0\n'
        )
        a = self._new_parser()
        a.parse_lines(text.splitlines(keepends=True))
        b = self._new_parser()
        collected = []
        for line in text.splitlines(keepends=True):
            collected.extend(b.feed_line(line))
        self.assertEqual(a.events, b.events)
        self.assertEqual(collected, b.events)

    def test_parser_failure_carries_partial_events(self):
        with tempfile.TemporaryDirectory() as td:
            trace = Path(td) / "trace.txt"
            trace.write_text('123 1714932000.000001 creat("x", 0600) = 3</tmp/work/x>\n', encoding="utf-8")
            with self.assertRaises(ParserFailure) as ctx:
                parse_trace_file(trace, None, session_id="s1", command=[], initial_cwd="/tmp/work", fail_after_events=1)
            self.assertEqual(len(ctx.exception.events), 1)


if __name__ == "__main__":
    unittest.main()
