"""Coqui XTTS için BAĞIMSIZ PROCESS paralelliği.

Ölçülen sonuçlar (RTX 4060 Laptop, 8GB VRAM, gerçek ders slaytlarıyla — kısa
sentetik cümleler değil): tek model sıralı üretime karşı 2 bağımsız model
paralel ~1.85x, 3 bağımsız model paralel ~2.74x hızlandırıyor. Her process
kendi modelini yükler (~2.1GB VRAM/process) ve kendine ayrılan slaytları
SIRADAN (tensor-batching YOK — gerçek anlatım uzunluklarında ölçülebilir bir
fayda sağlamadığı için kaldırıldı, bkz. coqui_provider.py) üretir.

GÜVENLİK NOTU — bu modülün en önemli kısmı: bu depoda 4 process'i aynı anda
VRAM'e yüklemeye çalışmak GERÇEK BİR WINDOWS MAVİ EKRANINA (BSOD) yol açtı.
Bu yüzden:
  1. İzin verilen worker sayısı sabit bir üst sınırla (MAX_PARALLEL_WORKERS)
     kısıtlı — kullanıcı arayüzü de bunu aşamaz.
  2. Bir grup CUDA belleğine sığmazsa (yakalanabilir bir OutOfMemoryError),
     worker sayısı otomatik olarak YARIYA iner ve yeniden denenir, en kötü
     durumda tek process'e (her zaman güvenli olduğu kanıtlanmış, orijinal
     yöntem) düşülür.
  3. Bu geri çekilme yalnızca PYTHON SEVİYESİNDE yakalanabilen hatalar
     içindir — gerçek bir sürücü çökmesi (BSOD gibi) Python'dan yakalanamaz.
     Bu yüzden (1) maddesi asıl güvenlik önlemidir; (2) yalnızca sınıra yakın
     ama henüz aşmamış durumlar için ek bir rahatlık katmanıdır.
"""

from __future__ import annotations

import queue
from pathlib import Path

from app.models import SynthResult

# Bu depoda 3 process (~6.3GB VRAM) güvenle çalıştı, 4 process (~8GB+) sistemi
# çökertti (BSOD). Makineden makineye VRAM farklı olacağından bu ihtiyatlı bir
# tavan — kullanıcı arayüzü de bu değerin üzerini seçtirmez.
MAX_PARALLEL_WORKERS = 3


def _is_oom_text(text: str) -> bool:
    text = (text or "").lower()
    return "out of memory" in text or "cuda oom" in text or "cuda error" in text


def _distribute(items: list[tuple[str, str, str]], n_workers: int) -> list[list[tuple[int, str, str, str]]]:
    """items'ı n_workers grubuna sırayla (round-robin) böler; her öğeye
    sonuçları doğru sıraya geri koyabilmek için orijinal indeksini iliştirir."""
    groups: list[list[tuple[int, str, str, str]]] = [[] for _ in range(n_workers)]
    for i, (text, voice, out_path) in enumerate(items):
        groups[i % n_workers].append((i, text, voice, out_path))
    return groups


def _worker_entry(
    worker_index: int,
    worker_items: list[tuple[int, str, str, str]],
    result_queue,
) -> None:
    """Bir alt process içinde çalışır ve durum/sonuç olaylarını kuyruğa yazar."""
    from app.tts.coqui_provider import CoquiTTSProvider

    result_queue.put(("status", "loading", worker_index, None))
    try:
        provider = CoquiTTSProvider(gpu=True)
    except Exception as exc:
        for idx, *_rest in worker_items:
            result_queue.put(("result", idx, False, _is_oom_text(str(exc)), str(exc)))
        return
    result_queue.put(("status", "ready", worker_index, None))

    aborted = False
    for idx, text, voice, out_path_str in worker_items:
        if aborted:
            result_queue.put(("result", idx, False, True, "Bu process önceki bir CUDA hatası nedeniyle durdu."))
            continue
        result_queue.put(("status", "started", worker_index, idx))
        try:
            provider.synthesize(text, voice, Path(out_path_str))
            result_queue.put(("result", idx, True, False, None))
        except Exception as exc:
            is_oom = _is_oom_text(str(exc))
            result_queue.put(("result", idx, False, is_oom, str(exc)))
            if is_oom:
                # Bu process'in CUDA durumu bozulmuş olabilir; kalan öğeleri
                # zorlamak yerine üst katmanın daha az worker'la yeniden
                # denemesine bırak.
                aborted = True


