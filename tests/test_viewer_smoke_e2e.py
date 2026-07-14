from html.parser import HTMLParser
from pathlib import Path
import json
import sys
import tempfile
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))  # tests/_util.py is here

from ai_observe.viewer.server import ViewerServer  # noqa: E402
from _util import poll_until  # noqa: E402


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.styles = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "script" and d.get("src"):
            self.scripts.append(d["src"])
        if tag == "link" and d.get("rel") == "stylesheet" and d.get("href"):
            self.styles.append(d["href"])
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


class ViewerSmokeE2ETests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "events.jsonl"
        self.path.write_text(json.dumps({
            "schema_version": 1,
            "timestamp": "2026-05-13T10:00:00.000000Z",
            "operation": "modify",
            "path": "/work/a.py",
            "old_path": None,
            "new_path": None,
            "result": 5,
        }) + "\n")
        self.srv = ViewerServer(self.path, port=0, poll_interval=0.05)
        self.srv.start()
        self.addCleanup(self.srv.stop)
        # Wait until the tailer has picked up the pre-written event.
        self.assertTrue(poll_until(lambda: self.srv.broadcaster_for(None).snapshot_len() >= 1))

    def get(self, rel):
        with urllib.request.urlopen(self.srv.url + rel.lstrip("/"), timeout=3.0) as resp:
            return resp.read().decode("utf-8")

    def test_page_references_expected_assets_and_lints(self):
        html = self.get("/")
        parser = AssetParser()
        parser.feed(html)
        self.assertEqual(parser.title, "ai_observe viewer")
        self.assertEqual(parser.styles, ["/static/style.css"])
        self.assertEqual(parser.scripts, [
            "/static/aggregator.js",
            "/static/treemap.js",
            "/static/table.js",
            "/static/index.js",
        ])
        all_text = html
        self.assertIn("Filters (0)", html)
        self.assertIn("Add selected to Filters", html)
        self.assertIn("Show filtered", html)
        self.assertIn("Sources", html)
        self.assertIn("Strace", html)
        self.assertIn("Snapshot", html)
        self.assertIn("Reset to defaults", html)
        self.assertNotIn("Show noise", html)
        for asset in parser.styles + parser.scripts:
            body = self.get(asset)
            all_text += "\n" + body
            self.assertGreater(len(body), 20)
        for forbidden in ("innerHTML", "document.write", "eval(", "raw_syscall", "document.title"):
            self.assertNotIn(forbidden, all_text)
        static_dir = ROOT / "src" / "ai_observe" / "viewer" / "static"
        total = sum(p.stat().st_size for p in static_dir.iterdir() if p.is_file())
        self.assertLess(total, 50_000)

    def test_session_endpoint_exposes_sanitized_default_artifact_state(self):
        payload = json.loads(self.get('/session'))
        self.assertEqual(payload['default_artifact'], 'jsonl')
        self.assertEqual(payload['requested_artifact'], 'jsonl')
        self.assertIsNone(payload['authoritative_artifact'])
        self.assertEqual(sorted(payload['artifacts'].keys()), ['jsonl', 'meta', 'partial', 'rebuilt'])
        self.assertTrue(payload['artifacts']['jsonl']['exists'])
        self.assertFalse(payload['artifacts']['partial']['exists'])
        self.assertNotIn('warnings', payload)

    def test_viewer_docs_describe_filters_and_port_behavior(self):
        docs = (ROOT / "docs" / "viewer.md").read_text()
        for expected in (
            "tries `127.0.0.1:7878`",
            "falls back to an OS-chosen ephemeral loopback port",
            "`Show filtered` reveals matching non-tombstoned paths",
            "http://127.0.0.1:7878",
            "Right-click a table row or treemap tile",
            "Add N selected to Filters",
            "memory linear in event count",
        ):
            self.assertIn(expected, docs)
        for old_wording in ("Show noise", "Default noise filter"):
            self.assertNotIn(old_wording, docs)


if __name__ == "__main__":
    unittest.main()
