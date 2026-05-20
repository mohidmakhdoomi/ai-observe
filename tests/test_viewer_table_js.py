import json
import shutil
import subprocess
import unittest
from pathlib import Path


class ViewerTableJsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available; skipping table.js tests")
        cls.root = Path(__file__).resolve().parents[1]

    def test_real_js_sibling_local_sort_and_relative_time(self):
        tree = {"children": [
            {"path":"/b", "name":"b", "isDir":False, "bytes":5, "events":10, "recent":1, "last_touched_ms":1000, "children":[]},
            {"path":"/a", "name":"a", "isDir":True, "bytes":9, "events":1, "recent":2, "last_touched_ms":4000, "children":[
                {"path":"/a/z", "name":"z", "isDir":False, "bytes":1, "events":1, "recent":1, "last_touched_ms":3000, "children":[]},
                {"path":"/a/y", "name":"y", "isDir":False, "bytes":3, "events":2, "recent":1, "last_touched_ms":2000, "children":[]},
            ]},
        ]}
        driver = """
        const tbl=require('./src/ai_observe/viewer/static/table.js');
        const tree=%s;
        const top=tbl.sortedChildren(tree,{column:'bytes',dir:'desc'}).map(n=>n.path);
        const kids=tbl.sortedChildren(tree.children[1],{column:'bytes',dir:'desc'}).map(n=>n.path);
        process.stdout.write(JSON.stringify({top:top,kids:kids,rel:tbl.relativeTime(1000,61000)}));
        """ % json.dumps(tree)
        proc = subprocess.run([self.node, "-e", driver], cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["top"], ["/a", "/b"])
        self.assertEqual(data["kids"], ["/a/y", "/a/z"])
        self.assertEqual(data["rel"], "1m ago")

    def test_real_js_flattens_visible_rows_for_shift_selection(self):
        tree = {"children": [
            {"path":"/b", "name":"b", "isDir":False, "bytes":5, "events":10, "recent":1, "last_touched_ms":1000, "children":[]},
            {"path":"/a", "name":"a", "isDir":True, "bytes":9, "events":1, "recent":2, "last_touched_ms":4000, "children":[
                {"path":"/a/z", "name":"z", "isDir":False, "bytes":1, "events":1, "recent":1, "last_touched_ms":3000, "children":[]},
                {"path":"/a/y", "name":"y", "isDir":False, "bytes":3, "events":2, "recent":1, "last_touched_ms":2000, "children":[]},
            ]},
        ]}
        driver = """
        const tbl=require('./src/ai_observe/viewer/static/table.js');
        const tree=%s;
        const state={sort:{column:'path',dir:'asc'}, expanded:new Set(['/a'])};
        const rows=tbl.flattenVisibleRows(tree,state).map(r=>({path:r.path,depth:r.depth,isDir:r.isDir}));
        process.stdout.write(JSON.stringify(rows));
        """ % json.dumps(tree)
        proc = subprocess.run([self.node, "-e", driver], cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [
            {"path": "/a", "depth": 0, "isDir": True},
            {"path": "/a/y", "depth": 1, "isDir": False},
            {"path": "/a/z", "depth": 1, "isDir": False},
            {"path": "/b", "depth": 0, "isDir": False},
        ])


    def test_real_js_provenance_helpers_build_badges_and_titles(self):
        driver = """
        const tbl=require('./src/ai_observe/viewer/static/table.js');
        const node={path:'/work/mixed.txt',sources:['strace','snapshot'],confidences:['direct','inferred']};
        process.stdout.write(JSON.stringify({
          summary: tbl.provenanceSummary(node),
          badges: tbl.badgeDataForNode(node)
        }));
        """
        proc = subprocess.run([self.node, "-e", driver], cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn('Sources: strace, snapshot', out['summary']['text'])
        self.assertIn('Confidence: direct, inferred', out['summary']['text'])
        self.assertEqual([badge['label'] for badge in out['badges']], ['strace', 'snapshot', 'direct', 'inferred'])


if __name__ == "__main__":
    unittest.main()
