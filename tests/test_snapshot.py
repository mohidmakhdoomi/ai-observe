from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_observe.snapshot import (  # noqa: E402
    BUILTIN_EXCLUDES,
    ManifestEntry,
    all_exclude_patterns,
    capture_manifest,
    deduplicate_snapshot_events,
    diff_manifests,
    parse_exclude_patterns,
    parse_roots,
    should_exclude,
    synthesize_events,
)


def entry(
    path: str,
    *,
    type: str = "file",
    size: int | None = 1,
    mtime_ns: int = 10,
    ctime_ns: int = 20,
    mode: int = stat.S_IFREG | 0o644,
    root: str | None = "/repo",
    dev: int | None = 1,
    ino: int | None = None,
    symlink_target: str | None = None,
    hash: str | None = None,
) -> ManifestEntry:
    return ManifestEntry(
        path=path,
        type=type,
        size=size,
        mtime_ns=mtime_ns,
        ctime_ns=ctime_ns,
        mode=mode,
        root=root,
        dev=dev,
        ino=ino if ino is not None else abs(hash_path(path)),
        symlink_target=symlink_target,
        hash=hash,
    )


def hash_path(path: str) -> int:
    # Stable enough for test identity without relying on Python's salted hash().
    return sum(ord(ch) for ch in path)


class SnapshotRootAndExcludeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_parse_roots_defaults_to_cwd_and_skips_missing_overlap(self):
        repo = self.root / "repo"
        src = repo / "src"
        src.mkdir(parents=True)
        missing = self.root / "missing"

        roots, diagnostics = parse_roots(f"{src}{os.pathsep}{repo}{os.pathsep}{missing}", cwd=self.root)

        self.assertEqual(roots, [repo.resolve()])
        codes = [d.code for d in diagnostics]
        self.assertIn("missing_root", codes)
        self.assertIn("overlapping_root", codes)

        default_roots, default_diags = parse_roots("", cwd=repo)
        self.assertEqual(default_roots, [repo.resolve()])
        self.assertNotIn("no_roots", [d.code for d in default_diags])

    def test_builtin_excludes_and_lockfiles(self):
        patterns = all_exclude_patterns(None)
        for rel in (
            ".git/config",
            "pkg/node_modules/a.js",
            "src/__pycache__/x.pyc",
            ".codev/observe/session.jsonl",
            "a.pyc",
            "pkg/a.pyo",
            "pkg/.file.swp",
            "pkg/.file.swo",
            "pkg/file~",
            ".DS_Store",
            "pkg/.nfs123",
        ):
            with self.subTest(rel=rel):
                self.assertTrue(should_exclude(rel, patterns))
        for rel in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "Pipfile.lock", "generic.lock"):
            with self.subTest(rel=rel):
                self.assertFalse(should_exclude(rel, patterns))

    def test_user_excludes_are_colon_or_newline_separated(self):
        self.assertEqual(parse_exclude_patterns("build/**:secret\n**/*.tmp"), ["build/**", "secret", "**/*.tmp"])
        patterns = all_exclude_patterns("build/**:secret\n**/*.tmp")
        self.assertTrue(should_exclude("build/out.o", patterns))
        self.assertTrue(should_exclude("src/secret/config", patterns))
        self.assertTrue(should_exclude("a.tmp", patterns))


class SnapshotCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_capture_manifest_fields_excludes_symlink_no_follow_and_hash(self):
        (self.root / "keep.txt").write_text("hello", encoding="utf-8")
        (self.root / "skip.pyc").write_bytes(b"cached")
        real_dir = self.root / "real"
        real_dir.mkdir()
        (real_dir / "inside.txt").write_text("inside", encoding="utf-8")
        os.symlink(real_dir, self.root / "linkdir")

        manifest = capture_manifest([self.root], hash_files=True, exclude_patterns=BUILTIN_EXCLUDES)

        keep = manifest.entries[str(self.root / "keep.txt")]
        self.assertEqual(keep.type, "file")
        self.assertEqual(keep.size, 5)
        self.assertIsInstance(keep.mtime_ns, int)
        self.assertIsInstance(keep.ctime_ns, int)
        self.assertTrue(keep.hash and keep.hash.startswith("sha256:"))
        self.assertEqual(keep.path, str(self.root / "keep.txt"))
        self.assertEqual(manifest.entries[str(self.root / "linkdir")].type, "symlink")
        self.assertNotIn(str(self.root / "linkdir" / "inside.txt"), manifest.entries)
        self.assertNotIn(str(self.root / "skip.pyc"), manifest.entries)
        self.assertEqual(manifest.diagnostics, [])

    def test_max_files_cap_records_diagnostic(self):
        for idx in range(3):
            (self.root / f"{idx}.txt").write_text(str(idx), encoding="utf-8")

        manifest = capture_manifest([self.root], max_files=2)

        self.assertFalse(manifest.complete)
        self.assertEqual(len(manifest.entries), 2)
        self.assertIn("max_files_exceeded", [d.code for d in manifest.diagnostics])

    def test_hash_error_records_diagnostic_without_hash_only_modify_signal(self):
        file_path = self.root / "keep.txt"
        file_path.write_text("hello", encoding="utf-8")

        with mock.patch("ai_observe.snapshot._hash_file", side_effect=OSError("boom")):
            manifest = capture_manifest([self.root], hash_files=True)

        self.assertFalse(manifest.complete)
        self.assertIn("hash_error", [d.code for d in manifest.diagnostics])
        self.assertIsNone(manifest.entries[str(file_path)].hash)

        before = {str(file_path): manifest.entries[str(file_path)]}
        after_entry = ManifestEntry(
            path=str(file_path),
            type="file",
            size=5,
            mtime_ns=before[str(file_path)].mtime_ns,
            ctime_ns=before[str(file_path)].ctime_ns,
            mode=before[str(file_path)].mode,
            root=str(self.root),
            dev=before[str(file_path)].dev,
            ino=before[str(file_path)].ino,
            hash="sha256:ok",
        )
        self.assertEqual(diff_manifests(before, {str(file_path): after_entry}), [])

    def test_unreadable_path_records_diagnostic(self):
        with mock.patch("ai_observe.snapshot.os.scandir", side_effect=OSError("denied")):
            manifest = capture_manifest([self.root])

        self.assertFalse(manifest.complete)
        self.assertEqual(manifest.entries, {})
        self.assertIn("unreadable_path", [d.code for d in manifest.diagnostics])


