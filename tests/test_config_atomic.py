import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.config as config


class AtomicConfigWriteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        for p in (patch.object(config, "SETTINGS_PATH", self.dir / "settings.json"),
                  patch.object(config, "ENV_PATH", self.dir / ".env"),
                  patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def _leftovers(self):
        return sorted(p.name for p in self.dir.iterdir() if p.name.endswith(".tmp"))

    def test_settings_round_trip_leaves_no_temp_file(self):
        config.save_settings({"tts_provider": "coqui", "ad": "Türkçe ğüşiöç"})
        self.assertEqual(json.loads((self.dir / "settings.json").read_text(encoding="utf-8"))["ad"], "Türkçe ğüşiöç")
        self.assertEqual(config.load_settings()["tts_provider"], "coqui")
        self.assertEqual(self._leftovers(), [])

    def test_failed_replace_keeps_the_original_settings_intact(self):
        config.save_settings({"tts_provider": "edge"})
        with patch.object(config.os, "replace", side_effect=OSError("disk dolu")):
            with self.assertRaises(OSError):
                config.save_settings({"tts_provider": "coqui"})
        self.assertEqual(config.load_settings()["tts_provider"], "edge")
        self.assertEqual(self._leftovers(), [])

    def test_failed_env_write_does_not_lose_existing_keys(self):
        config.save_api_key("GEMINI_API_KEY", "gizli-gemini")
        with patch.object(config.os, "replace", side_effect=OSError("disk dolu")):
            with self.assertRaises(OSError):
                config.save_api_key("OPENAI_API_KEY", "gizli-openai")
        self.assertIn("GEMINI_API_KEY=gizli-gemini", (self.dir / ".env").read_text(encoding="utf-8"))
        self.assertEqual(self._leftovers(), [])

    def test_api_key_save_and_delete(self):
        config.save_api_key("GEMINI_API_KEY", "a")
        config.save_api_key("OPENAI_API_KEY", "b")
        config.save_api_key("GEMINI_API_KEY", "c")  # aynı anahtar güncellenir, tekrarlanmaz
        lines = (self.dir / ".env").read_text(encoding="utf-8").splitlines()
        self.assertEqual(sorted(lines), ["GEMINI_API_KEY=c", "OPENAI_API_KEY=b"])
        config.delete_api_key("OPENAI_API_KEY")
        self.assertEqual((self.dir / ".env").read_text(encoding="utf-8").splitlines(), ["GEMINI_API_KEY=c"])
        self.assertEqual(self._leftovers(), [])

    def test_transient_permission_error_is_retried(self):
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("dosya kullanımda")
            return real_replace(src, dst)

        with patch.object(config.os, "replace", side_effect=flaky), patch.object(config.time, "sleep"):
            config.save_settings({"tts_provider": "piper"})
        self.assertEqual(calls["n"], 3)
        self.assertEqual(config.load_settings()["tts_provider"], "piper")


if __name__ == "__main__":
    unittest.main()
