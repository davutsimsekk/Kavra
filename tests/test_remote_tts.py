import hashlib
import os
import socket
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import requests
import uvicorn

import app.config as remote_config
from app.models import SynthResult
from app.tts import remote
from app.tts.remote import RemoteTTSClient, RemoteTTSConfig, RemoteTTSError, synthesize_remote
from tts_server.server import EnginePool, create_app

TOKEN = "test-token-0123456789abcdef"


class FakeProvider:
    """Motoru taklit eder ve gördüğü sesi/dosyaları kaydeder."""
    calls: list[dict] = []
    delay = 0.0

    def synthesize(self, text, voice, out_path, rate="+0%"):
        if "BOOM" in text:
            raise RuntimeError("CUDA out of memory (sahte)")
        if self.delay:
            time.sleep(self.delay)
        seen = {"text": text, "voice": voice, "rate": rate}
        if voice != "builtin:default":
            vdir = Path(voice).parent
            seen["files"] = {p.name: p.read_bytes() for p in vdir.iterdir()}
        FakeProvider.calls.append(seen)
        Path(out_path).write_bytes(b"AUDIO:" + text.encode("utf-8"))
        return SynthResult(duration=len(text) / 10)


@contextmanager
def running_server(*, engines=("coqui", "piper"), models=1, token=TOKEN):
    FakeProvider.calls = []
    FakeProvider.delay = 0.0
    with tempfile.TemporaryDirectory() as tmp:
        pools = {name: EnginePool(name, FakeProvider, models) for name in engines}
        app = create_app(token=token, data_dir=Path(tmp), pools=pools)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        try:
            yield f"http://127.0.0.1:{port}", Path(tmp)
        finally:
            server.should_exit = True
            thread.join(10)


def make_client(url, token=TOKEN):
    return RemoteTTSClient(RemoteTTSConfig(url=url, token=token), poll_interval=0.01)


class ServerTests(unittest.TestCase):
    def test_every_endpoint_requires_the_token(self):
        with running_server() as (url, _):
            for headers in ({}, {"Authorization": "Bearer yanlis-token-yanlis-token"}, {"Authorization": TOKEN}):
                self.assertEqual(requests.get(url + "/health", headers=headers).status_code, 401)
                self.assertEqual(requests.post(url + "/jobs", json={}, headers=headers).status_code, 401)
                self.assertEqual(requests.put(url + f"/assets/{'a' * 64}", data=b"x", headers=headers).status_code, 401)
            ok = requests.get(url + "/health", headers={"Authorization": f"Bearer {TOKEN}"})
            self.assertEqual(ok.status_code, 200)

    def test_short_token_is_refused_at_startup(self):
        with self.assertRaises(ValueError):
            create_app(token="kisa", data_dir=Path(tempfile.gettempdir()), pools={})

    def test_health_reports_engines(self):
        with running_server(engines=("coqui",)) as (url, _):
            info = make_client(url).health()
            self.assertEqual(info["protocol"], 1)
            self.assertTrue(info["engines"]["coqui"]["available"])
            self.assertFalse(info["engines"]["piper"]["available"])

    def test_asset_upload_verifies_digest_and_is_idempotent(self):
        with running_server() as (url, _):
            auth = {"Authorization": f"Bearer {TOKEN}"}
            data = b"referans-ses"
            sha = hashlib.sha256(data).hexdigest()
            self.assertEqual(requests.head(url + f"/assets/{sha}", headers=auth).status_code, 404)
            self.assertEqual(requests.put(url + f"/assets/{sha}", data=b"baska", headers=auth).status_code, 400)
            self.assertEqual(requests.put(url + f"/assets/{sha}", data=data, headers=auth).status_code, 201)
            self.assertEqual(requests.put(url + f"/assets/{sha}", data=data, headers=auth).status_code, 200)
            self.assertEqual(requests.head(url + f"/assets/{sha}", headers=auth).status_code, 200)

    def test_job_is_deduplicated_by_request_id(self):
        with running_server() as (url, _):
            auth = {"Authorization": f"Bearer {TOKEN}"}
            body = {"engine": "coqui", "text": "merhaba", "voice": {"kind": "builtin"}, "requestId": "abcdefgh12345678"}
            first = requests.post(url + "/jobs", json=body, headers=auth).json()
            second = requests.post(url + "/jobs", json=body, headers=auth).json()
            self.assertEqual(first["id"], second["id"])

    def test_missing_assets_are_reported_with_409(self):
        with running_server() as (url, _):
            auth = {"Authorization": f"Bearer {TOKEN}"}
            voice = {"kind": "assets", "files": {"reference.wav": "b" * 64}, "primary": "reference.wav"}
            response = requests.post(url + "/jobs", headers=auth, json={
                "engine": "coqui", "text": "x", "voice": voice, "requestId": "abcdefgh12345678"})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["missing"], ["b" * 64])

    def test_rejects_unsafe_input(self):
        with running_server() as (url, _):
            auth = {"Authorization": f"Bearer {TOKEN}"}
            base = {"engine": "coqui", "text": "x", "voice": {"kind": "builtin"}, "requestId": "abcdefgh12345678"}
            for change in ({"engine": "chatterbox"}, {"text": "  "}, {"requestId": "../x"},
                           {"voice": {"kind": "assets", "files": {"../evil": "a" * 64}, "primary": "../evil"}}):
                response = requests.post(url + "/jobs", headers=auth, json={**base, **change})
                self.assertEqual(response.status_code, 400, change)