def _run_workers(
    groups: list[list[tuple[int, str, str, str]]],
    progress_cb=None,
    status_cb=None,
) -> dict[int, tuple[bool, bool, str | None]]:
    """Verilen grupları gerçek ayrı process'lerde çalıştırır, sonuçları toplar.
    Test edilebilirlik için synthesize_parallel'dan ayrı bir fonksiyon —
    testler bunun yerine sahte (gerçek process açmayan) bir sürüm geçirebilir.

    progress_cb(tamamlanan, toplam): hangi worker'dan geldiğine bakmaksızın,
    her öğe sonucu kuyruktan alındıkça çağrılır — böylece uzun bir render'da
    kullanıcı arayüzü, tüm parti bitene kadar değil, öğe öğe ilerleme görür.
    """
    import multiprocessing as mp

    result_queue: mp.Queue = mp.Queue()
    non_empty = [g for g in groups if g]
    procs = [
        mp.Process(target=_worker_entry, args=(worker_index, group, result_queue))
        for worker_index, group in enumerate(non_empty, start=1)
    ]
    for p in procs:
        p.start()

    outcomes: dict[int, tuple[bool, bool, str | None]] = {}
    total_items = sum(len(g) for g in non_empty)
    completed = 0
    ready_workers = 0
    while completed < total_items:
        try:
            event = result_queue.get(timeout=2)
        except queue.Empty:
            crashed = [p for p in procs if p.exitcode not in (None, 0)]
            all_finished = all(p.exitcode is not None for p in procs)
            if not crashed and not all_finished:
                continue
            exit_summary = ", ".join(f"PID {p.pid}: exit {p.exitcode}" for p in crashed)
            detail = "XTTS worker sonuç üretmeden kapandı" + (f" ({exit_summary})" if exit_summary else ".")
            for group in non_empty:
                for idx, *_rest in group:
                    outcomes.setdefault(idx, (False, True, detail))
            for p in procs:
                if p.is_alive():
                    p.terminate()
            break

        if event[0] == "status":
            _kind, state, worker_index, item_index = event
            if state == "loading" and status_cb:
                status_cb(f"XTTS {len(procs)}× modelleri yükleniyor · {ready_workers}/{len(procs)} hazır")
            elif state == "ready":
                ready_workers += 1
                if status_cb:
                    status_cb(f"XTTS {len(procs)}× modelleri yükleniyor · {ready_workers}/{len(procs)} hazır")
            elif state == "started" and status_cb:
                status_cb(f"XTTS {len(procs)}× seslendiriliyor · slayt {item_index + 1}/{total_items} başladı")
            continue

        _kind, idx, ok, is_oom, detail = event
        outcomes[idx] = (ok, is_oom, detail)
        completed += 1
        if ok and progress_cb:
            successful = sum(1 for result in outcomes.values() if result[0])
            progress_cb(successful, total_items)
    for p in procs:
        p.join(timeout=5)
    return outcomes


def synthesize_parallel(
    items: list[tuple[str, str, Path]],
    n_workers: int,
    _run_workers_fn=None,
    progress_cb=None,
    status_cb=None,
) -> list[SynthResult]:
    """items: (text, voice, out_path). Tüm öğeler aynı sesi kullanmalı (bir
    render işi zaten tek bir ses kullanır). n_workers <= 1 ise (ya da tek
    öğe varsa) her zaman güvenli olan sıralı/tekil yönteme düşer.

    Bir grup CUDA belleğine sığmazsa otomatik olarak daha az worker'la
    (yarıya inerek) yeniden dener; OOM DIŞI bir hata (ör. ileride bir
    coqui-tts sürüm değişikliği bu iç yapıyı bozarsa) render'ı sessizce
    yutmaz, açıkça hata fırlatır — bu, "sessizce yanlış ses üretme" riskini
    "render açıkça başarısız olur, kullanıcı ne olduğunu görür" ile değiştirir.

    progress_cb(tamamlanan, toplam) verilirse, her ses öğesi bitişinde
    çağrılır (hem tek process'lik düşük seviye hem de çok worker'lı yolda) —
    bir OOM sonrası daha az worker'la yeniden denenirse bu sayaç o partide
    baştan başlar (parti tekrar üretildiği için bu doğru davranıştır).
    """
    n_workers = min(max(n_workers, 1), MAX_PARALLEL_WORKERS)
    if n_workers <= 1 or len(items) <= 1:
        from app.tts.coqui_provider import CoquiTTSProvider

        if status_cb:
            status_cb("XTTS modeli yükleniyor")
        provider = CoquiTTSProvider(gpu=True)
        if status_cb:
            status_cb("XTTS modeli hazır · seslendirme başlıyor")
        results = []
        for i, (text, voice, out_path) in enumerate(items, start=1):
            if status_cb:
                status_cb(f"XTTS seslendiriliyor · slayt {i}/{len(items)} başladı")
            results.append(provider.synthesize(text, voice, out_path))
            if progress_cb:
                progress_cb(i, len(items))
        return results

    n_workers = min(n_workers, len(items))
    string_items = [(text, voice, str(out_path)) for text, voice, out_path in items]
    groups = _distribute(string_items, n_workers)

    if _run_workers_fn is None:
        outcomes = _run_workers(groups, progress_cb, status_cb)
    else:
        outcomes = _run_workers_fn(groups, progress_cb)

    failures = [(i, is_oom, detail) for i, (ok, is_oom, detail) in outcomes.items() if not ok]
    if not failures:
        return [SynthResult(duration=0.0, words=None) for _ in items]

    if failures and all(is_oom for _, is_oom, _ in failures) and n_workers > 1:
        failed_indexes = sorted(index for index, _is_oom, _detail in failures)
        retry_items = [items[index] for index in failed_indexes]
        successful_count = len(items) - len(retry_items)
        retry_n = min(max(n_workers - 1, 1), len(retry_items))
        if status_cb:
            status_cb(
                f"XTTS {n_workers}× kararsız kaldı · yalnızca eksik "
                f"{len(retry_items)} slayt {retry_n}× ile sürdürülüyor"
            )

        def retry_progress(done: int, _retry_total: int) -> None:
            if progress_cb:
                progress_cb(successful_count + done, len(items))

        synthesize_parallel(retry_items, retry_n, _run_workers_fn, retry_progress, status_cb)
        return [SynthResult(duration=0.0, words=None) for _ in items]

    _, _, detail = failures[0]
    raise RuntimeError(detail or "Coqui paralel üretim beklenmedik şekilde başarısız oldu.")
