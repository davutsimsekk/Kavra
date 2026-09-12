import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient

from studio_web.api import _project_dir, app


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")

    def test_bootstrap_exposes_ui_catalogs_without_secret_values(self):
        response = self.client.get("/api/bootstrap")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(len(payload["themes"]), 7)
        self.assertIn("agent_command", payload["settings"])
        self.assertEqual(set(payload["keysConfigured"]), {"gemini", "openai", "elevenlabs"})
        self.assertNotIn("apiKey", response.text)

    def test_project_path_traversal_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            _project_dir("../outside")
        self.assertEqual(raised.exception.status_code, 400)

    def test_external_browser_origin_cannot_mutate_local_api(self):
        response = self.client.post(
            "/api/source/path",
            headers={"Origin": "https://example.invalid"},
            json={"path": "does-not-matter.pdf"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
