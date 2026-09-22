"""Kavra uzak TTS sunucusu.

GPU'su olan bir makinede çalışır (Colab, kiralık GPU, kendi bilgisayarın). GPU'suz Kavra
sunucusu ses üretimini bu servise devreder; XTTS v2 ve Piper desteklenir.

Protokol (v1) — tüm uçlar ``Authorization: Bearer <token>`` ister:

  GET    /health              motorların durumu, GPU adı
  HEAD   /assets/{sha256}     ses referansı/model dosyası sunucuda var mı
  PUT    /assets/{sha256}     dosyayı yükle (gövde ham bayt; özeti doğrulanır)
  POST   /jobs                seslendirme işi oluştur -> 202 {id}; eksik dosya varsa 409
  GET    /jobs/{id}           durum: queued | running | done | error
  GET    /jobs/{id}/audio     biten sesi indir
  DELETE /jobs/{id}           işi ve dosyasını sil

Neden tek uzun istek değil de iş kuyruğu? Cloudflare/ngrok tünelleri ~100 sn içinde yanıt
gelmeyen isteği keser; uzun bir XTTS slaytı bundan uzun sürebilir. İş modeliyle her istek
kısadır ve tünel kopması işi düşürmez (aynı ``requestId`` ile yeniden gönderim tekrarı önler).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse

PROTOCOL_VERSION = 1
MAX_ASSET_BYTES = 200 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
JOB_TTL_SECONDS = 30 * 60
SUPPORTED_ENGINES = ("coqui", "piper")

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class EnginePool:
    """Bir motorun N model kopyası; her iş bir kopyayı kilitleyerek kullanır."""

    def __init__(self, name: str, factory: Callable[[], object], size: int = 1):
        self.name = name
        self.size = max(1, size)
        self._factory = factory
        self._free: queue.Queue = queue.Queue()
        self._created = 0
        self._lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=self.size, thread_name_prefix=f"tts-{name}")
        self.load_error: str | None = None

    def _acquire(self):
        try:
            return self._free.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._created < self.size:
                self._created += 1
                try:
                    return self._factory()
                except Exception as exc:
                    self._created -= 1
                    self.load_error = f"{type(exc).__name__}: {exc}"[:400]
                    raise
        return self._free.get()

    def run(self, fn: Callable[[object], object]):
        instance = self._acquire()
        try:
            return fn(instance)
        finally:
            self._free.put(instance)

    def preload(self) -> None:
        """Tüm model kopyalarını baştan yükler: ilk render'da yükleme beklenmez,
        eksik VRAM gibi hatalar sunucu açılırken görünür."""
        instances = [self._acquire() for _ in range(self.size)]
        for instance in instances:
            self._free.put(instance)

    @property
    def loaded(self) -> int:
        return self._created


@dataclass
class Job:
    id: str
    request_id: str
    engine: str
    text: str
    voice_path: str
    rate: str
    path: Path
    status: str = "queued"
    error: str | None = None
    duration: float | None = None
    created: float = 0.0
    finished: float = 0.0

    def public(self) -> dict:
        return {"id": self.id, "status": self.status, "error": self.error, "duration": self.duration}


class AssetStore:
    """İçerik adreslemeli dosya deposu: aynı referans WAV/model bir kez yüklenir."""

    def __init__(self, root: Path):
        self.assets = root / "assets"
        self.voices = root / "voices"
        self.assets.mkdir(parents=True, exist_ok=True)
        self.voices.mkdir(parents=True, exist_ok=True)

    def path(self, sha: str) -> Path:
        return self.assets / sha

    def exists(self, sha: str) -> bool:
        return self.path(sha).is_file()

    def materialize(self, files: dict[str, str], primary: str) -> str:
        """{ad: sha} kümesini tek bir klasörde toplar ve ana dosyanın yolunu döndürür.

        Piper ``voice.onnx`` yanında ``voice.onnx.json`` bekler; bu yüzden dosyalar özgün
        adlarıyla aynı klasöre yerleştirilir."""
        key = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        target = self.voices / key
        if not target.is_dir():
            staging = self.voices / f".{key}.{uuid.uuid4().hex}"
            staging.mkdir()
            try:
                for name, sha in files.items():
                    try:
                        os.link(self.path(sha), staging / name)
                    except OSError:
                        shutil.copyfile(self.path(sha), staging / name)
                try:
                    staging.rename(target)
                except OSError:
                    if not target.is_dir():
                        raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        return str(target / primary)


def _gpu_name() -> str | None:
    try:
        import torch

        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:
        return None


def create_app(*, token: str, data_dir: Path, pools: dict[str, EnginePool],
               engine_errors: dict[str, str] | None = None) -> FastAPI:
    if len(token) < 16:
        # Açık (kimliksiz) bir GPU servisi bir tünelin ucunda kabul edilemez.
        raise ValueError("Token en az 16 karakter olmalı.")
    store = AssetStore(Path(data_dir))
    jobs: dict[str, Job] = {}
    by_request: dict[str, str] = {}
    jobs_lock = threading.Lock()
    engine_errors = engine_errors or {}
    expected = token.encode("utf-8")

    def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, given = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode("utf-8"), expected):
            raise HTTPException(401, "Geçersiz veya eksik token.")

    app = FastAPI(title="Kavra TTS Server", version=str(PROTOCOL_VERSION), dependencies=[Depends(require_token)],
                  docs_url=None, redoc_url=None, openapi_url=None)

    def purge_expired() -> None:
        now = time.time()
        with jobs_lock:
            stale = [j for j in jobs.values() if j.finished and now - j.finished > JOB_TTL_SECONDS]
            for job in stale:
                jobs.pop(job.id, None)
                by_request.pop(job.request_id, None)
        for job in stale:
            shutil.rmtree(job.path.parent, ignore_errors=True)

    @app.get("/health")
    def health():
        engines = {}
        for name in SUPPORTED_ENGINES:
            pool = pools.get(name)
            engines[name] = {
                "available": pool is not None,
                "loaded": pool.loaded if pool else 0,
                "poolSize": pool.size if pool else 0,
                "error": (pool.load_error if pool else engine_errors.get(name)),
            }
        return {"protocol": PROTOCOL_VERSION, "gpu": _gpu_name(), "engines": engines}

    @app.head("/assets/{sha}")
    def head_asset(sha: str):
        if not _SHA_RE.match(sha):
            raise HTTPException(400, "Geçersiz özet.")
        return Response(status_code=200 if store.exists(sha) else 404)

    @app.put("/assets/{sha}")
    async def put_asset(sha: str, request: Request):
        if not _SHA_RE.match(sha):
            raise HTTPException(400, "Geçersiz özet.")
        if store.exists(sha):
            return JSONResponse({"stored": False}, status_code=200)
        tmp = store.assets / f".{sha}.{uuid.uuid4().hex}.part"
        digest, size = hashlib.sha256(), 0
        try:
            with open(tmp, "wb") as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_ASSET_BYTES:
                        raise HTTPException(413, "Dosya çok büyük.")
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0 or digest.hexdigest() != sha:
                raise HTTPException(400, "Yüklenen dosyanın özeti eşleşmiyor.")
            tmp.replace(store.path(sha))
        finally:
            tmp.unlink(missing_ok=True)
        return JSONResponse({"stored": True}, status_code=201)

    def parse_voice(engine: str, voice: dict) -> str:
        kind = voice.get("kind") if isinstance(voice, dict) else None
        if kind == "builtin":
            if engine != "coqui":
                raise HTTPException(400, "Yerleşik ses yalnızca coqui için geçerli.")
            return "builtin:default"
        if kind != "assets":
            raise HTTPException(400, "Geçersiz ses tanımı.")
        files, primary = voice.get("files"), voice.get("primary")
        if (not isinstance(files, dict) or not files or len(files) > 4 or primary not in files):
            raise HTTPException(400, "Ses dosyaları geçersiz.")
        for name, sha in files.items():
            if not _NAME_RE.match(str(name)) or not _SHA_RE.match(str(sha)):
                raise HTTPException(400, "Ses dosyası adı veya özeti geçersiz.")
        missing = sorted({sha for sha in files.values() if not store.exists(sha)})
        if missing:
            raise HTTPException(409, detail={"missing": missing})
        return store.materialize(files, str(primary))

    @app.post("/jobs", status_code=202)
    def create_job(payload: dict):
        purge_expired()
        engine = str(payload.get("engine", ""))
        pool = pools.get(engine)
        if engine not in SUPPORTED_ENGINES or pool is None:
            reason = engine_errors.get(engine) or "desteklenmiyor"
            raise HTTPException(400, f"Bu sunucuda '{engine}' motoru kullanılamıyor: {reason}")
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > MAX_TEXT_CHARS:
            raise HTTPException(400, f"Metin boş olamaz ve {MAX_TEXT_CHARS} karakteri geçemez.")
        request_id = str(payload.get("requestId", ""))
        if not _REQUEST_ID_RE.match(request_id):
            raise HTTPException(400, "requestId geçersiz.")
        voice_path = parse_voice(engine, payload.get("voice") or {})
        with jobs_lock:
            existing = jobs.get(by_request.get(request_id, ""))
            if existing and existing.status != "error":
                return existing.public()
            job_id = uuid.uuid4().hex
            job_dir = store.assets.parent / "jobs" / job_id
            job_dir.mkdir(parents=True)
            job = Job(id=job_id, request_id=request_id, engine=engine, text=text, voice_path=voice_path,
                      rate=str(payload.get("rate", "+0%")), path=job_dir / "audio.mp3", created=time.time())
            jobs[job_id] = job
            by_request[request_id] = job_id

        def run() -> None:
            job.status = "running"
            try:
                result = pool.run(lambda provider: provider.synthesize(job.text, job.voice_path, job.path, job.rate))
                if not job.path.is_file() or job.path.stat().st_size == 0:
                    raise RuntimeError("Motor ses dosyası üretmedi.")
                job.duration = float(result.duration)
                job.text = ""
                job.status = "done"
            except Exception as exc:  # motor hatası istemciye açık bir mesajla döner
                job.error = f"{type(exc).__name__}: {exc}"[:500]
                job.status = "error"
            finally:
                job.finished = time.time()

        pool.executor.submit(run)
        return job.public()

    def get_job(job_id: str) -> Job:
        with jobs_lock:
            job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "İş bulunamadı (sunucu yeniden başlamış veya süresi dolmuş olabilir).")
        return job

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str):
        return get_job(job_id).public()

    @app.get("/jobs/{job_id}/audio")
    def job_audio(job_id: str):
        job = get_job(job_id)
        if job.status != "done":
            raise HTTPException(409, "Ses henüz hazır değil.")
        return FileResponse(job.path, media_type="application/octet-stream",
                            headers={"X-Kavra-Duration": str(job.duration)})

    @app.delete("/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        with jobs_lock:
            job = jobs.pop(job_id, None)
            if job:
                by_request.pop(job.request_id, None)
        if job:
            shutil.rmtree(job.path.parent, ignore_errors=True)
        return Response(status_code=204)

    return app


def build_pools(engines: list[str], coqui_models: int, preload: bool) -> tuple[dict[str, EnginePool], dict[str, str]]:
    pools: dict[str, EnginePool] = {}
    errors: dict[str, str] = {}
    for name in engines:
        if name not in SUPPORTED_ENGINES:
            errors[name] = "desteklenmiyor"
            continue
        try:
            if name == "coqui":
                from app.tts.coqui_provider import CoquiTTSProvider

                pool = EnginePool(name, lambda: CoquiTTSProvider(), coqui_models)
            else:
                from app.tts.piper_provider import PiperTTSProvider

                pool = EnginePool(name, lambda: PiperTTSProvider(), 1)
            if preload:
                pool.preload()
            pools[name] = pool
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"[:400]
    return pools, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Kavra uzak TTS sunucusu")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--engines", default="coqui,piper")
    parser.add_argument("--coqui-models", type=int, default=1, help="Aynı anda yüklü XTTS kopya sayısı (VRAM'e göre)")
    parser.add_argument("--data-dir", default=os.environ.get("KAVRA_TTS_DATA_DIR", "kavra_tts_data"))
    parser.add_argument("--no-preload", action="store_true", help="Modelleri ilk işte yükle")
    args = parser.parse_args()
    token = os.environ.get("KAVRA_TTS_TOKEN", "")
    if not token:
        raise SystemExit("KAVRA_TTS_TOKEN ortam değişkeni gerekli (en az 16 karakter).")

    pools, errors = build_pools([e.strip() for e in args.engines.split(",") if e.strip()],
                                args.coqui_models, preload=not args.no_preload)
    for name, message in errors.items():
        print(f"[uyarı] {name} kullanılamıyor: {message}", flush=True)
    import uvicorn

    uvicorn.run(create_app(token=token, data_dir=Path(args.data_dir), pools=pools, engine_errors=errors),
                host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
