import re
import unittest
from pathlib import Path

from tools import pack_for_vps

ROOT = Path(__file__).resolve().parent.parent


def _packed() -> set[str]:
    return {p.relative_to(ROOT).as_posix() for p in pack_for_vps.collect()}


class PackForVpsTests(unittest.TestCase):
    def test_never_includes_secrets_user_data_or_environments(self):
        forbidden_roots = {".env", "settings.json", "projects", "models", "study_data", "docker-data", "_cache",
                           "venv", "chatterbox_venv", ".git", "colab", "logo.png", "gps.mp4"}
        for path in _packed():
            parts = path.split("/")
            self.assertNotIn(parts[0], forbidden_roots, path)
            self.assertFalse({"node_modules", "dist", "__pycache__"} & set(parts), path)
            self.assertFalse(path.endswith((".pyc", ".log", ".mp4", ".mp3")), path)

    def test_contains_everything_the_dockerfile_copies(self):
        packed = _packed()
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        sources: list[str] = []
        for line in dockerfile.splitlines():
            match = re.match(r"^COPY\s+(?!--from)(.+)\s+\S+$", line.strip())
            if match:
                sources.extend(match.group(1).split())
        self.assertTrue(sources)
        for source in sources:
            covered = source in packed or any(p.startswith(source.rstrip("/") + "/") for p in packed)
            self.assertTrue(covered, f"Dockerfile COPY kaynağı pakette yok: {source}")

    def test_contains_the_runtime_code_and_web_sources(self):
        packed = _packed()
        for required in ("Dockerfile", "compose.yaml", "app/pipeline.py", "app/tts/remote.py",
                         "studio_web/api.py", "webui/src/App.jsx", "webui/public/favicon.ico",
                         "prompts", ".env.example"):
            self.assertTrue(required in packed or any(p.startswith(required + "/") for p in packed), required)


if __name__ == "__main__":
    unittest.main()
