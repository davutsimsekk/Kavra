"""Chatterbox için GPU process paralelliği.

Chatterbox farklı bir transformers sürümü kullandığından her worker,
``chatterbox_venv`` Python'ı ile başlatılır. Worker'lar model yükleme ve her
slaytın başlangıç/bitiş durumunu ana sürece canlı olarak bildirir. RTX 4060
Laptop'ın 8 GB VRAM'i için iki model güvenli üst sınırdır; CUDA/native worker
hatasında tek modele geri dönülür.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import uuid
from pathlib import Path

from app.config import CACHE_DIR, ROOT
from app.tts.base import local_engine_unavailable
from app.models import SynthResult


MAX_PARALLEL_WORKERS = 2
_EVENT_PREFIX = "__CHATTERBOX_WORKER__"


def _is_oom_text(text: str | None) -> bool:
    text = (text or "").lower()
    return "out of memory" in text or "cuda oom" in text or "cuda error" in text


def _distribute(items: list[tuple[str, str, str]], n_workers: int) -> list[list[tuple[int, str, str, str]]]:
    groups: list[list[tuple[int, str, str, str]]] = [[] for _ in range(n_workers)]
    for index, (text, voice, out_path) in enumerate(items):
        groups[index % n_workers].append((index, text, voice, out_path))
    return groups


def _chatterbox_python() -> Path:
    configured = os.environ.get("CHATTERBOX_PYTHON", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate
        raise RuntimeError(local_engine_unavailable(
            "Chatterbox", f"CHATTERBOX_PYTHON bulunamadı: {candidate}", remote_ok=False))
    candidate = ROOT / "chatterbox_venv" / "Scripts" / "python.exe"
    if not candidate.exists():
        candidate = ROOT / "chatterbox_venv" / "bin" / "python"
    if not candidate.exists():
        raise RuntimeError(local_engine_unavailable(
            "Chatterbox",
            "Chatterbox ortamı bulunamadı. venv\\Scripts\\python.exe install_chatterbox.py çalıştırılmalı.",
            remote_ok=False))
    return candidate


def _read_worker_output(worker_number: int, process: subprocess.Popen, events: queue.Queue) -> None:
    try:
        assert process.stdout is not None
        for line in process.stdout:
            events.put(("line", worker_number, line.rstrip()))
    finally:
        events.put(("closed", worker_number, None))


def _run_workers(
    groups: list[list[tuple[int, str, str, str]]],
    progress_cb=None,
    status_cb=None,
) -> dict[int, tuple[bool, bool, str | None]]:
    job_dir = CACHE_DIR / "chatterbox_parallel"
    job_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    environment = os.environ.copy()
    # Yerel kurulumda model önceden indirildiği için varsayılan offline kalır;
    # temiz Docker volume'unda ilk model indirmesine izin vermek için Compose bu
    # değeri 0 yapar. İndirme tamamlandıktan sonra 1'e alınabilir.
    if os.environ.get("CHATTERBOX_HF_HUB_OFFLINE", "1").strip().lower() not in {"0", "false", "no"}:
        environment["HF_HUB_OFFLINE"] = "1"
    else:
        environment.pop("HF_HUB_OFFLINE", None)
    environment["PYTHONPATH"] = str(ROOT) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    workers: list[tuple[int, subprocess.Popen, Path, Path, list[tuple[int, str, str, str]]]] = []
    events: queue.Queue = queue.Queue()

    try:
        non_empty = [group for group in groups if group]
        for worker_number, group in enumerate(non_empty, start=1):
            spec_path = job_dir / f"{token}-{worker_number}.json"
            result_path = job_dir / f"{token}-{worker_number}.result.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "workerNumber": worker_number,
                        "items": [
                            {"index": index, "text": text, "voice": voice, "outPath": out_path}
                            for index, text, voice, out_path in group
                        ],
                        "resultPath": str(result_path),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            process = subprocess.Popen(
                [str(_chatterbox_python()), "-m", "app.tts.chatterbox_parallel_worker", str(spec_path)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                creationflags=creation_flags,
            )
            workers.append((worker_number, process, spec_path, result_path, group))
            threading.Thread(
                target=_read_worker_output,
                args=(worker_number, process, events),
                daemon=True,
            ).start()

        outcomes: dict[int, tuple[bool, bool, str | None]] = {}
        diagnostics: dict[int, list[str]] = {number: [] for number, *_rest in workers}
        total = sum(len(group) for group in non_empty)
        completed = 0
        ready_workers: set[int] = set()
        closed_workers: set[int] = set()
        if status_cb:
            status_cb(f"Chatterbox {len(workers)}× modelleri yükleniyor · 0/{len(workers)} hazır")

        while completed < total:
            try:
                kind, worker_number, raw = events.get(timeout=2)
            except queue.Empty:
                if any(process.poll() is None for _n, process, *_rest in workers):
                    continue
                break

            if kind == "closed":
                closed_workers.add(worker_number)
                record = next(record for record in workers if record[0] == worker_number)
                _number, process, _spec_path, result_path, group = record
                # Normalde sonuçlar stdout olaylarıyla gelir. Pipe beklenmedik
                # biçimde kapanırsa atomik sonuç dosyası son emniyet ağıdır.
                if result_path.exists():
                    try:
                        result_items = json.loads(result_path.read_text(encoding="utf-8"))["results"]
                    except (OSError, ValueError, KeyError, TypeError):
                        result_items = []
                    for item in result_items:
                        index = int(item["index"])
                        if index in outcomes:
                            continue
                        ok = bool(item["ok"])
                        outcomes[index] = (ok, bool(item["oom"]), item.get("detail"))
                        completed += 1
                        if ok and progress_cb:
                            successful = sum(1 for result in outcomes.values() if result[0])
                            progress_cb(successful, total)
                missing = [index for index, *_rest in group if index not in outcomes]
                if missing:
                    detail = "Chatterbox worker sonuç üretmeden kapandı"
                    if process.returncode is not None:
                        detail += f" (worker {worker_number}, exit {process.returncode})"
                    if diagnostics[worker_number]:
                        detail += ": " + " | ".join(diagnostics[worker_number][-3:])[-900:]
                    if status_cb:
                        status_cb(
                            f"Chatterbox worker {worker_number} kapandı · "
                            "diğer worker tamamlanınca eksik slaytlar 1× sürdürülecek"
                        )
                    for index in missing:
                        outcomes[index] = (False, True, detail)
                        completed += 1
                if len(closed_workers) == len(workers) and completed < total:
                    break
                continue

            marker_index = raw.find(_EVENT_PREFIX)
            if marker_index < 0:
                if raw:
                    diagnostics[worker_number].append(raw[-500:])
                    diagnostics[worker_number] = diagnostics[worker_number][-8:]
                continue
            prefix = raw[:marker_index].strip()
            if prefix:
                diagnostics[worker_number].append(prefix[-500:])
                diagnostics[worker_number] = diagnostics[worker_number][-8:]
            try:
                event = json.loads(raw[marker_index + len(_EVENT_PREFIX):])
            except (ValueError, TypeError):
                diagnostics[worker_number].append(raw[-500:])
                continue

            if event.get("type") == "status":
                state = event.get("state")
                if state == "ready":
                    ready_workers.add(worker_number)
                    if status_cb:
                        status_cb(
                            f"Chatterbox {len(workers)}× modelleri yükleniyor · "
                            f"{len(ready_workers)}/{len(workers)} hazır"
                        )
                elif state == "started" and status_cb:
                    status_cb(
                        f"Chatterbox {len(workers)}× seslendiriliyor · "
                        f"slayt {int(event['index']) + 1}/{total} başladı"
                    )
                continue

            if event.get("type") == "result":
                index = int(event["index"])
                if index in outcomes:
                    continue
                ok = bool(event["ok"])
                outcomes[index] = (ok, bool(event["oom"]), event.get("detail"))
                completed += 1
                if ok and progress_cb:
                    successful = sum(1 for result in outcomes.values() if result[0])
                    progress_cb(successful, total)

        for _number, process, _spec_path, _result_path, group in workers:
            if process.poll() is None:
                continue
            for index, *_rest in group:
                if index not in outcomes:
                    outcomes[index] = (False, True, f"Chatterbox worker exit {process.returncode} ile kapandı.")
        return outcomes
    finally:
        for _number, process, spec_path, result_path, _group in workers:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            spec_path.unlink(missing_ok=True)
            result_path.unlink(missing_ok=True)


def synthesize_parallel(
    items: list[tuple[str, str, Path]],
    n_workers: int,
    _run_workers_fn=None,
    progress_cb=None,
    status_cb=None,
) -> list[SynthResult]:
    """Chatterbox seslerini bağımsız GPU worker'larına bölerek üretir."""
    if not items:
        return []
    n_workers = min(max(n_workers, 1), MAX_PARALLEL_WORKERS, len(items))
    groups = _distribute([(text, voice, str(out_path)) for text, voice, out_path in items], n_workers)
    if _run_workers_fn is None:
        outcomes = _run_workers(groups, progress_cb, status_cb)
    else:
        outcomes = _run_workers_fn(groups, progress_cb)
    failures = [(index, recoverable, detail) for index, (ok, recoverable, detail) in outcomes.items() if not ok]
    if not failures:
        return [SynthResult(duration=0.0, words=None) for _ in items]
    if failures and all(recoverable for _index, recoverable, _detail in failures) and n_workers > 1:
        failed_indexes = sorted(index for index, recoverable, _detail in failures if recoverable)
        retry_items = [items[index] for index in failed_indexes]
        successful_count = len(items) - len(retry_items)
        if status_cb:
            status_cb(
                f"Chatterbox 2× kararsız kaldı · yalnızca eksik "
                f"{len(retry_items)} slayt 1× ile sürdürülüyor"
            )

        def retry_progress(done: int, _retry_total: int) -> None:
            if progress_cb:
                progress_cb(successful_count + done, len(items))

        synthesize_parallel(retry_items, 1, _run_workers_fn, retry_progress, status_cb)
        return [SynthResult(duration=0.0, words=None) for _ in items]
    _index, _recoverable, detail = failures[0]
    raise RuntimeError(detail or "Chatterbox ses üretimi başarısız oldu.")
