from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_observe import codex_observe


class ObserveEnvAliasTests(unittest.TestCase):
    def test_env_value_prefers_ai_observe_over_legacy_alias(self):
        env = {
            "CODEV_OBSERVE_SESSION_ID": "legacy",
            "AI_OBSERVE_SESSION_ID": "preferred",
        }
        self.assertEqual(codex_observe.env_value(env, "SESSION_ID"), "preferred")
        self.assertIsNone(codex_observe.env_value({}, "SESSION_ID"))
        self.assertEqual(codex_observe.env_value({}, "SESSION_ID", "fallback"), "fallback")

    def test_env_flag_prefers_ai_observe_over_legacy_alias(self):
        self.assertTrue(codex_observe.env_flag({"AI_OBSERVE_DISABLE": "1", "CODEV_OBSERVE_DISABLE": "0"}, "DISABLE"))
        self.assertFalse(codex_observe.env_flag({"AI_OBSERVE_DISABLE": "0", "CODEV_OBSERVE_DISABLE": "1"}, "DISABLE"))
        self.assertTrue(codex_observe.env_flag({"CODEV_OBSERVE_DISABLE": "1"}, "DISABLE"))

    def test_prepare_logs_prefers_ai_dir_and_session_over_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = os.getcwd()
            try:
                os.chdir(root)
                env = {
                    "CODEV_OBSERVE_DIR": str(root / "legacy-obs"),
                    "AI_OBSERVE_DIR": str(root / "preferred-obs"),
                    "CODEV_OBSERVE_SESSION_ID": "legacy-session",
                    "AI_OBSERVE_SESSION_ID": "preferred-session",
                }
                logs = codex_observe.prepare_logs(env)
            finally:
                os.chdir(old)
            self.assertEqual(logs.observe_dir, (root / "preferred-obs").resolve())
            self.assertEqual(logs.session_id, "preferred-session")
            self.assertTrue(logs.jsonl_path.exists())
            self.assertFalse((root / "legacy-obs").exists())

    def test_live_knobs_prefer_ai_observe_over_legacy(self):
        self.assertTrue(codex_observe._live_enabled({
            "AI_OBSERVE_LIVE_PARSE": "1",
            "CODEV_OBSERVE_LIVE_PARSE": "0",
        }))
        self.assertFalse(codex_observe._live_enabled({
            "AI_OBSERVE_LIVE_PARSE": "0",
            "CODEV_OBSERVE_LIVE_PARSE": "1",
        }))
        self.assertAlmostEqual(codex_observe._live_poll_seconds({
            "AI_OBSERVE_LIVE_POLL_MS": "50",
            "CODEV_OBSERVE_LIVE_POLL_MS": "2000",
        }), 0.050)
        self.assertAlmostEqual(codex_observe._live_join_timeout({
            "AI_OBSERVE_LIVE_JOIN_TIMEOUT": "0.5",
            "CODEV_OBSERVE_LIVE_JOIN_TIMEOUT": "600",
        }), 0.5)

    def test_additional_shared_aliases_prefer_ai_observe_over_legacy(self):
        env = {
            "AI_OBSERVE_STRICT_PARSE": "1",
            "CODEV_OBSERVE_STRICT_PARSE": "0",
            "AI_OBSERVE_INCLUDE_LOG_WRITES": "1",
            "CODEV_OBSERVE_INCLUDE_LOG_WRITES": "0",
            "AI_OBSERVE_SIGNAL_GRACE": "0.25",
            "CODEV_OBSERVE_SIGNAL_GRACE": "9",
        }
        self.assertTrue(codex_observe.env_flag(env, "STRICT_PARSE"))
        self.assertTrue(codex_observe.env_flag(env, "INCLUDE_LOG_WRITES"))
        self.assertEqual(codex_observe.env_value(env, "SIGNAL_GRACE"), "0.25")

    def test_symlink_dir_allowance_prefers_ai_observe_over_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)

            allowed = codex_observe.prepare_logs({
                "AI_OBSERVE_DIR": str(link),
                "AI_OBSERVE_ALLOW_SYMLINK_DIR": "1",
                "CODEV_OBSERVE_ALLOW_SYMLINK_DIR": "0",
                "AI_OBSERVE_SESSION_ID": "allowed",
            })
            self.assertEqual(allowed.observe_dir, target.resolve())

            with self.assertRaises(codex_observe.ObserveError):
                codex_observe.prepare_logs({
                    "AI_OBSERVE_DIR": str(link),
                    "AI_OBSERVE_ALLOW_SYMLINK_DIR": "0",
                    "CODEV_OBSERVE_ALLOW_SYMLINK_DIR": "1",
                    "AI_OBSERVE_SESSION_ID": "blocked",
                })

    def test_resolve_real_codex_prefers_ai_real_over_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shim = root / "codex"
            legacy = root / "legacy-codex"
            preferred = root / "preferred-codex"
            for path in (shim, legacy, preferred):
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                path.chmod(0o755)
            env = {
                "AI_OBSERVE_REAL_CODEX": str(preferred),
                "CODEV_OBSERVE_REAL_CODEX": str(legacy),
            }
            self.assertEqual(codex_observe.resolve_real_codex(env, shim), preferred.resolve())


if __name__ == "__main__":
    unittest.main()
