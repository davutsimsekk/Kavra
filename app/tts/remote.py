"""Uzak GPU'da çalışan Kavra TTS sunucusuna (bkz. tts_server/server.py) istemci.

GPU'suz bir Kavra sunucusu XTTS v2 / Piper seslendirmesini bu istemciyle Colab gibi bir
GPU makinesine devreder. HTTP olduğu için CUDA/torch gerektirmez ve izole render
sürecinde çalışır; süreç iptal edilirse istemci de onunla ölür.

Dayanıklılık: her slayt kısa istekli bir iş olarak gönderilir (tünel ~100 sn'de uzun
istekleri keser). Geçici hatalarda (tünel/sunucu yanıt vermiyor) aynı ``requestId`` ile
yeniden gönderilir; sunucu tekrarı üretmez. Biten sesler zaten diskte önbellekte olduğundan
render yeniden başlatılınca kaldığı yerden devam eder.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests

from app.models import SynthResult

REMOTE_ENGINES = ("coqui", "piper")
PROTOCOL_VERSION = 1
MAX_TRANSIENT_FAILURES = 4
JOB_TIMEOUT_SECONDS = 15 * 60
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Sabit iki GPU kaynağı: kendi bilgisayarın (Tailscale üzerinden) ve Colab. İkisi de aynı
# protokolü konuşur (tts_server/server.py); tek fark hangisinin adres/token'ı aktif olduğu.
# Render, ayarlardaki "aktif" profili kullanır; ikisi de kaydedilip tek tıkla değiştirilebilir.
PROFILES = ("pc", "colab")
PROFILE_LABELS = {"pc": "Bu bilgisayar (Tailscale)", "colab": "Colab"}


class RemoteTTSError(RuntimeError):
    """Kullanıcıya gösterilebilecek, tekrar denemenin çözmeyeceği hata."""


class TransientRemoteError(RemoteTTSError):
    """Tünel/sunucu geçici olarak yanıt vermiyor; yeniden denenebilir."""


def _unreachable(cause: Exception) -> RemoteTTSError:
    return RemoteTTSError(
        f"Uzak GPU'ya ulaşılamıyor ({cause}). Colab oturumu kapanmış veya tünel adresi "
        "yenilenmiş olabilir; ayarlardaki adresi güncelleyip renderı yeniden başlat "
        "(biten sesler korunur)."
    )


def normalize_url(raw: str) -> str:
    value = (raw or "").strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise RemoteTTSError("Adres http:// veya https:// ile başlamalı (örn. https://ornek.trycloudflare.com).")
    if parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise RemoteTTSError("Adres yalnızca sunucu adını içermeli; yol, kullanıcı adı veya parametre eklenmemeli.")
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK_HOSTS:
        raise RemoteTTSError("Uzak GPU adresi https olmalı (yalnızca localhost için http kabul edilir).")
    return f"{parts.scheme}://{parts.netloc.lower()}"


def _require_profile(profile: str) -> str:
    if profile not in PROFILES:
        raise RemoteTTSError(f"Bilinmeyen GPU profili: {profile!r} (geçerli: {', '.join(PROFILES)}).")
    return profile


def token_env_name(profile: str) -> str:
    return f"KAVRA_REMOTE_TTS_TOKEN_{_require_profile(profile).upper()}"


def profile_url(profile: str, settings: dict | None = None) -> str:
    from app.config import load_settings

    settings = settings if settings is not None else load_settings()
    profiles = settings.get("remote_tts_profiles") or {}
    return str((profiles.get(profile) or {}).get("url", "")).strip()


@dataclass(frozen=True)
class RemoteTTSConfig:
    url: str
    token: str
    profile: str = ""

    @classmethod
    def for_profile(cls, profile: str) -> "RemoteTTSConfig":
        """Belirli bir profil (aktif olsun ya da olmasın) için yapılandırma; bağlantı testinde kullanılır."""
        _require_profile(profile)
        url = profile_url(profile)
        token = os.environ.get(token_env_name(profile), "").strip()
        label = PROFILE_LABELS[profile]
        if not url or not token:
            raise RemoteTTSError(f"{label} ayarlanmamış: adres ve token'ı Ses ayarlarından gir.")
        return cls(url=normalize_url(url), token=token, profile=profile)

    @classmethod
    def load(cls) -> "RemoteTTSConfig":
        """Render'ın kullanacağı config: ayarlardaki AKTİF profil."""
        from app.config import load_settings

        active = str(load_settings().get("remote_tts_active_profile", "")).strip()
        if not active:
            raise RemoteTTSError(
                "Uzak GPU ayarlanmamış: Ses ayarlarında bir GPU profili (Bu bilgisayar / Colab) seçip aktif yap."
            )
        try:
            return cls.for_profile(active)
        except RemoteTTSError as exc:
            raise RemoteTTSError(f"Aktif GPU profili ({PROFILE_LABELS.get(active, active)}): {exc}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RemoteTTSClient:
    def __init__(self, config: RemoteTTSConfig, *, poll_interval: float = 1.0):
        self.config = config
        self.poll_interval = poll_interval
        self.run_id = uuid.uuid4().hex[:8]
        self.stop = threading.Event()
        self._local = threading.local()
        self._lock = threading.Lock()
        self._voices: dict[tuple[str, str], tuple[dict, dict[str, Path]]] = {}
        self._uploaded: set[str] = set()

    # --- HTTP ---------------------------------------------------------------
    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["Authorization"] = f"Bearer {self.config.token}"
            self._local.session = session
        return session

    def _request(self, method: str, path: str, *, timeout=(10, 30), **kwargs) -> requests.Response:
        try:
            response = self._session().request(method, self.config.url + path, timeout=timeout,
                                               allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise TransientRemoteError(f"Uzak GPU'ya ulaşılamadı: {type(exc).__name__}") from exc
        code = response.status_code
        if code in (401, 403):
            raise RemoteTTSError("Uzak GPU token'ı reddetti; Colab'da yazdırılan token ile ayardakinin aynı olduğundan emin ol.")
        if 300 <= code < 400:
            raise RemoteTTSError("Uzak GPU adresi yönlendirme yaptı; adres yanlış veya eski olabilir.")
        if code in (502, 503, 504) or 520 <= code <= 530:
            raise TransientRemoteError(f"Tünel veya uzak sunucu yanıt vermedi (HTTP {code}).")
        return response

    def health(self, timeout: float = 8.0) -> dict:
        response = self._request("GET", "/health", timeout=(timeout, timeout))
        if response.status_code == 404:
            raise RemoteTTSError("Bu adreste Kavra TTS sunucusu bulunamadı.")
        if not response.ok:
            raise RemoteTTSError(f"Uzak GPU sağlık kontrolü başarısız (HTTP {response.status_code}).")
        info = response.json()
        if info.get("protocol") != PROTOCOL_VERSION:
            raise RemoteTTSError("Uzak sunucunun protokol sürümü bu Kavra ile uyumsuz; not defterindeki paketi güncelle.")
        return info

    # --- Ses referansları -------------------------------------------------
    def _voice(self, engine: str, voice: str) -> tuple[dict, dict[str, Path]]:
        key = (engine, voice)
        with self._lock:
            cached = self._voices.get(key)
        if cached:
            return cached
        if engine == "coqui":
            if not voice or voice == "builtin:default":
                built: tuple[dict, dict[str, Path]] = ({"kind": "builtin"}, {})
            else:
                path = Path(voice)
                if not path.is_file():
                    raise RemoteTTSError(f"Klon referansı bulunamadı: {path.name}")
                sha = _sha256_file(path)
                built = ({"kind": "assets", "files": {"reference.wav": sha}, "primary": "reference.wav"}, {sha: path})
        elif engine == "piper":
            model, config = Path(voice), Path(f"{voice}.json")
            if not model.is_file() or not config.is_file():
                raise RemoteTTSError(f"Piper modeli veya yapılandırması bulunamadı: {model.name}")
            model_sha, config_sha = _sha256_file(model), _sha256_file(config)
            built = ({"kind": "assets", "files": {"voice.onnx": model_sha, "voice.onnx.json": config_sha},
                      "primary": "voice.onnx"}, {model_sha: model, config_sha: config})
        else:
            raise RemoteTTSError(f"'{engine}' motoru uzakta desteklenmiyor.")
        with self._lock:
            self._voices[key] = built
        return built

    def _upload(self, sha: str, path: Path) -> None:
        with self._lock:
            if sha in self._uploaded:
                return
        head = self._request("HEAD", f"/assets/{sha}")
        if head.status_code != 200:
            with open(path, "rb") as handle:
                response = self._request("PUT", f"/assets/{sha}", data=handle, timeout=(10, 900))
            if not response.ok:
                raise RemoteTTSError(f"Ses dosyası yüklenemedi ({path.name}): HTTP {response.status_code}")
        with self._lock:
            self._uploaded.add(sha)

    # --- Seslendirme -------------------------------------------------------
    def _submit(self, engine: str, text: str, voice: str, rate: str, request_id: str) -> str:
        spec, files = self._voice(engine, voice)
        for sha, path in files.items():
            self._upload(sha, path)
        body = {"engine": engine, "text": text, "voice": spec, "rate": rate, "requestId": request_id}
        response = self._request("POST", "/jobs", json=body)
        if response.status_code == 409:  # sunucu dosyayı kaybetmiş (yeniden başlamış)
            with self._lock:
                self._uploaded.clear()
            raise TransientRemoteError("Sunucu ses dosyalarını yeniden istiyor.")
        if response.status_code == 400:
            raise RemoteTTSError(f"Uzak GPU isteği reddetti: {_detail(response)}")
        if not response.ok:
            raise TransientRemoteError(f"İş oluşturulamadı (HTTP {response.status_code}).")
        return response.json()["id"]

    def _await(self, job_id: str, out_path: Path) -> SynthResult:
        deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
        poll_failures = 0
        while True:
            if self.stop.is_set():
                raise RemoteTTSError("Seslendirme durduruldu.")
            if time.monotonic() > deadline:
                raise RemoteTTSError("Uzak GPU 15 dakikada bir slaytı bitiremedi.")
            try:
                response = self._request("GET", f"/jobs/{job_id}")
                if response.status_code == 404:
                    raise TransientRemoteError("İş sunucuda bulunamadı (oturum yenilenmiş olabilir).")
                status = response.json()
                poll_failures = 0
            except TransientRemoteError:
                poll_failures += 1
                if poll_failures >= 3:
                    raise
                time.sleep(self.poll_interval)
                continue
            if status["status"] == "error":
                raise RemoteTTSError(f"Uzak GPU sesi üretemedi: {status.get('error')}")
            if status["status"] == "done":
                return self._download(job_id, out_path)
            time.sleep(self.poll_interval)

    def _download(self, job_id: str, out_path: Path) -> SynthResult:
        response = self._request("GET", f"/jobs/{job_id}/audio", timeout=(10, 180), stream=True)
        if not response.ok:
            raise TransientRemoteError(f"Ses indirilemedi (HTTP {response.status_code}).")
        out_path = Path(out_path)
        part = out_path.with_name(out_path.name + ".part")
        size = 0
        try:
            with open(part, "wb") as handle:
                for chunk in response.iter_content(1 << 16):
                    size += len(chunk)
                    handle.write(chunk)
            if size == 0:
                raise TransientRemoteError("Uzak GPU boş ses dosyası döndürdü.")
            part.replace(out_path)
        except requests.RequestException as exc:
            raise TransientRemoteError(f"Ses indirilirken bağlantı koptu: {type(exc).__name__}") from exc
        finally:
            part.unlink(missing_ok=True)
        try:
            duration = float(response.headers.get("X-Kavra-Duration", "0"))
        except ValueError:
            duration = 0.0
        try:
            self._request("DELETE", f"/jobs/{job_id}", timeout=(5, 10))
        except RemoteTTSError:
            pass  # temizlik en iyi çabayla; sunucu zaten süresi dolunca siler
        return SynthResult(duration=duration, words=None)

    def synthesize(self, engine: str, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        request_id = hashlib.sha256(f"{self.run_id}|{engine}|{voice}|{rate}|{text}".encode("utf-8")).hexdigest()[:32]
        failures = 0
        while True:
            try:
                job_id = self._submit(engine, text, voice, rate, request_id)
                return self._await(job_id, out_path)
            except TransientRemoteError as exc:
                failures += 1
                if failures >= MAX_TRANSIENT_FAILURES or self.stop.is_set():
                    raise _unreachable(exc) from exc
                time.sleep(min(2 ** failures, 15))


def _detail(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
        return str(detail)
    except (ValueError, AttributeError):
        return f"HTTP {response.status_code}"


def check_connection(config: RemoteTTSConfig) -> dict:
    """Ayarlar ekranındaki "Bağlantıyı dene" düğmesi için."""
    started = time.monotonic()
    try:
        info = RemoteTTSClient(config).health()
    except TransientRemoteError as exc:
        raise _unreachable(exc) from exc
    info["latencyMs"] = round((time.monotonic() - started) * 1000)
    return info


def synthesize_remote(
    items: list[tuple[str, str, Path]],
    engine: str,
    rate: str,
    concurrency: int,
    *,
    progress_cb=None,
    status_cb=None,
    client: RemoteTTSClient | None = None,
) -> list[SynthResult]:
    """(metin, ses, çıktı_yolu) listesini uzak GPU'da üretir; sonuçlar girdiyle aynı sırada."""
    client = client or RemoteTTSClient(RemoteTTSConfig.load())
    if status_cb:
        status_cb("Uzak GPU'ya bağlanılıyor…")
    try:
        info = client.health()
    except TransientRemoteError as exc:
        raise _unreachable(exc) from exc
    state = (info.get("engines") or {}).get(engine) or {}
    if not state.get("available"):
        raise RemoteTTSError(f"Uzak sunucuda '{engine}' motoru kullanılamıyor: {state.get('error') or 'kurulu değil'}")
    if status_cb:
        gpu = info.get("gpu")
        status_cb(f"Uzak GPU hazır: {gpu}" if gpu else f"Uyarı: uzak sunucuda GPU görünmüyor, {engine} CPU'da yavaş çalışır.")

    results: list[SynthResult | None] = [None] * len(items)
    done = 0
    workers = max(1, min(concurrency, len(items) or 1))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="remote-tts") as pool:
        futures = {
            pool.submit(client.synthesize, engine, text, voice, Path(out_path), rate): index
            for index, (text, voice, out_path) in enumerate(items)
        }
        try:
            for future in as_completed(futures):
                results[futures[future]] = future.result()
                done += 1
                if progress_cb:
                    progress_cb(done, len(items))
        except BaseException:
            client.stop.set()
            for pending in futures:
                pending.cancel()
            raise
    return results  # type: ignore[return-value]
