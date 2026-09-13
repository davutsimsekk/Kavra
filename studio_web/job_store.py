"""Small persistent job registry for the local desktop/web process."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

JOB_RETENTION = 100


class PersistentJobStore:
    """Keep UI-visible job state across API restarts.

    Python threads cannot survive a process exit, but their last state can. Any
    queued/running record found on startup becomes a clear interrupted failure;
    generation can then continue from its source-aware checkpoint.
    """

    def __init__(self, storage_dir: Path):
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._storage_dir = storage_dir
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _path(self, job_id: str) -> Path:
        return self._storage_dir / f"{job_id}.json"

    def _persist_locked(self, job_id: str) -> None:
        path = self._path(job_id)
        temp = path.with_suffix(".json.tmp")
        try:
            temp.write_text(
                json.dumps(self._jobs[job_id], ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            temp.replace(path)
        except OSError:
            temp.unlink(missing_ok=True)

    def _prune_locked(self) -> None:
        """Keep only the most recently updated JOB_RETENTION jobs, on disk and in memory.

        Otherwise studio_web/web_job_status grows by one small JSON file per
        script/render/regenerate/chapters job forever, across every session.
        """
        if len(self._jobs) <= JOB_RETENTION:
            return
        ordered = sorted(self._jobs.items(), key=lambda kv: kv[1].get("updatedAt", 0))
        for job_id, _ in ordered[: len(self._jobs) - JOB_RETENTION]:
            del self._jobs[job_id]
            self._path(job_id).unlink(missing_ok=True)

    def _load(self) -> None:
        for path in self._storage_dir.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                job_id = str(job["id"])
                if job.get("status") in {"queued", "running"}:
                    job.update({
                        "status": "failed",
                        "message": "Uygulama kapanınca iş kesildi",
                        "error": "İş yarıda kesildi. Anlatı üretiminde Kaldığı yerden devam et ile güvenle sürdürebilirsin.",
                        "updatedAt": time.time(),
                    })
                self._jobs[job_id] = job
                self._persist_locked(job_id)
            except (OSError, ValueError, TypeError, KeyError):
                continue
        self._prune_locked()

    def create(self, kind: str, work: Callable[[str], dict[str, Any]]) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "progress": 0,
                "current": 0,
                "total": 0,
                "message": "Sıraya alındı",
                "result": None,
                "error": None,
                "updatedAt": time.time(),
            }
            self._persist_locked(job_id)
            self._prune_locked()

        def runner():
            self.update(job_id, status="running", message="Başlatılıyor")
            try:
                result = work(job_id)
                self.update(
                    job_id,
                    status="complete",
                    progress=100,
                    message="Tamamlandı",
                    result=result,
                )
            except Exception as exc:
                self.update(job_id, status="failed", message="İşlem başarısız", error=str(exc))

        threading.Thread(target=runner, daemon=True).start()
        return job_id

    def update(self, job_id: str, **changes) -> None:
        with self._lock:
            if job_id not in self._jobs:
                return
            self._jobs[job_id].update(changes)
            self._jobs[job_id]["updatedAt"] = time.time()
            self._persist_locked(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return dict(job)
