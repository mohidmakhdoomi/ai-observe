import unittest


def breadcrumb_segments(path):
    segs = [("/", "/")]
    parts = [p for p in (path or "/").split("/") if p]
    cur = ""
    for part in parts:
        cur += "/" + part
        segs.append((part, cur))
    return segs


def parent_path(path):
    if not path or path == "/":
        return None
    parts = [p for p in path.split("/") if p]
    parts.pop()
    return "/" + "/".join(parts) if parts else "/"


class ViewerBreadcrumbTests(unittest.TestCase):
    def test_breadcrumb_segments(self):
        self.assertEqual(
            breadcrumb_segments("/a/b/c"),
            [("/", "/"), ("a", "/a"), ("b", "/a/b"), ("c", "/a/b/c")],
        )

    def test_up_target(self):
        self.assertEqual(parent_path("/a/b/c"), "/a/b")
        self.assertEqual(parent_path("/a"), "/")
        self.assertIsNone(parent_path("/"))


if __name__ == "__main__":
    unittest.main()

class ViewerIndexJsHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not available; skipping index.js helper parity")

    def test_real_js_breadcrumb_and_live_badge_helpers(self):
        import json, subprocess
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        driver = """
        const h=require('./src/ai_observe/viewer/static/index.js');
        process.stdout.write(JSON.stringify({
          crumbs:h.breadcrumbSegments('/a/b/c'),
          up:h.parentPath('/a/b/c'),
          rootUp:h.parentPath('/'),
          idle:h.liveBadgeState(null,'idle',1000),
          live:h.liveBadgeState(500,'idle',1000),
          shut:h.liveBadgeState(500,'shutdown',1000),
          inScope:h.isInScope('/a','/a/b')
        }));
        """
        proc = subprocess.run([self.node, "-e", driver], cwd=root, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["crumbs"], [{"label":"/","path":"/"},{"label":"a","path":"/a"},{"label":"b","path":"/a/b"},{"label":"c","path":"/a/b/c"}])
        self.assertEqual(data["up"], "/a/b")
        self.assertIsNone(data["rootUp"])
        self.assertEqual(data["idle"], {"text":"idle", "className":"badge gray"})
        self.assertEqual(data["live"], {"text":"live", "className":"badge green"})
        self.assertEqual(data["shut"], {"text":"shutdown", "className":"badge red"})
        self.assertTrue(data["inScope"])
