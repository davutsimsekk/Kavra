import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class PortableConfigTests(unittest.TestCase):
    def test_data_root_can_be_moved_outside_source_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "kavra-data"
            data_dir.mkdir()
            (data_dir / ".env").write_text("GEMINI_API_KEY=saved-in-data\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["KAVRA_DATA_DIR"] = str(data_dir)
            environment["GEMINI_API_KEY"] = ""
            for name in (
                "KAVRA_CACHE_DIR",
                "KAVRA_MODELS_DIR",
                "KAVRA_PROJECTS_DIR",
                "KAVRA_STUDY_DATA_DIR",
                "KAVRA_ENV_PATH",
                "KAVRA_SETTINGS_PATH",
            ):
                environment.pop(name, None)
            command = (
                "import json; from app import config; "
                "print(json.dumps({"
                "'cache': str(config.CACHE_DIR), "
                "'models': str(config.MODELS_DIR), "
                "'projects': str(config.PROJECTS_DIR), "
                "'study': str(config.STUDY_DATA_DIR), "
                "'settings': str(config.SETTINGS_PATH), "
                "'gemini': config.get_api_key('GEMINI_API_KEY')}))"
            )
            result = subprocess.run(
                [sys.executable, "-c", command],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            paths = json.loads(result.stdout.strip())
            self.assertEqual(Path(paths["cache"]), data_dir / "_cache")
            self.assertEqual(Path(paths["models"]), data_dir / "models")
            self.assertEqual(Path(paths["projects"]), data_dir / "projects")
            self.assertEqual(Path(paths["study"]), data_dir / "study_data")
            self.assertEqual(Path(paths["settings"]), data_dir / "settings.json")
            self.assertEqual(paths["gemini"], "saved-in-data")
            for name in ("_cache", "models", "projects", "study_data"):
                self.assertTrue((data_dir / name).is_dir())


if __name__ == "__main__":
    unittest.main()
