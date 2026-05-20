import unittest
from pathlib import Path


def metric_value(n, metric="bytes"):
    v = n.get(metric, 0)
    return v if isinstance(v, (int, float)) and v > 0 else 0


def worst(row, side):
    if not row:
        return float("inf")
    total = sum(r["area"] for r in row)
    mn = min(r["area"] for r in row)
    mx = max(r["area"] for r in row)
    if total <= 0 or side <= 0:
        return float("inf")
    s2 = side * side
    return max((s2 * mx) / (total * total), (total * total) / (s2 * mn))


def layout_row(row, rect, out):
    total = sum(r["area"] for r in row)
    if rect["w"] >= rect["h"]:
        h = total / rect["w"]
        x = rect["x"]
        for r in row:
            w = r["area"] / h
            out.append({"node": r["node"], "x": x, "y": rect["y"], "w": w, "h": h})
            x += w
        rect["y"] += h
        rect["h"] -= h
    else:
        w = total / rect["h"]
        y = rect["y"]
        for r in row:
            h = r["area"] / w
            out.append({"node": r["node"], "x": rect["x"], "y": y, "w": w, "h": h})
            y += h
        rect["x"] += w
        rect["w"] -= w


def squarify(items, rect):
    total = sum(i["value"] for i in items)
    if total <= 0:
        return []
    area = rect["w"] * rect["h"]
    pending = [{"node": i["node"], "area": i["value"] * area / total} for i in items]
    pending.sort(key=lambda r: (-r["area"], r["node"]["path"]))
    out, row = [], []
    r = dict(rect)
    while pending:
        item = pending[0]
        side = min(r["w"], r["h"])
        if not row or worst(row + [item], side) <= worst(row, side):
            row.append(item)
            pending.pop(0)
        else:
            layout_row(row, r, out)
            row = []
    layout_row(row, r, out)
    return out


def layout_treemap(node, width, height, metric="bytes"):
    rects = []

    def rec(n, x, y, w, h, depth):
        inset = 0 if depth == 0 else 3
        ix, iy, iw, ih = x + inset, y + inset, max(0, w - 2 * inset), max(0, h - 2 * inset)
        items = [{"node": c, "value": metric_value(c, metric)} for c in n.get("children", [])]
        items = [i for i in items if i["value"] > 0]
        for cell in squarify(items, {"x": ix, "y": iy, "w": iw, "h": ih}):
            c = cell["node"]
            rects.append({"path": c["path"], "x": round(cell["x"], 3), "y": round(cell["y"], 3), "w": round(cell["w"], 3), "h": round(cell["h"], 3), "isDir": c["isDir"]})
            if c.get("isDir"):
                rec(c, cell["x"], cell["y"], cell["w"], cell["h"], depth + 1)
    rec(node, 0, 0, width, height, 0)
    return rects


def find_node(n, path):
    if n["path"] == path:
        return n
    for c in n.get("children", []):
        f = find_node(c, path)
        if f:
            return f
    return None


class ViewerTreemapTests(unittest.TestCase):
    def test_one_rectangle_fills_container(self):
        tree = {"path": "/", "children": [{"path": "/a", "name": "a", "isDir": False, "bytes": 10, "children": []}]}
        self.assertEqual(layout_treemap(tree, 100, 50), [{"path": "/a", "x": 0, "y": 0, "w": 100, "h": 50, "isDir": False}])

    def test_two_equal_rectangles_are_deterministic(self):
        tree = {"path": "/", "children": [
            {"path": "/a", "name": "a", "isDir": False, "bytes": 1, "children": []},
            {"path": "/b", "name": "b", "isDir": False, "bytes": 1, "children": []},
        ]}
        self.assertEqual(layout_treemap(tree, 100, 50), [
            {"path": "/a", "x": 0, "y": 0, "w": 100, "h": 25, "isDir": False},
            {"path": "/b", "x": 0, "y": 25, "w": 100, "h": 25, "isDir": False},
        ])

    def test_drilled_root_only_lays_out_subtree(self):
        tree = {"path": "/", "children": [
            {"path": "/p", "name": "p", "isDir": True, "bytes": 5, "children": [{"path": "/p/x", "name": "x", "isDir": False, "bytes": 5, "children": []}]},
            {"path": "/q", "name": "q", "isDir": True, "bytes": 7, "children": [{"path": "/q/y", "name": "y", "isDir": False, "bytes": 7, "children": []}]},
        ]}
        rects = layout_treemap(find_node(tree, "/p"), 100, 100)
        self.assertEqual([r["path"] for r in rects], ["/p/x"])


if __name__ == "__main__":
    unittest.main()

class ViewerTreemapJsParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available; skipping treemap JS parity")

    def test_real_js_layout_matches_oracle(self):
        import json, subprocess
        tree = {"path": "/", "children": [
            {"path": "/a", "name": "a", "isDir": False, "bytes": 1, "children": []},
            {"path": "/b", "name": "b", "isDir": False, "bytes": 1, "children": []},
        ]}
        driver = """
        const t=require('./src/ai_observe/viewer/static/treemap.js');
        const tree=%s;
        const out=t.layoutTreemap(tree,100,50,'bytes').map(r=>({path:r.path,x:Math.round(r.x*1000)/1000,y:Math.round(r.y*1000)/1000,w:Math.round(r.w*1000)/1000,h:Math.round(r.h*1000)/1000,isDir:r.isDir}));
        process.stdout.write(JSON.stringify(out));
        """ % json.dumps(tree)
        proc = subprocess.run([self.node, "-e", driver], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), layout_treemap(tree, 100, 50))


    def test_real_js_tooltip_formats_last_touched_human_readably(self):
        import json, subprocess
        driver = """
        const t=require('./src/ai_observe/viewer/static/treemap.js');
        const out={
          formatted:t.formatLastTouched(Date.UTC(2026,4,13,10,0,1)),
          never:t.formatLastTouched(0),
          tooltip:t.tooltipFor({path:'/a.txt',bytes:7,events:2,last_touched_ms:Date.UTC(2026,4,13,10,0,1),sources:['strace','snapshot'],confidences:['direct','inferred']})
        };
        process.stdout.write(JSON.stringify(out));
        """
        proc = subprocess.run([self.node, "-e", driver], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["formatted"], "2026-05-13 10:00:01 UTC")
        self.assertEqual(out["never"], "never")
        self.assertIn("Last touched: 2026-05-13 10:00:01 UTC", out["tooltip"])
        self.assertIn("Sources: strace, snapshot", out["tooltip"])
        self.assertIn("Confidence: direct, inferred", out["tooltip"])
        self.assertNotIn("Last touched: 1778666401000", out["tooltip"])

    def test_real_js_context_metadata_preserves_path_and_directory_flag(self):
        import json, subprocess
        driver = """
        const t=require('./src/ai_observe/viewer/static/treemap.js');
        const out=t.actionMetadataForRect({path:'/work/build',name:'build',isDir:true});
        process.stdout.write(JSON.stringify(out));
        """
        proc = subprocess.run([self.node, "-e", driver], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"path": "/work/build", "name": "build", "isDir": True})
