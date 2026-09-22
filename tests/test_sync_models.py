import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import sync_models_to_vps as sync


class SyncModelsTests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, sync.__file__, *args], capture_output=True, text=True, encoding="utf-8")

    def test_missing_models_dir_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sync, "MODELS_DIR", Path(tmp) / "yok"):
            with self.assertRaises(SystemExit) as raised:
                with patch.object(sys, "argv", ["sync", "kullanici@vps"]):
                    sync.main()
            self.assertIn("bulunamadı", str(raised.exception))

    def test_empty_models_dir_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sync, "MODELS_DIR", Path(tmp)):
            with self.assertRaises(SystemExit) as raised:
                with patch.object(sys, "argv", ["sync", "kullanici@vps"]):
                    sync.main()
            self.assertIn("boş", str(raised.exception))

    def test_dry_run_builds_the_expected_scp_command_without_running_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            (models / "piper").mkdir()
            (models / "piper" / "voice.onnx").write_bytes(b"x")
            with patch.object(sync, "MODELS_DIR", models), patch("subprocess.run") as run:
                with patch.object(sys, "argv", ["sync", "user@vps.example", "--port", "2222", "--identity", "k", "--dry-run"]):
                    sync.main()
                run.assert_not_called()

    def test_real_run_invokes_scp_with_the_right_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            (models / "piper").mkdir()
            with patch.object(sync, "MODELS_DIR", models), patch("subprocess.run") as run:
                run.return_value.returncode = 0
                with patch.object(sys, "argv", ["sync", "user@vps.example", "--remote-dir", "kavra2", "--port", "2222", "-i", "k"]):
                    sync.main()
                cmd = run.call_args[0][0]
                self.assertEqual(cmd[:2], ["scp", "-r"])
                self.assertIn("2222", cmd)
                self.assertIn("k", cmd)
                self.assertEqual(cmd[-1], "user@vps.example:kavra2/")
                self.assertEqual(cmd[-2], str(models))

    def test_scp_failure_raises_a_helpful_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            (models / "piper").mkdir()
            with patch.object(sync, "MODELS_DIR", models), patch("subprocess.run") as run:
                run.return_value.returncode = 1
                with patch.object(sys, "argv", ["sync", "user@vps.example"]):
                    with self.assertRaises(SystemExit) as raised:
                        sync.main()
                    self.assertIn("başarısız", str(raised.exception))

    def test_cli_help_runs_without_error(self):
        result = self._run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("models/", result.stdout)


if __name__ == "__main__":
    unittest.main()
