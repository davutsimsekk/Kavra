import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.config as config
from app.models import Slide
from app.tts.remote import token_env_name
from studio_web.api import app
from tests.test_remote_tts import TOKEN, running_server
from tests.test_web_api import _temp_project

ORIGIN = {"Origin": "http://127.0.0.1:5173"}


class RemoteTtsProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        # Gerçek settings.json ve .env dosyalarına dokunma.
        for p in (patch.object(config, "SETTINGS_PATH", tmp / "settings.json"),
                  patch.object(config, "ENV_PATH", tmp / ".env"),
                  patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        for name in ("pc", "colab"):
            os.environ.pop(token_env_name(name), None)
        self.addCleanup(self._tmp.cleanup)

    def _put(self, profile, url="https://ornek.trycloudflare.com", token=TOKEN):
        return self.client.put(f"/api/remote-tts/{profile}", headers=ORIGIN, json={"url": url, "token": token})

    def test_starts_unconfigured_with_both_fixed_profiles(self):
        state = self.client.get("/api/remote-tts").json()
        self.assertEqual(state, {"active": None, "configured": False, "profiles": {
            "pc": {"url": "", "tokenSet": False}, "colab": {"url": "", "tokenSet": False}}})

    def test_unknown_profile_name_is_rejected(self):
        for method, path in [("PUT", "/api/remote-tts/vps"), ("DELETE", "/api/remote-tts/vps"),
                             ("POST", "/api/remote-tts/vps/test")]:
            self.assertEqual(self.client.request(method, path, headers=ORIGIN, json={"url": "https://x.com"}).status_code, 404)
        self.assertEqual(self.client.post("/api/remote-tts/active", headers=ORIGIN, json={"profile": "vps"}).status_code, 404)

    def test_saving_a_profile_never_echoes_the_token_anywhere(self):
        response = self._put("pc")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["profiles"]["pc"], {"url": "https://ornek.trycloudflare.com", "tokenSet": True})
        self.assertEqual(body["profiles"]["colab"], {"url": "", "tokenSet": False})
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn(TOKEN, self.client.get("/api/remote-tts").text)
        self.assertNotIn(TOKEN, self.client.get("/api/bootstrap").text)
        self.assertIn(TOKEN, (Path(self._tmp.name) / ".env").read_text(encoding="utf-8"))

    def test_two_profiles_are_independent(self):
        self._put("pc", url="https://pc.tailnet.ts.net", token="pc-token-0123456789abcdef")
        self._put("colab", url="https://colab.trycloudflare.com", token="colab-token-0123456789ab")
        state = self.client.get("/api/remote-tts").json()
        self.assertEqual(state["profiles"]["pc"]["url"], "https://pc.tailnet.ts.net")
        self.assertEqual(state["profiles"]["colab"]["url"], "https://colab.trycloudflare.com")
        self.assertTrue(state["profiles"]["pc"]["tokenSet"] and state["profiles"]["colab"]["tokenSet"])

    def test_blank_token_keeps_the_existing_one(self):
        self._put("pc")
        state = self.client.put("/api/remote-tts/pc", headers=ORIGIN,
                                json={"url": "https://yeni.trycloudflare.com", "token": ""}).json()
        self.assertEqual(state["profiles"]["pc"], {"url": "https://yeni.trycloudflare.com", "tokenSet": True})

    def test_delete_clears_url_token_and_deactivates_if_active(self):
        self._put("pc")
        self.client.post("/api/remote-tts/active", headers=ORIGIN, json={"profile": "pc"})
        cleared = self.client.delete("/api/remote-tts/pc", headers=ORIGIN).json()
        self.assertEqual(cleared["profiles"]["pc"], {"url": "", "tokenSet": False})
        self.assertIsNone(cleared["active"])
        self.assertNotIn(TOKEN, (Path(self._tmp.name) / ".env").read_text(encoding="utf-8"))

    def test_rejects_bad_url_and_token(self):
        self.assertEqual(self._put("pc", url="http://ornek.com").status_code, 400)
        self.assertEqual(self._put("pc", url="https://ornek.com/yol").status_code, 400)
        self.assertEqual(self._put("pc", token="kisa").status_code, 400)
        self.assertEqual(self._put("pc", token="bosluk iceren token 123456").status_code, 400)

    def test_switching_active_profile(self):
        self._put("pc")
        self._put("colab")
        self.assertEqual(self.client.post("/api/remote-tts/active", headers=ORIGIN, json={"profile": "colab"}).json()["active"], "colab")
        self.assertEqual(self.client.get("/api/remote-tts").json()["active"], "colab")
        self.assertEqual(self.client.post("/api/remote-tts/active", headers=ORIGIN, json={"profile": None}).json()["active"], None)

    def test_bootstrap_reflects_active_configured_profile(self):
        self.assertFalse(self.client.get("/api/bootstrap").json()["remoteTts"]["configured"])
        self._put("pc")
        self.assertFalse(self.client.get("/api/bootstrap").json()["remoteTts"]["configured"])  # kayıtlı ama aktif değil
        self.client.post("/api/remote-tts/active", headers=ORIGIN, json={"profile": "pc"})
        self.assertTrue(self.client.get("/api/bootstrap").json()["remoteTts"]["configured"])

    def test_connection_test_targets_the_given_profile_regardless_of_active(self):
        self.assertFalse(self.client.post("/api/remote-tts/pc/test", headers=ORIGIN).json()["ok"])
        with running_server() as (url, _):
            self._put("colab", url=url)  # "pc" hâlâ boş, aktif de değil
            result = self.client.post("/api/remote-tts/colab/test", headers=ORIGIN).json()
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["engines"]["coqui"]["available"])
            self.assertFalse(self.client.post("/api/remote-tts/pc/test", headers=ORIGIN).json()["ok"])
            self._put("colab", url=url, token="yanlis-token-yanlis-token")
            bad = self.client.post("/api/remote-tts/colab/test", headers=ORIGIN).json()
            self.assertFalse(bad["ok"])
            self.assertIn("token", bad["error"].lower())


class RenderBackendValidationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        for p in (patch.object(config, "SETTINGS_PATH", tmp / "settings.json"),
                  patch.object(config, "ENV_PATH", tmp / ".env"),
                  patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        for name in ("pc", "colab"):
            os.environ.pop(token_env_name(name), None)
        self.addCleanup(self._tmp.cleanup)

    def _render(self, pdir, **body):
        return self.client.post(f"/api/projects/{pdir.name}/render", headers=ORIGIN, json=body)

    def _activate(self, profile, url, token=TOKEN):
        self.client.put(f"/api/remote-tts/{profile}", headers=ORIGIN, json={"url": url, "token": token})
        self.client.post("/api/remote-tts/active", headers=ORIGIN, json={"profile": profile})

    @staticmethod
    def _slide():
        return Slide(title="Slayt", narration="Kısa bir anlatım metni burada duruyor.")

    def test_rejects_unknown_backend_and_bad_concurrency(self):
        with _temp_project([self._slide()]) as pdir:
            self.assertEqual(self._render(pdir, ttsProvider="coqui", ttsBackend="bulut").status_code, 400)
            self.assertEqual(self._render(pdir, ttsProvider="coqui", remoteTtsConcurrency=9).status_code, 400)
            self.assertEqual(self._render(pdir, ttsProvider="coqui", remoteTtsConcurrency="çok").status_code, 400)

    def test_remote_is_refused_for_engines_that_cannot_run_remotely(self):
        with _temp_project([self._slide()]) as pdir:
            for provider in ("edge", "chatterbox", "anka", "elevenlabs"):
                response = self._render(pdir, ttsProvider=provider, ttsBackend="remote")
                self.assertEqual(response.status_code, 400, provider)

    def test_remote_without_an_active_profile_fails_fast_with_a_clear_message(self):
        with _temp_project([self._slide()]) as pdir:
            response = self._render(pdir, ttsProvider="coqui", ttsBackend="remote")
            self.assertEqual(response.status_code, 400)
            self.assertIn("ayarlanmamış", response.json()["detail"])

    def test_active_profile_with_incomplete_settings_names_the_profile(self):
        with _temp_project([self._slide()]) as pdir:
            self.client.post("/api/remote-tts/active", headers=ORIGIN, json={"profile": "colab"})
            response = self._render(pdir, ttsProvider="coqui", ttsBackend="remote")
            self.assertEqual(response.status_code, 400)
            self.assertIn("Colab", response.json()["detail"])

    def test_remote_with_unreachable_server_fails_before_a_job_is_created(self):
        with _temp_project([self._slide()]) as pdir, patch("studio_web.api._render_in_isolated_process") as fake:
            self._activate("pc", "http://127.0.0.1:9")
            response = self._render(pdir, ttsProvider="coqui", ttsBackend="remote")
            self.assertEqual(response.status_code, 400)
            fake.assert_not_called()

    def test_remote_options_use_whichever_profile_is_active(self):
        with running_server() as (url, _), _temp_project([self._slide()]) as pdir:
            self._activate("colab", url)
            with patch("studio_web.api._render_in_isolated_process", return_value={}) as fake:
                response = self._render(pdir, ttsProvider="coqui", ttsBackend="remote", remoteTtsConcurrency=3)
                self.assertEqual(response.status_code, 200)
                for _ in range(50):
                    if self.client.get(f"/api/jobs/{response.json()['jobId']}").json()["status"] in {"complete", "failed"}:
                        break
                    time.sleep(0.05)
                options = fake.call_args[0][-1]
                self.assertEqual((options.tts_backend, options.remote_tts_concurrency), ("remote", 3))

    def test_switching_active_profile_switches_which_gpu_is_used(self):
        with running_server() as (good_url, _), _temp_project([self._slide()]) as pdir:
            self._activate("pc", "http://127.0.0.1:9")     # ölü sunucu
            self._activate("colab", good_url)               # ve şimdi colab'ı aktif yap
            with patch("studio_web.api._render_in_isolated_process", return_value={}) as fake:
                response = self._render(pdir, ttsProvider="coqui", ttsBackend="remote")
                self.assertEqual(response.status_code, 200, response.text)
                for _ in range(50):
                    if self.client.get(f"/api/jobs/{response.json()['jobId']}").json()["status"] in {"complete", "failed"}:
                        break
                    time.sleep(0.05)
                fake.assert_called_once()

    def test_default_backend_stays_local(self):
        with _temp_project([self._slide()]) as pdir, patch("studio_web.api._render_in_isolated_process", return_value={}) as fake:
            response = self._render(pdir, ttsProvider="edge")
            self.assertEqual(response.status_code, 200)
            for _ in range(50):
                if self.client.get(f"/api/jobs/{response.json()['jobId']}").json()["status"] in {"complete", "failed"}:
                    break
                time.sleep(0.05)
            self.assertEqual(fake.call_args[0][-1].tts_backend, "local")


if __name__ == "__main__":
    unittest.main()
