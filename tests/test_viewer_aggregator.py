"""Aggregator behavior + JS parity for the ai_observe viewer.

`Aggregator` (Python) in `tests/_aggregator_oracle.py` is the test oracle.
`aggregator.js` is the canonical browser implementation. `JsParityTests`
runs both against the same fixture under Node (if available) and asserts
identical snapshots; the test is skipped if `node` is not on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))  # tests/_aggregator_oracle.py is here
sys.path.insert(0, str(ROOT / "src"))

from _aggregator_oracle import (  # noqa: E402
    Aggregator,
    FACTORY_FILTER_PATTERNS,
    compile_filter_pattern,
    event_is_noise,
    event_matches_filters,
    is_filtered_path,
    is_noise,
    RECENCY_HALF_LIFE_MS,
    validate_filter_pattern,
)


FIXTURES = ROOT / "tests" / "fixtures" / "viewer"


def _load_jsonl(path: Path):
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        events.append(obj)
    return events


def _find_node(tree, path):
    if tree["path"] == path:
        return tree
    for c in tree["children"]:
        r = _find_node(c, path)
        if r is not None:
            return r
    return None


def _all_paths(tree):
    out = []
    def walk(n):
        out.append(n["path"])
        for c in n["children"]:
            walk(c)
    walk(tree)
    return out


class FilterGlobTests(unittest.TestCase):
    def assertMatches(self, pattern, matches, non_matches):
        rx = compile_filter_pattern(pattern)
        for path in matches:
            with self.subTest(pattern=pattern, path=path, expected=True):
                self.assertIsNotNone(rx.match(path))
        for path in non_matches:
            with self.subTest(pattern=pattern, path=path, expected=False):
                self.assertIsNone(rx.match(path))

    def test_factory_filter_patterns_are_globs(self):
        self.assertEqual(FACTORY_FILTER_PATTERNS[0], "/home/*/.codex/**")
        self.assertIn("/tmp/**", FACTORY_FILTER_PATTERNS)
        # Backward-compatible helper names still use the factory filters.
        self.assertTrue(is_noise("/home/user/.codex/tmp/x"))
        self.assertTrue(is_noise("/home/user/.codex"))
        self.assertTrue(is_noise("/tmp"))
        self.assertTrue(is_noise("/tmp/whatever"))
        self.assertFalse(is_noise("/home/user/code/x"))

    def test_spec_glob_examples(self):
        self.assertMatches("/tmp/**", ["/tmp", "/tmp/a", "/tmp/a/b"], ["/tmpish", "/var/tmp/a"])
        self.assertMatches(
            "/home/*/.cache/**",
            ["/home/alice/.cache", "/home/bob/.cache/pip/x"],
            ["/home/alice/project/.cache", "/home/alice/.cachex"],
        )
        self.assertMatches("/work/build/*", ["/work/build/a.o"], ["/work/build", "/work/build/obj/a.o"])
        self.assertMatches("/work/build/**", ["/work/build", "/work/build/a.o", "/work/build/obj/a.o"], ["/work/building/a.o"])
        self.assertMatches("/work/?.txt", ["/work/a.txt"], ["/work/ab.txt", "/work/dir/a.txt"])

    def test_double_star_middle_matches_zero_or_more_segments(self):
        self.assertMatches("/a/**/b", ["/a/b", "/a/x/b", "/a/x/y/b"], ["/a/x/b/c", "/ax/b"])

    def test_literal_regex_metacharacters_are_escaped(self):
        self.assertMatches(
            "/work/a+b.[txt]",
            ["/work/a+b.[txt]"],
            ["/work/ab.t", "/work/aaab.txt", "/work/a+bxtxt"],
        )

    def test_invalid_empty_whitespace_and_relative_patterns(self):
        for pattern in ("", "   ", "tmp/**"):
            with self.subTest(pattern=pattern):
                ok, _normalized, error = validate_filter_pattern(pattern)
                self.assertFalse(ok)
                self.assertIsInstance(error, str)
                with self.assertRaises(ValueError):
                    compile_filter_pattern(pattern)

    def test_exact_directory_filter_does_not_match_descendants(self):
        exact = compile_filter_pattern("/work/build")
        subtree = compile_filter_pattern("/work/build/**")
        self.assertIsNotNone(exact.match("/work/build"))
        self.assertIsNone(exact.match("/work/build/out.log"))
        self.assertIsNotNone(subtree.match("/work/build"))
        self.assertIsNotNone(subtree.match("/work/build/out.log"))


class NoiseFilterTests(unittest.TestCase):
    def test_is_noise_matches_codex_tmp(self):
        self.assertTrue(is_noise("/home/user/.codex/tmp/x"))
        self.assertTrue(is_noise("/home/user/.codex"))
        self.assertTrue(is_noise("/proc/1/status"))
        self.assertTrue(is_noise("/tmp/whatever"))
        self.assertFalse(is_noise("/home/user/code/x"))
        self.assertFalse(is_noise(None))
        self.assertFalse(is_noise(""))

    def test_event_is_noise_requires_all_non_null_paths_to_match(self):
        # All paths in noise → event is noise.
        ev = {"path": "/tmp/a", "old_path": None, "new_path": None}
        self.assertTrue(event_is_noise(ev))
        # Mixed → event is NOT noise.
        ev = {"path": "/tmp/a", "old_path": "/home/user/code/x", "new_path": None}
        self.assertFalse(event_is_noise(ev))
        # No paths at all → not noise.
        self.assertFalse(event_is_noise({"path": None, "old_path": None, "new_path": None}))


class CustomFilterTests(unittest.TestCase):
    def _event(self, path, idx=0, op="modify", old_path=None, new_path=None, result=1):
        return {
            "schema_version": 1,
            "timestamp": f"2026-05-13T10:00:{idx:02d}.000000Z",
            "operation": op,
            "path": path,
            "old_path": old_path,
            "new_path": new_path,
            "result": result,
        }

    def test_custom_filters_control_event_counts_and_snapshot_paths(self):
        agg = Aggregator(filter_patterns=["/secret/**"])
        agg.ingest(self._event("/secret/a.txt", idx=0, result=7))
        agg.ingest(self._event("/work/a.txt", idx=1, result=11))

        filtered = agg.snapshot(include_noise=False)
        shown = agg.snapshot(include_noise=True)
        self.assertEqual(filtered["total_event_count"], 2)
        self.assertEqual(filtered["filtered_event_count"], 1)
        self.assertIsNone(_find_node(filtered["tree"], "/secret/a.txt"))
        self.assertIsNotNone(_find_node(filtered["tree"], "/work/a.txt"))
        self.assertIsNotNone(_find_node(shown["tree"], "/secret/a.txt"))

    def test_v2_provenance_fields_do_not_change_core_aggregation(self):
        agg = Aggregator()
        ev = self._event("/work/v2.txt", idx=0, result=7)
        ev.update({"schema_version": 2, "source": "strace", "confidence": "direct"})
        agg.ingest(ev)
        snap = agg.snapshot(include_noise=False)
        node = _find_node(snap["tree"], "/work/v2.txt")
        self.assertIsNotNone(node)
        self.assertEqual(node["events"], 1)
        self.assertEqual(node["bytes"], 7)
        self.assertEqual(node["sources"], ["strace"])
        self.assertEqual(node["confidences"], ["direct"])

    def test_mixed_v1_v2_sources_roll_up_provenance(self):
        agg = Aggregator()
        for ev in _load_jsonl(FIXTURES / "mixed_sources.jsonl"):
            agg.ingest(ev)
        snap = agg.snapshot()
        mixed = _find_node(snap["tree"], "/work/mixed.txt")
        work = _find_node(snap["tree"], "/work")
        self.assertEqual(mixed["sources"], ["strace", "snapshot"])
        self.assertEqual(mixed["confidences"], ["direct", "inferred"])
        self.assertEqual(work["sources"], ["strace", "snapshot"])
        self.assertEqual(work["confidences"], ["direct", "inferred"])

    def test_source_visibility_replays_only_selected_sources(self):
        agg = Aggregator(enabled_sources=["snapshot"])
        for ev in _load_jsonl(FIXTURES / "mixed_sources.jsonl"):
            agg.ingest(ev)
        snap = agg.snapshot()
        self.assertEqual(snap["enabled_sources"], ["snapshot"])
        self.assertIsNone(_find_node(snap["tree"], "/work/direct.txt"))
        inferred = _find_node(snap["tree"], "/work/inferred.txt")
        mixed = _find_node(snap["tree"], "/work/mixed.txt")
        self.assertEqual(inferred["sources"], ["snapshot"])
        self.assertEqual(mixed["sources"], ["snapshot"])
        self.assertEqual(mixed["bytes"], 11)

    def test_custom_event_filtering_uses_all_paths_match_rule(self):
        secret_rx = [compile_filter_pattern("/secret/**")]
        self.assertTrue(event_matches_filters(self._event("/secret/a"), secret_rx))
        self.assertFalse(event_matches_filters(self._event(None, old_path="/secret/a", new_path="/work/a"), secret_rx))
        self.assertFalse(event_matches_filters({"path": None, "old_path": None, "new_path": None}, secret_rx))

        agg = Aggregator(filter_patterns=["/secret/**"])
        agg.ingest(self._event("/secret/a", idx=0))
        agg.ingest(self._event(None, idx=1, op="rename", old_path="/secret/a", new_path="/work/a"))
        self.assertEqual(agg.snapshot()["filtered_event_count"], 1)

    def test_exact_directory_filter_snapshot_does_not_hide_descendants(self):
        agg = Aggregator(filter_patterns=["/work/build"])
        agg.ingest(self._event("/work/build/out.log", idx=0))
        snap = agg.snapshot(include_noise=False)
        self.assertIsNotNone(_find_node(snap["tree"], "/work/build/out.log"))

        subtree = Aggregator(filter_patterns=["/work/build/**"])
        subtree.ingest(self._event("/work/build/out.log", idx=0))
        self.assertIsNone(_find_node(subtree.snapshot(include_noise=False)["tree"], "/work/build/out.log"))
        self.assertIsNotNone(_find_node(subtree.snapshot(include_noise=True)["tree"], "/work/build/out.log"))

    def test_tombstones_win_even_when_filtered_paths_are_shown(self):
        agg = Aggregator(filter_patterns=["/p/**"])
        agg.ingest(self._event("/p/tmp", idx=0, result=3))
        agg.ingest(self._event(None, idx=1, op="rename", old_path="/p/tmp", new_path="/p/final", result=0))
        shown_paths = _all_paths(agg.snapshot(include_noise=True)["tree"])
        self.assertNotIn("/p/tmp", shown_paths)
        self.assertIn("/p/final", shown_paths)


class SnapshotGoldenTests(unittest.TestCase):
    def _snapshot_for(self, fixture_name, metric):
        agg = Aggregator()
        for ev in _load_jsonl(FIXTURES / f"{fixture_name}.jsonl"):
            agg.ingest(ev)
        return agg.snapshot(metric=metric, include_noise=False)

    def test_committed_golden_snapshots_match_fixtures(self):
        for fixture_name in ("basic", "rename_chain"):
            for metric in ("bytes", "events", "recent"):
                with self.subTest(fixture=fixture_name, metric=metric):
                    expected_path = FIXTURES / "golden" / f"{fixture_name}_{metric}.json"
                    expected = json.loads(expected_path.read_text(encoding="utf-8"))
                    self.assertEqual(self._snapshot_for(fixture_name, metric), expected)


class BasicAggregationTests(unittest.TestCase):
    def setUp(self):
        self.agg = Aggregator()
        for ev in _load_jsonl(FIXTURES / "basic.jsonl"):
            self.agg.ingest(ev)
        self.snap = self.agg.snapshot()

    def test_total_and_filtered_counts(self):
        # basic.jsonl has 16 valid mixed-schema events; none are filtered by default.
        self.assertEqual(self.snap["total_event_count"], 16)
        self.assertEqual(self.snap["filtered_event_count"], 0)

    def test_bytes_metric_for_known_path(self):
        # /work/c.txt: two modify events with result 300 and 150.
        node = _find_node(self.snap["tree"], "/work/c.txt")
        self.assertIsNotNone(node)
        self.assertEqual(node["bytes"], 300 + 150)

    def test_rename_migrates_bytes_to_destination(self):
        # /work/b.tmp was modify=200, then renamed to /work/b.txt.
        # Then /work/d.tmp was modify=99, then renamed to /work/b.txt
        # (collision case). Destination /work/b.txt accumulates both.
        node = _find_node(self.snap["tree"], "/work/b.txt")
        self.assertIsNotNone(node)
        self.assertEqual(node["bytes"], 200 + 99)
        # Source paths must be absent from the rendered tree.
        all_paths = _all_paths(self.snap["tree"])
        self.assertNotIn("/work/b.tmp", all_paths)
        self.assertNotIn("/work/d.tmp", all_paths)

    def test_directory_sum_rolls_up(self):
        work = _find_node(self.snap["tree"], "/work")
        self.assertIsNotNone(work)
        # /work bytes = a.txt(150) + b.txt(299) + c.txt(450) = 899
        self.assertEqual(work["bytes"], 899)


class SnapshotNoiseToggleTests(unittest.TestCase):
    def setUp(self):
        self.agg = Aggregator()
        for ev in _load_jsonl(FIXTURES / "noise.jsonl"):
            self.agg.ingest(ev)

    def test_include_noise_toggles_paths_without_replay(self):
        filtered = self.agg.snapshot(include_noise=False)
        noisy = self.agg.snapshot(include_noise=True)
        self.assertEqual(filtered["total_event_count"], 4)
        self.assertEqual(filtered["filtered_event_count"], 2)
        self.assertIsNone(_find_node(filtered["tree"], "/home/user/.codex/tmp/noise.txt"))
        self.assertIsNotNone(_find_node(noisy["tree"], "/home/user/.codex/tmp/noise.txt"))
        self.assertEqual(_find_node(noisy["tree"], "/home/user/.codex/tmp/noise.txt")["bytes"], 77)
        # Mixed-path events are not counted as filtered and remain visible in
        # the filtered snapshot because at least one path is non-noise.
        self.assertIsNotNone(_find_node(filtered["tree"], "/work/from-noise.txt"))


class RenameChainTests(unittest.TestCase):
    def setUp(self):
        self.agg = Aggregator()
        for ev in _load_jsonl(FIXTURES / "rename_chain.jsonl"):
            self.agg.ingest(ev)
        self.snap = self.agg.snapshot()

    def test_destination_accumulates_collision(self):
        node = _find_node(self.snap["tree"], "/p/final")
        self.assertIsNotNone(node)
        # /p/final pre-rename: modify=15
        # /p/tmp.x: create + modify(40) + modify(60) -> bytes=100, events=3
        # rename: dst.events += src.events + 1 = (1) + 3 + 1 = 5, dst.bytes += 100 -> 115
        # post-rename modify(25): events=6, bytes=140
        self.assertEqual(node["bytes"], 140)
        self.assertEqual(node["events"], 6)

    def test_source_is_tombstoned(self):
        all_paths = _all_paths(self.snap["tree"])
        self.assertNotIn("/p/tmp.x", all_paths)


class PartialRenameResolutionTests(unittest.TestCase):
    def setUp(self):
        self.agg = Aggregator()
        for ev in _load_jsonl(FIXTURES / "partial_rename.jsonl"):
            self.agg.ingest(ev)
        self.snap = self.agg.snapshot()

    def test_known_old_or_new_path_retains_rename_event(self):
        old_only = _find_node(self.snap["tree"], "/known/old-only.txt")
        new_only = _find_node(self.snap["tree"], "/known/new-only.txt")
        self.assertIsNotNone(old_only)
        self.assertIsNotNone(new_only)
        self.assertEqual(old_only["events"], 1)
        self.assertEqual(new_only["events"], 1)
        self.assertEqual(old_only["last_touched_ms"], 1778666400000.0)
        self.assertEqual(new_only["last_touched_ms"], 1778666401000.0)


class RecencyTests(unittest.TestCase):
    def test_decay_after_one_halflife_halves_contribution(self):
        agg = Aggregator()
        # Two events on different paths, separated by one half-life.
        agg.ingest({
            "schema_version": 1,
            "timestamp": "2026-05-13T10:00:00.000000Z",
            "operation": "modify", "path": "/a",
            "old_path": None, "new_path": None, "result": 1,
        })
        # 60 seconds later.
        agg.ingest({
            "schema_version": 1,
            "timestamp": "2026-05-13T10:01:00.000000Z",
            "operation": "modify", "path": "/b",
            "old_path": None, "new_path": None, "result": 1,
        })
        snap = agg.snapshot()
        a = _find_node(snap["tree"], "/a")
        b = _find_node(snap["tree"], "/b")
        # Decay reference: a was last touched 60s before latest_ts, so its
        # recent value should be ~0.5; b is at latest_ts, recent=1.0.
        self.assertAlmostEqual(a["recent"], 0.5, places=3)
        self.assertAlmostEqual(b["recent"], 1.0, places=3)


class JsParityTests(unittest.TestCase):
    """Opt-in: run the canonical JS aggregator under Node (if present) and
    assert it produces the same snapshot as the Python oracle for the
    fixtures we care about. Skipped when Node is not installed."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available; skipping JS parity check")
        cls.js_path = ROOT / "src" / "ai_observe" / "viewer" / "static" / "aggregator.js"

    def _js_eval(self, script: str):
        proc = subprocess.run(
            [self.node, "-e", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            self.fail(f"node driver failed: {proc.stderr}")
        return json.loads(proc.stdout)

    def _js_snapshot(self, fixture_path: Path, filter_patterns=None) -> dict:
        events = _load_jsonl(fixture_path)
        opts = {"filter_patterns": filter_patterns} if filter_patterns is not None else {}
        # Tiny driver script: load aggregator.js via require, ingest, print
        # the snapshot as JSON.
        driver = f"""
        const aggMod = require({json.dumps(str(self.js_path))});
        const events = {json.dumps(events)};
        const agg = aggMod.createAggregator({json.dumps(opts)});
        for (const e of events) agg.ingest(e);
        const snap = agg.snapshot({{}});
        process.stdout.write(JSON.stringify(snap));
        """
        return self._js_eval(driver)

    def _normalize(self, snap: dict) -> dict:
        # Strip metric/include_noise labels; they're informational.
        snap = dict(snap)
        snap.pop("metric", None)
        snap.pop("include_noise", None)
        return snap

    def _compare(self, py_snap: dict, js_snap: dict) -> None:
        # Both should have the same tree structure and numeric values.
        # Floats may differ at the last bit; round recency to 6 places.
        def canon(node):
            return {
                "path": node["path"],
                "name": node["name"],
                "isDir": node["isDir"],
                "bytes": node["bytes"],
                "events": node["events"],
                "recent": round(float(node["recent"]), 6),
                "last_touched_ms": node["last_touched_ms"],
                "sources": list(node.get("sources", [])),
                "confidences": list(node.get("confidences", [])),
                "children": [canon(c) for c in node["children"]],
            }

        self.assertEqual(canon(py_snap["tree"]), canon(js_snap["tree"]))
        self.assertEqual(py_snap["total_event_count"], js_snap["total_event_count"])
        self.assertEqual(py_snap["filtered_event_count"], js_snap["filtered_event_count"])
        self.assertEqual(py_snap.get("enabled_sources"), js_snap.get("enabled_sources"))


    def test_js_filter_helpers_cover_validation_and_globs(self):
        driver = f"""
        const aggMod = require({json.dumps(str(self.js_path))});
        const tmp = aggMod.createFilterMatcher(['/tmp/**']);
        const exact = aggMod.createFilterMatcher(['/work/build']);
        const subtree = aggMod.createFilterMatcher(['/work/build/**']);
        const literal = aggMod.createFilterMatcher(['/work/a+b.[txt]']);
        process.stdout.write(JSON.stringify({{
          tmpRoot: tmp.matches('/tmp'),
          tmpChild: tmp.matches('/tmp/a/b'),
          exactChild: exact.matches('/work/build/out.log'),
          subtreeChild: subtree.matches('/work/build/out.log'),
          literalGood: literal.matches('/work/a+b.[txt]'),
          literalBad: literal.matches('/work/ab.t'),
          emptyOk: aggMod.validateFilterPattern('   ').ok,
          relativeOk: aggMod.validateFilterPattern('tmp/**').ok
        }}));
        """
        out = self._js_eval(driver)
        self.assertTrue(out["tmpRoot"])
        self.assertTrue(out["tmpChild"])
        self.assertFalse(out["exactChild"])
        self.assertTrue(out["subtreeChild"])
        self.assertTrue(out["literalGood"])
        self.assertFalse(out["literalBad"])
        self.assertFalse(out["emptyOk"])
        self.assertFalse(out["relativeOk"])

    def test_parity_basic(self):
        agg = Aggregator()
        for ev in _load_jsonl(FIXTURES / "basic.jsonl"):
            agg.ingest(ev)
        py = self._normalize(agg.snapshot())
        js = self._normalize(self._js_snapshot(FIXTURES / "basic.jsonl"))
        self._compare(py, js)


    def test_parity_custom_filter_list(self):
        patterns = ["/work/c.txt", "/home/*/.codex/**"]
        agg = Aggregator(filter_patterns=patterns)
        for ev in _load_jsonl(FIXTURES / "basic.jsonl"):
            agg.ingest(ev)
        py = self._normalize(agg.snapshot(include_noise=False))
        js = self._normalize(self._js_snapshot(FIXTURES / "basic.jsonl", filter_patterns=patterns))
        self._compare(py, js)
        self.assertIsNone(_find_node(py["tree"], "/work/c.txt"))

    def test_parity_rename_chain(self):
        agg = Aggregator()
        for ev in _load_jsonl(FIXTURES / "rename_chain.jsonl"):
            agg.ingest(ev)
        py = self._normalize(agg.snapshot())
        js = self._normalize(self._js_snapshot(FIXTURES / "rename_chain.jsonl"))
        self._compare(py, js)

    def test_parity_partial_rename(self):
        agg = Aggregator()
        for ev in _load_jsonl(FIXTURES / "partial_rename.jsonl"):
            agg.ingest(ev)
        py = self._normalize(agg.snapshot())
        js = self._normalize(self._js_snapshot(FIXTURES / "partial_rename.jsonl"))
        self._compare(py, js)


if __name__ == "__main__":
    unittest.main()
