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

    def test_source_visibility_helpers_and_replay_options(self):
        events = [
            {"timestamp":"2026-05-13T10:00:00.000000Z", "operation":"modify", "path":"/work/direct.txt", "old_path":None, "new_path":None, "result":5},
            {"timestamp":"2026-05-13T10:00:01.000000Z", "schema_version":2, "source":"snapshot", "confidence":"inferred", "operation":"modify", "path":"/work/inferred.txt", "old_path":None, "new_path":None, "result":7},
        ]
        script = f"""
        const h = require('./src/ai_observe/viewer/static/index.js');
        const events = {json.dumps(events)};
        const visibility = h.normalizeSourceVisibility({{strace:false, snapshot:true}});
        const agg = h.createAggregatorFromEvents(events, [], {{sourceVisibility: visibility}});
        const snap = agg.snapshot({{}});
        function paths(n){{ return [n.path].concat(...(n.children||[]).map(paths)); }}
        process.stdout.write(JSON.stringify({{
          visibility,
          enabled: h.enabledSourcesFromVisibility(visibility),
          directShown: h.eventSourceIncluded(events[0], visibility),
          snapshotShown: h.eventSourceIncluded(events[1], visibility),
          paths: paths(snap.tree)
        }}));
        """
        out = self._run_node(script)
        self.assertEqual(out["visibility"], {"strace": False, "snapshot": True})
        self.assertEqual(out["enabled"], ["snapshot"])
        self.assertFalse(out["directShown"])
        self.assertTrue(out["snapshotShown"])
        self.assertNotIn("/work/direct.txt", out["paths"])
        self.assertIn("/work/inferred.txt", out["paths"])

    def test_session_banner_model_prefers_rebuilt_and_exposes_partial_switch(self):
        script = r"""
        const h = require('./src/ai_observe/viewer/static/index.js');
        const model = h.buildSessionBannerModel({
          default_artifact:'rebuilt',
          authoritative_artifact:'rebuilt',
          parser_status:'live_timeout_rebuilt',
          warnings_count:1,
          snapshot:{enabled:true,complete:false,diagnostic_count:2,emitted_event_count:4},
          artifacts:{
            jsonl:{exists:true,role:'partial_live'},
            rebuilt:{exists:true,role:'authoritative_complete'},
            partial:{exists:true,role:'partial_direct'},
            meta:{exists:true,role:'metadata'}
          }
        }, 'rebuilt');
        process.stdout.write(JSON.stringify(model));
        """
        out = self._run_node(script)
        self.assertTrue(out["visible"])
        self.assertIn("Rebuilt artifact is authoritative", out["text"])
        self.assertEqual([b["key"] for b in out["buttons"]], ["jsonl", "rebuilt", "partial"])
        self.assertEqual([b["active"] for b in out["buttons"]], [False, True, False])

    def test_append_and_append_batch_ingestion_helpers_preserve_order(self):
        script = r"""
        const h = require('./src/ai_observe/viewer/static/index.js');
        const eventBuffer = [];
        const ingested = [];
        const agg = {ingest(ev){ ingested.push(ev.path); }};
        const singleCount = h.ingestAppendData(JSON.stringify({path:'/one'}), eventBuffer, agg);
        const batchCount = h.ingestAppendBatchData(JSON.stringify([{path:'/two'}, {path:'/three'}]), eventBuffer, agg);
        const invalidBatchCount = h.ingestAppendBatchData(JSON.stringify({path:'/not-array'}), eventBuffer, agg);
        const malformedBatchCount = h.ingestAppendBatchData('{not json', eventBuffer, agg);
        const malformedSingleCount = h.ingestAppendData('{not json', eventBuffer, agg);
        process.stdout.write(JSON.stringify({
          counts: [singleCount, batchCount, invalidBatchCount, malformedBatchCount, malformedSingleCount],
          buffered: eventBuffer.map(ev => ev.path),
          ingested
        }));
        """
        out = self._run_node(script)
        self.assertEqual(out["counts"], [1, 2, 0, 0, 0])
        self.assertEqual(out["buffered"], ["/one", "/two", "/three"])
        self.assertEqual(out["ingested"], ["/one", "/two", "/three"])

    def test_filter_editor_helpers_validate_dedupe_and_reset(self):
        script = r"""
        const h = require('./src/ai_observe/viewer/static/index.js');
        const start = ['/tmp/**', '/work/build/**'];
        const added = h.addFilterPattern(start, '/tmp/**');
        const edited = h.updateFilterPatternAt(start, 1, ' /work/out/** ');
        const invalid = h.updateFilterPatternAt(start, 0, 'relative/**');
        const removed = h.removeFilterPatternAt(start, 0);
        const reset = h.resetFilterPatterns();
        process.stdout.write(JSON.stringify({
          summary: h.filterEditorSummary(start),
          added,
          edited,
          invalid,
          removed,
          reset
        }));
        """
        out = self._run_node(script)
        self.assertEqual(out["summary"], "Filters (2)")
        self.assertTrue(out["added"]["ok"])
        self.assertEqual(out["added"]["patterns"], ["/tmp/**", "/work/build/**"])
        self.assertTrue(out["edited"]["ok"])
        self.assertEqual(out["edited"]["patterns"], ["/tmp/**", "/work/out/**"])
        self.assertFalse(out["invalid"]["ok"])
        self.assertEqual(out["removed"]["patterns"], ["/work/build/**"])
        self.assertIn("/home/*/.codex/**", out["reset"]["patterns"])

    def test_item_action_helpers_build_preview_patterns_and_prune_selection(self):
        script = r"""
        const h = require('./src/ai_observe/viewer/static/index.js');
        const throwingTree = {get path(){ throw new Error('walked empty selection tree'); }};
        const tree = {path:'/', children:[
          {path:'/work', isDir:true, children:[
            {path:'/work/a.log', isDir:false, children:[]},
            {path:'/work/b.log', isDir:false, children:[]}
          ]},
          {path:'/tmp', isDir:true, children:[]}
        ]};
        process.stdout.write(JSON.stringify({
          dir: h.filterPatternProposals({path:'/work', isDir:true}),
          file: h.filterPatternProposals({path:'/work/a.log', isDir:false}),
          exact: h.exactPatternsForSelection(['/work/a.log','/work/a.log','/work/b.log']),
          emptyPruned: h.pruneSelectedPaths([], throwingTree),
          pruned: h.pruneSelectedPaths(['/work/a.log','/missing'], tree),
          toggled: h.togglePathSelection(['/work/a.log'], '/work/a.log'),
          range: h.selectVisibleRange(['/work/a.log'], '/work/a.log', '/tmp', ['/work/a.log','/work/b.log','/tmp']),
          offState: h.updateMultiSelectionState({selectedPaths:['/work/a.log'], selectedPath:'/work/a.log', selectionAnchorPath:'/work/a.log'}, '/work/a.log', {}),
          onState: h.updateMultiSelectionState({selectedPaths:[], selectedPath:null, selectionAnchorPath:null}, '/work/a.log', {}),
          rangeState: h.updateMultiSelectionState({selectedPaths:['/work/a.log'], selectedPath:'/work/a.log', selectionAnchorPath:'/work/a.log'}, '/tmp', {shiftKey:true, visiblePaths:['/work/a.log','/work/b.log','/tmp']})
        }));
        """
        out = self._run_node(script)
        self.assertEqual(
            [p["pattern"] for p in out["dir"]],
            ["/work", "/work/**"],
        )
        self.assertEqual([p["pattern"] for p in out["file"]], ["/work/a.log"])
        self.assertEqual(out["exact"], ["/work/a.log", "/work/b.log"])
        self.assertEqual(out["emptyPruned"], [])
        self.assertEqual(out["pruned"], ["/work/a.log"])
        self.assertEqual(out["toggled"], [])
        self.assertEqual(out["range"], ["/work/a.log", "/work/b.log", "/tmp"])
        self.assertEqual(out["offState"]["selectedPaths"], [])
        self.assertIsNone(out["offState"]["selectedPath"])
        self.assertEqual(out["onState"]["selectedPath"], "/work/a.log")
        self.assertEqual(out["rangeState"]["selectedPaths"], ["/work/a.log", "/work/b.log", "/tmp"])
        self.assertEqual(out["rangeState"]["selectedPath"], "/tmp")

    def test_runtime_prune_selections_has_empty_selection_fast_path(self):
        # This used to assert an exact source snippet. Keep the intent but
        # avoid coupling to formatting/minification details.
        script = r"""
        const h = require('./src/ai_observe/viewer/static/index.js');
        const tree = {path:'/', children:[{path:'/work', children:[]}]};
        process.stdout.write(JSON.stringify(h.pruneSelectedPaths([], tree)));
        """
        self.assertEqual(self._run_node(script), [])


if __name__ == "__main__":
    unittest.main()