class ClientTests(unittest.TestCase):
    def test_synthesizes_in_order_with_progress_and_status(self):
        with running_server(models=2) as (url, _), tempfile.TemporaryDirectory() as out:
            FakeProvider.delay = 0.05
            texts = [f"slayt numarasi {i}" for i in range(6)]
            items = [(t, "builtin:default", Path(out) / f"s{i}.mp3") for i, t in enumerate(texts)]
            progress, status = [], []
            results = synthesize_remote(items, "coqui", "+0%", 3, client=make_client(url),
                                        progress_cb=lambda d, t: progress.append((d, t)), status_cb=status.append)
            for i, (text, _v, path) in enumerate(items):
                self.assertEqual(path.read_bytes(), b"AUDIO:" + text.encode("utf-8"))
                self.assertAlmostEqual(results[i].duration, len(text) / 10)
            self.assertEqual(progress[-1], (6, 6))
            self.assertEqual([p[0] for p in progress], list(range(1, 7)))
            self.assertIn("Uzak GPU'ya bağlanılıyor…", status)

    def test_cloned_reference_is_uploaded_once_and_used(self):
        with running_server() as (url, _), tempfile.TemporaryDirectory() as out:
            wav = Path(out) / "ses.wav"
            wav.write_bytes(b"RIFF-referans-ses" * 100)
            client = make_client(url)
            items = [(f"metin {i}", str(wav), Path(out) / f"a{i}.mp3") for i in range(3)]
            synthesize_remote(items, "coqui", "+0%", 2, client=client)
            self.assertEqual(len(client._uploaded), 1)
            self.assertEqual(len(FakeProvider.calls), 3)
            for call in FakeProvider.calls:
                self.assertEqual(call["files"], {"reference.wav": wav.read_bytes()})

    def test_piper_model_and_config_land_side_by_side(self):
        with running_server() as (url, _), tempfile.TemporaryDirectory() as out:
            model = Path(out) / "tr_TR-dfki-medium.onnx"
            model.write_bytes(b"onnx-modeli")
            Path(f"{model}.json").write_bytes(b'{"sample_rate": 22050}')
            synthesize_remote([("merhaba", str(model), Path(out) / "p.mp3")], "piper", "+0%", 1, client=make_client(url))
            call = FakeProvider.calls[0]
            self.assertEqual(call["files"], {"voice.onnx": b"onnx-modeli", "voice.onnx.json": b'{"sample_rate": 22050}'})
            self.assertTrue(call["voice"].endswith("voice.onnx"))

    def test_engine_failure_surfaces_the_server_message(self):
        with running_server() as (url, _), tempfile.TemporaryDirectory() as out:
            items = [("iyi", "builtin:default", Path(out) / "a.mp3"), ("BOOM", "builtin:default", Path(out) / "b.mp3")]
            with self.assertRaises(RemoteTTSError) as raised:
                synthesize_remote(items, "coqui", "+0%", 1, client=make_client(url))
            self.assertIn("CUDA out of memory", str(raised.exception))

    def test_wrong_token_gives_actionable_error(self):
        with running_server() as (url, _), tempfile.TemporaryDirectory() as out:
            client = make_client(url, token="yanlis-token-yanlis-token")
            with self.assertRaises(RemoteTTSError) as raised:
                synthesize_remote([("x", "builtin:default", Path(out) / "a.mp3")], "coqui", "+0%", 1, client=client)
            self.assertIn("token", str(raised.exception).lower())

    def test_unavailable_engine_is_reported_before_any_work(self):
        with running_server(engines=("coqui",)) as (url, _), tempfile.TemporaryDirectory() as out:
            with self.assertRaises(RemoteTTSError) as raised:
                synthesize_remote([("x", "m.onnx", Path(out) / "a.mp3")], "piper", "+0%", 1, client=make_client(url))
            self.assertIn("piper", str(raised.exception))

    def test_unreachable_server_fails_with_recovery_hint_and_keeps_no_partial_file(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as out, patch.object(remote.time, "sleep"):
            client = make_client(f"http://127.0.0.1:{port}")
            with self.assertRaises(RemoteTTSError) as raised:
                client.synthesize("coqui", "x", "builtin:default", Path(out) / "a.mp3")
            self.assertIn("yeniden başlat", str(raised.exception))
            self.assertEqual(list(Path(out).iterdir()), [])


class PreflightTests(unittest.TestCase):
    def test_server_down_at_start_gives_the_recovery_hint(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(RemoteTTSError) as raised:
                synthesize_remote([("x", "builtin:default", Path(out) / "a.mp3")], "coqui", "+0%", 1,
                                  client=make_client(f"http://127.0.0.1:{port}"))
            self.assertIn("yeniden başlat", str(raised.exception))


class ProfileConfigTests(unittest.TestCase):
    """RemoteTTSConfig.load()/for_profile() (bkz. studio_web/remote_tts_routes.py rota katmanı için ayrı testler)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        for p in (patch.object(remote_config, "SETTINGS_PATH", self.dir / "settings.json"),
                  patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        for name in ("pc", "colab"):
            os.environ.pop(remote.token_env_name(name), None)
        self.addCleanup(self._tmp.cleanup)

    def _set(self, profile, url):
        settings = remote_config.load_settings()
        profiles = dict(settings.get("remote_tts_profiles") or {})
        profiles[profile] = {"url": url}
        settings["remote_tts_profiles"] = profiles
        remote_config.save_settings(settings)

    def test_load_with_no_active_profile_is_a_clear_actionable_error(self):
        with self.assertRaises(RemoteTTSError) as raised:
            remote.RemoteTTSConfig.load()
        self.assertIn("aktif yap", str(raised.exception))

    def test_load_uses_the_active_profile(self):
        self._set("colab", "https://abc.trycloudflare.com")
        os.environ[remote.token_env_name("colab")] = "colab-token-0123456789ab"
        settings = remote_config.load_settings()
        settings["remote_tts_active_profile"] = "colab"
        remote_config.save_settings(settings)
        config = remote.RemoteTTSConfig.load()
        self.assertEqual((config.url, config.token, config.profile), ("https://abc.trycloudflare.com", "colab-token-0123456789ab", "colab"))

    def test_load_names_the_active_profile_when_incomplete(self):
        settings = remote_config.load_settings()
        settings["remote_tts_active_profile"] = "pc"
        remote_config.save_settings(settings)
        with self.assertRaises(RemoteTTSError) as raised:
            remote.RemoteTTSConfig.load()
        self.assertIn("Bu bilgisayar", str(raised.exception))

    def test_for_profile_ignores_which_one_is_active(self):
        self._set("pc", "https://pc.tailnet.ts.net")
        os.environ[remote.token_env_name("pc")] = "pc-token-0123456789abcdef"
        config = remote.RemoteTTSConfig.for_profile("pc")  # aktif profil ayarlanmamış olsa da çalışır
        self.assertEqual(config.url, "https://pc.tailnet.ts.net")

    def test_unknown_profile_name_is_rejected(self):
        with self.assertRaises(RemoteTTSError):
            remote.RemoteTTSConfig.for_profile("vps")


class UrlTests(unittest.TestCase):
    def test_normalize_url(self):
        self.assertEqual(remote.normalize_url(" https://Abc-Def.trycloudflare.com/ "), "https://abc-def.trycloudflare.com")
        self.assertEqual(remote.normalize_url("http://127.0.0.1:8790"), "http://127.0.0.1:8790")
        for bad in ("", "abc.trycloudflare.com", "http://abc.trycloudflare.com", "https://u:p@abc.com",
                    "https://abc.com/path", "ftp://abc.com", "https://abc.com?x=1"):
            with self.subTest(bad=bad), self.assertRaises(RemoteTTSError):
                remote.normalize_url(bad)


if __name__ == "__main__":
    unittest.main()
