import unittest
from unittest.mock import patch

from app.tts import pronunciation


class OverridesLifecycleTests(unittest.TestCase):
    def test_no_file_means_no_overrides(self):
        with patch.object(pronunciation, "OVERRIDES_PATH", pronunciation.ROOT / "does-not-exist.json"):
            self.assertEqual(pronunciation.load_overrides(), {})

    def test_save_then_load_round_trips_and_lowercases_terms(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overrides.json"
            with patch.object(pronunciation, "OVERRIDES_PATH", path):
                pronunciation.save_overrides({"SWITCH": "sivic", "  ": "boş anahtar atlanmalı", "kernel": ""})
                loaded = pronunciation.load_overrides()
                self.assertEqual(loaded, {"switch": "sivic"})

    def test_effective_map_lets_override_win_over_built_in(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overrides.json"
            with patch.object(pronunciation, "OVERRIDES_PATH", path):
                self.assertEqual(pronunciation.effective_map()["switch"], "sviç")  # yerleşik varsayılan
                pronunciation.save_overrides({"switch": "sivic"})
                self.assertEqual(pronunciation.effective_map()["switch"], "sivic")  # kullanıcı ezdi

    def test_effective_map_allows_brand_new_terms_not_in_built_in_map(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overrides.json"
            with patch.object(pronunciation, "OVERRIDES_PATH", path):
                pronunciation.save_overrides({"kernel": "körnıl"})
                self.assertEqual(pronunciation.effective_map()["kernel"], "körnıl")


class NormalizeWithExplicitMappingTests(unittest.TestCase):
    def test_explicit_mapping_overrides_disk_state_without_touching_it(self):
        result = pronunciation.normalize_pronunciation("switch burada", mapping={"switch": "test-degeri"})
        self.assertIn("test-degeri", result)


if __name__ == "__main__":
    unittest.main()
