"""Small persistent job registry for the local desktop/web process."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

JOB_RETENTION = 100


def _process_commandline(pid: int) -> str | None:
    """Best-effort komut satırı okuma; başarısız olursa None (asla istisna fırlatmaz)."""
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" -ErrorAction Stop).CommandLine"],
                capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.stdout.strip() or None
        return Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip() or None
    except Exception:
        return None


def _kill_pid_tree(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(pid, signal.SIGKILL)  # worker start_new_session=True ile başladığından pgid == pid
    except Exception:
        pass


def _kill_orphaned_worker(job: dict[str, Any]) -> None:
    """API çökmüş/beklenmeden kapanmışsa, o anki render worker sürecini de kapatır.

    PID'ler sistem yeniden başlayınca yeniden kullanılabildiğinden, öldürmeden önce hedef sürecin
    komut satırının hâlâ beklenen marker'ı içerdiğini doğrular; eşleşmezse (veya okunamazsa)
    dokunmadan geçer — alakasız bir sürece asla dokunmaz."""
    pid, marker = job.get("workerPid"), job.get("workerMarker")
    if not pid or not marker:
        return
    commandline = _process_commandline(pid)
    if commandline and marker in commandline:
        _kill_pid_tree(pid)


class PersistentJobStore:
    """Keep UI-visible job state across API restarts.

    Python threads cannot survive a process exit, but their last state can. Any
    queued/running record found on startup becomes a clear interrupted failure;
    generation can then continue from its source-aware checkpoint.
    """

    def __init__(self, storage_dir: Path):
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_callbacks: dict[str, Callable[[], None]] = {}
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
                if job.get("status") in {"queued", "running", "cancelling"}:
                    _kill_orphaned_worker(job)
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

    def create(
        self,
        kind: str,
        work: Callable[[str], dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> str:
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
                "cancelRequested": False,
                "context": dict(context or {}),
                "updatedAt": time.time(),
            }
            self._persist_locked(job_id)
            self._prune_locked()

        def runner():
            if self.is_cancel_requested(job_id):
                self.update(job_id, status="cancelled", message="İptal edildi")
                return
            self.update(job_id, status="running", message="Başlatılıyor")
            try:
                result = work(job_id)
                if self.is_cancel_requested(job_id):
                    self.update(job_id, status="cancelled", message="İptal edildi")
                else:
                    self.update(
                        job_id,
                        status="complete",
                        progress=100,
                        message="Tamamlandı",
                        result=result,
                    )
            except Exception as exc:
                if self.is_cancel_requested(job_id):
                    self.update(job_id, status="cancelled", message="İptal edildi", error=None)
                else:
                    self.update(job_id, status="failed", message="İşlem başarısız", error=str(exc))
            finally:
                self.clear_cancel_callback(job_id)

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

    def list_active(self, kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            active = [
                dict(job)
                for job in self._jobs.values()
                if job.get("status") in {"queued", "running", "cancelling"}
                and (kind is None or job.get("kind") == kind)
            ]
        return sorted(active, key=lambda job: job.get("updatedAt", 0), reverse=True)

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.get("cancelRequested"))

    def set_cancel_callback(self, job_id: str, callback: Callable[[], None]) -> None:
        call_now = False
        with self._lock:
            if job_id not in self._jobs:
                return
            self._cancel_callbacks[job_id] = callback
            call_now = bool(self._jobs[job_id].get("cancelRequested"))
        if call_now:
            callback()

    def clear_cancel_callback(self, job_id: str) -> None:
        with self._lock:
            self._cancel_callbacks.pop(job_id, None)

    def cancel(self, job_id: str) -> dict[str, Any]:
        callback = None
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.get("status") in {"complete", "failed", "cancelled"}:
                return dict(job)
            job["cancelRequested"] = True
            job["status"] = "cancelling"
            job["message"] = "İptal ediliyor"
            job["updatedAt"] = time.time()
            callback = self._cancel_callbacks.get(job_id)
            self._persist_locked(job_id)
        if callback:
            callback()
        return self.get(job_id)
