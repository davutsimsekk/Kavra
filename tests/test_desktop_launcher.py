import tempfile
import unittest
from pathlib import Path

from studio_web.desktop import browser_app_args, browser_candidates, find_app_browser


class DesktopLauncherTests(unittest.TestCase):
    def test_browser_candidates_prefer_edge_before_chrome(self):
        candidates = browser_candidates({"PROGRAMFILES": "C:/Apps"})

        self.assertEqual(candidates[0][0], "Microsoft Edge")
        self.assertTrue(str(candidates[0][1]).endswith("Microsoft\\Edge\\Application\\msedge.exe"))
        self.assertEqual(candidates[1][0], "Google Chrome")

    def test_finds_existing_app_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edge = root / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            edge.parent.mkdir(parents=True)
            edge.touch()

            found = find_app_browser({"PROGRAMFILES": str(root)})

            self.assertIsNotNone(found)
            self.assertEqual(found[0], "Microsoft Edge")
            self.assertEqual(found[1], edge)

    def test_app_mode_has_isolated_profile_and_no_browser_chrome(self):
        executable = Path("C:/Edge/msedge.exe")
        profile = Path("D:/cache/session_1")

        args = browser_app_args(executable, "http://127.0.0.1:8768", profile)

        self.assertIn("--app=http://127.0.0.1:8768", args)
        self.assertIn(f"--user-data-dir={profile}", args)
        self.assertIn("--no-first-run", args)


if __name__ == "__main__":
    unittest.main()