class SnapshotDiffTests(unittest.TestCase):
    def test_diff_create_modify_delete_metadata_rename_and_ambiguous_pairs(self):
        before = {
            "/repo/delete.txt": entry("/repo/delete.txt", ino=11),
            "/repo/modify.txt": entry("/repo/modify.txt", size=5, mtime_ns=10, ino=12),
            "/repo/meta.txt": entry("/repo/meta.txt", mode=stat.S_IFREG | 0o644, ino=13),
            "/repo/old.txt": entry("/repo/old.txt", ino=14),
            "/repo/ambiguous-old.txt": entry("/repo/ambiguous-old.txt", dev=None, ino=None),
        }
        after = {
            "/repo/create.txt": entry("/repo/create.txt", ino=21),
            "/repo/modify.txt": entry("/repo/modify.txt", size=7, mtime_ns=11, ino=12),
            "/repo/meta.txt": entry("/repo/meta.txt", mode=stat.S_IFREG | 0o600, ino=13),
            "/repo/new.txt": entry("/repo/new.txt", ino=14),
            "/repo/ambiguous-new.txt": entry("/repo/ambiguous-new.txt", dev=None, ino=None),
        }

        records = diff_manifests(before, after)
        ops_by_path = {(r["operation"], r.get("path"), r.get("old_path"), r.get("new_path")) for r in records}

        self.assertIn(("create", "/repo/create.txt", None, None), ops_by_path)
        self.assertIn(("delete", "/repo/delete.txt", None, None), ops_by_path)
        self.assertIn(("modify", "/repo/modify.txt", None, None), ops_by_path)
        self.assertIn(("metadata", "/repo/meta.txt", None, None), ops_by_path)
        self.assertIn(("rename", None, "/repo/old.txt", "/repo/new.txt"), ops_by_path)
        self.assertIn(("delete", "/repo/ambiguous-old.txt", None, None), ops_by_path)
        self.assertIn(("create", "/repo/ambiguous-new.txt", None, None), ops_by_path)

    def test_rename_detection_does_not_cross_root_boundaries(self):
        before = {
            "/repo-a/old.txt": entry("/repo-a/old.txt", root="/repo-a", ino=14),
        }
        after = {
            "/repo-b/new.txt": entry("/repo-b/new.txt", root="/repo-b", ino=14),
        }

        records = diff_manifests(before, after)
        ops_by_path = {(r["operation"], r.get("path"), r.get("old_path"), r.get("new_path")) for r in records}

        self.assertIn(("delete", "/repo-a/old.txt", None, None), ops_by_path)
        self.assertIn(("create", "/repo-b/new.txt", None, None), ops_by_path)
        self.assertNotIn(("rename", None, "/repo-a/old.txt", "/repo-b/new.txt"), ops_by_path)

    def test_ctime_only_does_not_emit_event(self):
        before = {"/repo/a.txt": entry("/repo/a.txt", ctime_ns=20)}
        after = {"/repo/a.txt": entry("/repo/a.txt", ctime_ns=99)}
        self.assertEqual(diff_manifests(before, after), [])

    def test_hash_difference_emits_modify_when_enabled(self):
        before = {"/repo/a.txt": entry("/repo/a.txt", size=4, mtime_ns=10, hash="sha256:a")}
        after = {"/repo/a.txt": entry("/repo/a.txt", size=4, mtime_ns=10, hash="sha256:b")}
        records = diff_manifests(before, after)
        self.assertEqual([r["operation"] for r in records], ["modify"])

    def test_snapshot_events_are_schema_v2_inferred_without_process_attribution(self):
        before = {"/repo/old.txt": entry("/repo/old.txt", ino=100)}
        after = {"/repo/new.txt": entry("/repo/new.txt", ino=100)}
        records = diff_manifests(before, after)
        events = synthesize_events(records, session_id="s", timestamp="2026-05-19T13:00:00Z")

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["schema_version"], 2)
        self.assertEqual(event["source"], "snapshot")
        self.assertEqual(event["confidence"], "inferred")
        self.assertEqual(event["operation"], "rename")
        self.assertEqual(event["old_path"], "/repo/old.txt")
        self.assertEqual(event["new_path"], "/repo/new.txt")
        self.assertEqual(event["path"], "/repo/new.txt")
        self.assertEqual(event["object"], {"dev": 1, "ino": 100})
        self.assertIn("before", event["snapshot"])
        self.assertIn("after", event["snapshot"])
        for forbidden in ("pid", "process", "command", "raw_syscall"):
            self.assertNotIn(forbidden, event)


class SnapshotDedupTests(unittest.TestCase):
    def test_deduplicate_snapshot_events_uses_spec_operation_rules(self):
        snapshot_events = [
            {"operation": "create", "path": "/repo/create.txt", "source": "snapshot"},
            {"operation": "modify", "path": "/repo/modify.txt", "source": "snapshot"},
            {"operation": "delete", "path": "/repo/delete.txt", "source": "snapshot"},
            {"operation": "metadata", "path": "/repo/meta.txt", "source": "snapshot"},
            {"operation": "rename", "old_path": "/repo/old.txt", "new_path": "/repo/new.txt", "path": "/repo/new.txt", "source": "snapshot"},
            {"operation": "delete", "path": "/repo/keep-delete.txt", "source": "snapshot"},
        ]
        direct_events = [
            {"operation": "create", "path": "/repo/create.txt"},
            {"operation": "create", "path": "/repo/modify.txt"},
            {"operation": "delete", "path": "/repo/delete.txt"},
            {"operation": "metadata", "path": "/repo/meta.txt"},
            {"operation": "rename", "old_path": "/repo/old.txt", "new_path": "/repo/new.txt"},
            {"operation": "modify", "path": "/repo/keep-delete.txt"},
        ]

        filtered = deduplicate_snapshot_events(snapshot_events, direct_events)

        self.assertEqual(filtered, [{"operation": "delete", "path": "/repo/keep-delete.txt", "source": "snapshot"}])


if __name__ == "__main__":
    unittest.main()
