import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import build_colab
from tts_server.colab_tunnel import find_url

ROOT = Path(__file__).resolve().parent.parent


class TunnelUrlTests(unittest.TestCase):
    def test_finds_the_quick_tunnel_address(self):
        line = "2026-09-22T10:00:00Z INF |  https://calm-river-lake-1234.trycloudflare.com                    |"
        self.assertEqual(find_url(line), "https://calm-river-lake-1234.trycloudflare.com")

    def test_ignores_the_cloudflare_api_host_in_error_lines(self):
        line = 'ERR failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": EOF'
        self.assertIsNone(find_url(line))
        self.assertIsNone(find_url(""))
        self.assertIsNone(find_url(None))


class BundleTests(unittest.TestCase):
    def test_bundle_is_self_contained(self):
        """Paket tek başına (deponun geri kalanı olmadan) içe aktarılabilmeli."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_colab.build_bundle(Path(tmp) / "bundle.zip")
            target = Path(tmp) / "kavra"
            zipfile.ZipFile(bundle).extractall(target)
            env = {k: v for k, v in os.environ.items() if not k.startswith("KAVRA_")}
            env.update(PYTHONPATH=str(target), PYTHONDONTWRITEBYTECODE="1")
            code = ("import app, tts_server.server, app.tts.coqui_provider, app.tts.piper_provider; "
                    "print(app.__file__)")
            result = subprocess.run([sys.executable, "-c", code], cwd=target, env=env, capture_output=True,
                                    text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(Path(result.stdout.strip()).resolve().is_relative_to(target.resolve()))

    def test_bundle_excludes_user_data_and_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            names = zipfile.ZipFile(build_colab.build_bundle(Path(tmp) / "b.zip")).namelist()
        self.assertFalse([n for n in names if n.startswith(("projects/", "models/", "study_data/", "_cache/"))
                          or n.endswith((".env", "settings.json"))])


class NotebookTests(unittest.TestCase):
    def test_every_code_cell_is_plain_python(self):
        notebook = build_colab.notebook()
        code = ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]
        self.assertEqual(len(code), 5)
        for source in code:
            self.assertFalse([l for l in source.splitlines() if l.lstrip().startswith(("!", "%"))])
            ast.parse(source)

    def test_committed_notebook_matches_the_generator(self):
        path = ROOT / "colab" / "Kavra_TTS_Sunucusu.ipynb"
        if path.exists():
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), build_colab.notebook(),
                             "python tools/build_colab.py çalıştır")

    def test_studio_notebook_cells_are_plain_python(self):
        code = ["".join(c["source"]) for c in build_colab.studio_notebook()["cells"] if c["cell_type"] == "code"]
        self.assertEqual(len(code), 4)
        for source in code:
            self.assertFalse([l for l in source.splitlines() if l.lstrip().startswith(("!", "%"))])
            ast.parse(source)
        self.assertIn(build_colab.REPO_URL, code[0])

    def test_committed_studio_notebook_matches_the_generator(self):
        path = ROOT / "colab" / "Kavra_Studyo_Colab.ipynb"
        self.assertTrue(path.exists(), "python tools/build_colab.py çalıştır")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), build_colab.studio_notebook(),
                         "python tools/build_colab.py çalıştır")

    def test_kaggle_notebook_cells_are_plain_python_and_avoid_colab_apis(self):
        code = ["".join(c["source"]) for c in build_colab.kaggle_notebook()["cells"] if c["cell_type"] == "code"]
        self.assertEqual(len(code), 4)
        for source in code:
            self.assertFalse([l for l in source.splitlines() if l.lstrip().startswith(("!", "%"))])
            self.assertNotIn("google.colab", source)
            self.assertNotIn("/content", source)
            ast.parse(source)

    def test_committed_kaggle_notebook_matches_the_generator(self):
        path = ROOT / "colab" / "Kavra_Studyo_Kaggle.ipynb"
        self.assertTrue(path.exists(), "python tools/build_colab.py çalıştır")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), build_colab.kaggle_notebook(),
                         "python tools/build_colab.py çalıştır")


class ColabStudioTests(unittest.TestCase):
    def test_server_env_exposes_only_the_tunnel_host_behind_the_token(self):
        from tools import colab_studio

        env = colab_studio.server_env("calm-river.trycloudflare.com", "x" * 24, Path("/content/kavra_data"),
                                      root=Path("/content/Kavra"), base={"HF_HOME": "/root/.cache", "PATH": "/bin"})
        self.assertEqual(env["KAVRA_ALLOWED_HOSTS"], "calm-river.trycloudflare.com")
        self.assertEqual(env["KAVRA_ACCESS_TOKEN"], "x" * 24)
        self.assertEqual(env["KAVRA_MODELS_DIR"], str(Path("/content/Kavra/models")))
        self.assertNotIn("HF_HOME", env)  # Kavra kendi önbellek düzenini kursun
        self.assertEqual(env["PATH"], "/bin")

    def test_apt_packages_are_checked_one_by_one(self):
        """Colab'da ffmpeg hazır gelir ama fontlar gelmez; ffmpeg varlığı font kurulumunu atlatmamalı."""
        from tools import colab_studio

        with tempfile.TemporaryDirectory() as tmp:
            present = Path(tmp) / "ffmpeg"
            present.touch()
            packages = {"ffmpeg": str(present), "fonts-dejavu-core": str(Path(tmp) / "DejaVuSans-Bold.ttf")}
            self.assertEqual(colab_studio.missing_apt_packages(packages), ["fonts-dejavu-core"])
        self.assertIn("fonts-dejavu-core", colab_studio.APT_PACKAGES)

    def test_install_fingerprint_follows_requirements(self):
        from tools import colab_studio

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("requirements.txt", "requirements-tts.txt", "requirements-chatterbox.txt",
                         "install_chatterbox.py"):
                (root / name).write_text("a==1\n")
            first = colab_studio.requirements_fingerprint(root)
            self.assertEqual(first, colab_studio.requirements_fingerprint(root))
            self.assertNotEqual(first, colab_studio.requirements_fingerprint(root, chatterbox=True))
            (root / "requirements-tts.txt").write_text("a==2\n")
            self.assertNotEqual(first, colab_studio.requirements_fingerprint(root))


if __name__ == "__main__":
    unittest.main()
