"""Toplu/kuyruk modu: birden fazla kaynak dosyasını (yerel yol) sıraya koyup,
her birini sırayla ayrıştır → anlatı üret → render et zincirinden geçirir —
gözetimsiz, gece boyu çalışacak şekilde.

Kasıtlı olarak studio_web.api'nin KENDİ, zaten test edilmiş uç noktalarını
(parse_source_path/generate_script/start_render) düz Python fonksiyonları
olarak doğrudan çağırır — mantığı burada tekrar yazmaz. Bu yüzden bu modül
o uç noktaların davranışını asla değiştirmez, sadece onları otomatik olarak
sırayla tetikler. Dairesel import'tan kaçınmak için studio_web.api'den
içe aktarma bilerek GECİKMELİ (fonksiyon içinde) yapılır — bu proje genelinde
zaten yerleşik bir desendir (ör. app/llm sağlayıcı importları).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

_ACTIVE_STATUSES = {"parsing", "generating", "rendering"}
_JOB_POLL_INTERVAL_SEC = 1.5


class BatchQueueStore:
    def __init__(self, storage_dir: Path):
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._storage_dir = storage_dir
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._worker_thread: threading.Thread | None = None
        self._load()

    def _path(self, item_id: str) -> Path:
        return self._storage_dir / f"{item_id}.json"

    def _persist_locked(self, item_id: str) -> None:
        path = self._path(item_id)
        temp = path.with_suffix(".json.tmp")
        try:
            temp.write_text(
                json.dumps(self._items[item_id], ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            temp.replace(path)
        except OSError:
            temp.unlink(missing_ok=True)

    def _load(self) -> None:
        for path in self._storage_dir.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                item_id = str(item["id"])
                if item.get("status") in _ACTIVE_STATUSES:
                    # Uygulama kapanınca yarıda kesilmiş bir öğe — kaybetmeden
                    # tekrar sıraya al, worker otomatik devam eder.
                    item["status"] = "queued"
                    item["stage"] = "Uygulama kapanınca kesildi, yeniden sıraya alındı"
                self._items[item_id] = item
            except (OSError, ValueError, TypeError, KeyError):
                continue
        if any(i["status"] == "queued" for i in self._items.values()):
            self._ensure_worker()

    def add(self, *, source_path: str, project_name: str, page_mode: bool,
            vision_enrich: bool, vision_api_key: str, llm_settings: dict,
            video_settings: dict) -> str:
        item_id = uuid.uuid4().hex
        item = {
            "id": item_id,
            "sourcePath": source_path,
            "projectName": project_name,
            "status": "queued",
            "stage": "Sırada",
            "progress": 0,
            "error": None,
            "projectId": None,
            "pageMode": page_mode,
            "visionEnrich": vision_enrich,
            "visionApiKey": vision_api_key,
            "llmSettings": llm_settings,
            "videoSettings": video_settings,
            "createdAt": time.time(),
            "updatedAt": time.time(),
        }
        with self._lock:
            self._items[item_id] = item
            self._persist_locked(item_id)
        self._ensure_worker()
        return item_id

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                (dict(i) for i in self._items.values()), key=lambda i: i["createdAt"]
            )

    def remove(self, item_id: str) -> None:
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(item_id)
            if item["status"] in _ACTIVE_STATUSES:
                raise ValueError("İşlenmekte olan bir kuyruk öğesi kaldırılamaz.")
            del self._items[item_id]
            self._path(item_id).unlink(missing_ok=True)

    def _update(self, item_id: str, **changes) -> None:
        with self._lock:
            if item_id not in self._items:
                return
            self._items[item_id].update(changes)
            self._items[item_id]["updatedAt"] = time.time()
            self._persist_locked(item_id)

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._worker_thread = threading.Thread(target=self._run_worker, daemon=True)
            self._worker_thread.start()

    def _next_queued_locked(self) -> dict[str, Any] | None:
        candidates = [i for i in self._items.values() if i["status"] == "queued"]
        if not candidates:
            return None
        return min(candidates, key=lambda i: i["createdAt"])

    def _run_worker(self) -> None:
        while True:
            with self._lock:
                item = self._next_queued_locked()
            if item is None:
                return
            self._process(dict(item))

    def _wait_for_job(self, jobs, job_id: str, item_id: str, stage_label: str) -> dict[str, Any]:
        while True:
            try:
                job = jobs.get(job_id)
            except KeyError:
                raise RuntimeError(f"{stage_label}: iş kaydı bulunamadı.")
            if job["status"] == "complete":
                return job.get("result") or {}
            if job["status"] == "failed":
                raise RuntimeError(job.get("error") or f"{stage_label} başarısız oldu.")
            self._update(
                item_id, progress=int(job.get("progress") or 0),
                stage=f"{stage_label}: {job.get('message', '')}".strip(": "),
            )
            time.sleep(_JOB_POLL_INTERVAL_SEC)

    def _process(self, item: dict[str, Any]) -> None:
        item_id = item["id"]
        # Dairesel import'tan kaçınmak için kasıtlı olarak burada, geç içe aktarılıyor.
        from studio_web.api import generate_script, jobs, parse_source_path, start_render

        try:
            self._update(item_id, status="parsing", stage="Kaynak ayrıştırılıyor", progress=0)
            parsed = parse_source_path({
                "path": item["sourcePath"],
                "pageMode": item["pageMode"],
                "visionEnrich": item["visionEnrich"],
                "visionApiKey": item["visionApiKey"],
            })
            project_id = parsed["id"]
            self._update(item_id, projectId=project_id)
            section_count = len(parsed.get("sections") or [])
            if section_count == 0:
                raise RuntimeError("Kaynakta ayrıştırılacak bölüm bulunamadı.")

            self._update(item_id, status="generating", stage="Anlatı üretiliyor", progress=0)
            gen_payload = {**item["llmSettings"], "sectionIndexes": list(range(section_count))}
            gen_response = generate_script(project_id, gen_payload)
            self._wait_for_job(jobs, gen_response["jobId"], item_id, "Anlatı üretimi")

            self._update(item_id, status="rendering", stage="Video render ediliyor", progress=0)
            render_response = start_render(project_id, item["videoSettings"])
            self._wait_for_job(jobs, render_response["jobId"], item_id, "Render")

            self._update(item_id, status="complete", stage="Tamamlandı", progress=100, error=None)
        except HTTPException as exc:
            self._update(item_id, status="failed", stage="Hata", error=str(exc.detail))
        except Exception as exc:
            self._update(item_id, status="failed", stage="Hata", error=str(exc))
