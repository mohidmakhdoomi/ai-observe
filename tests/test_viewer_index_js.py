import json
import shutil
import subprocess
import unittest
from pathlib import Path


class ViewerIndexRuntimeJsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available; skipping index.js runtime helper tests")
        cls.root = Path(__file__).resolve().parents[1]

    def _run_node(self, script):
        proc = subprocess.run(
            [self.node, "-e", script],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_stable_origin_storage_is_read_and_written_only_on_7878(self):
        script = r"""
        const h = require('./src/ai_observe/viewer/static/index.js');
        function storage(initial){
          return {value: initial, reads: 0, writes: 0,
            getItem(k){ this.reads += 1; return this.value; },
            setItem(k, v){ this.writes += 1; this.value = v; }};
        }
        const stable = {origin:'http://127.0.0.1:7878'};
        const fallback = {origin:'http://127.0.0.1:45123'};
        const stableStore = storage(JSON.stringify(['/work/**']));
        const fallbackStore = storage(JSON.stringify(['/secret/**']));
        const stableRead = h.readStoredFilterPatterns(stableStore, stable);
        const fallbackRead = h.readStoredFilterPatterns(fallbackStore, fallback);
        const stableWrite = h.writeStoredFilterPatterns(stableStore, stable, ['/tmp/**']);
        const fallbackWrite = h.writeStoredFilterPatterns(fallbackStore, fallback, ['/tmp/**']);
        process.stdout.write(JSON.stringify({
          stableRead, fallbackRead,
          stableReads: stableStore.reads,
          fallbackReads: fallbackStore.reads,
          stableWrite, fallbackWrite,
          stableWrites: stableStore.writes,
          fallbackWrites: fallbackStore.writes
        }));
        """
        out = self._run_node(script)
        self.assertEqual(out["stableRead"], ["/work/**"])
        self.assertIn("/home/*/.codex/**", out["fallbackRead"])
        self.assertEqual(out["stableReads"], 1)
        self.assertEqual(out["fallbackReads"], 0)
        self.assertTrue(out["stableWrite"])
        self.assertFalse(out["fallbackWrite"])
        self.assertEqual(out["stableWrites"], 1)
        self.assertEqual(out["fallbackWrites"], 0)

    def test_storage_malformed_invalid_and_throwing_values_fall_back_to_defaults(self):
        script = r"""
        const h = require('./src/ai_observe/viewer/static/index.js');
        const stable = {origin:'http://127.0.0.1:7878'};
        const malformed = {getItem(){ return '{not json'; }};
        const invalid = {getItem(){ return JSON.stringify(['relative/**']); }};
        const throwing = {getItem(){ throw new Error('blocked'); }};
        process.stdout.write(JSON.stringify({
          malformed: h.readStoredFilterPatterns(malformed, stable),
          invalid: h.readStoredFilterPatterns(invalid, stable),
          throwing: h.readStoredFilterPatterns(throwing, stable),
          emptyOk: h.normalizeFilterPatterns(['   ']).ok,
          relativeOk: h.normalizeFilterPatterns(['relative/**']).ok
        }));
        """
        out = self._run_node(script)
        for key in ("malformed", "invalid", "throwing"):
            self.assertIn("/tmp/**", out[key])
        self.assertFalse(out["emptyOk"])
        self.assertFalse(out["relativeOk"])

    def test_snapshot_from_events_replays_with_active_filters(self):
        events = [
            {"timestamp":"2026-05-13T10:00:00.000000Z", "operation":"modify", "path":"/secret/a.txt", "old_path":None, "new_path":None, "result":7},
            {"timestamp":"2026-05-13T10:00:01.000000Z", "operation":"modify", "path":"/work/a.txt", "old_path":None, "new_path":None, "result":11},
        ]
        script = f"""
        const h = require('./src/ai_observe/viewer/static/index.js');
        const events = {json.dumps(events)};
        const hidden = h.snapshotFromEvents(events, ['/secret/**'], {{include_filtered:false}});
        const shown = h.snapshotFromEvents(events, ['/secret/**'], {{include_filtered:true}});
        function paths(n){{ return [n.path].concat(...(n.children||[]).map(paths)); }}
        process.stdout.write(JSON.stringify({{
          hiddenCount: hidden.filtered_event_count,
          hiddenPaths: paths(hidden.tree),
          shownPaths: paths(shown.tree)
        }}));
        """
        out = self._run_node(script)
        self.assertEqual(out["hiddenCount"], 1)
        self.assertNotIn("/secret/a.txt", out["hiddenPaths"])
        self.assertIn("/work/a.txt", out["hiddenPaths"])
        self.assertIn("/secret/a.txt", out["shownPaths"])


if __name__ == "__main__":
    unittest.main()
