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
  2. İsteğe bağlı tekrar koruması açıksa, bir grup CUDA belleğine sığmadığında
     (yakalanabilir bir OutOfMemoryError) worker sayısı azalır ve yeniden
     denenir. Varsayılan eski/hızlı mod hata durumunda açıkça durur.
  3. Bu geri çekilme yalnızca PYTHON SEVİYESİNDE yakalanabilen hatalar
     içindir — gerçek bir sürücü çökmesi (BSOD gibi) Python'dan yakalanamaz.
     Bu yüzden (1) maddesi asıl güvenlik önlemidir; (2) yalnızca sınıra yakın
     ama henüz aşmamış durumlar için ek bir rahatlık katmanıdır.
"""

from __future__ import annotations

import os
import queue
from pathlib import Path

from app.models import SynthResult

# Bu depoda 3 process (~6.3GB VRAM) güvenle çalıştı, 4 process (~8GB+) sistemi
# çökertti (BSOD). Makineden makineye VRAM farklı olacağından bu ihtiyatlı bir
# tavan — kullanıcı arayüzü de bu değerin üzerini seçtirmez. Bellek miktarı bilinen
# bulut GPU'larında (Kaggle 2x T4 gibi) KAVRA_COQUI_MAX_WORKERS ile yükseltilebilir;
# tools/colab_studio.py bunu donanıma göre hesaplayıp verir.
DEFAULT_MAX_PARALLEL_WORKERS = 3
HARD_MAX_PARALLEL_WORKERS = 16


def _max_workers_from_env(env: dict[str, str] | None = None) -> int:
    raw = ((os.environ if env is None else env).get("KAVRA_COQUI_MAX_WORKERS") or "").strip()
    if raw.isdigit() and int(raw) >= 1:
        return min(int(raw), HARD_MAX_PARALLEL_WORKERS)
    return DEFAULT_MAX_PARALLEL_WORKERS


MAX_PARALLEL_WORKERS = _max_workers_from_env()


def _select_cuda_device(worker_index: int) -> int | None:
    """Birden çok GPU varsa worker'ları kartlara sırayla dağıtır (1→GPU0, 2→GPU1, 3→GPU0...).

    Coqui modeli ``.to("cuda")`` ile o anki varsayılan karta taşıdığından, model
    yüklenmeden önce ``set_device`` çağırmak yeterlidir."""
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    device = (worker_index - 1) % max(torch.cuda.device_count(), 1)
    torch.cuda.set_device(device)
    return device


# Ana döngünün olay beklerken uyanma aralığı (saniye) — testler küçültür.
_POLL_SECONDS = 2
# Bir model yüklemesi bu süreyi aşarsa (ör. takılmış bir checkpoint okuması) o worker'ın
# yüklenemediği kabul edilir; aksi halde bariyer hiç açılmaz ve render sonsuza dek bekler.
LOAD_TIMEOUT_SECONDS = 20 * 60
# Hiçbir worker'dan (hazır/başladı/sonuç) bu kadar süre TEK bir olay gelmezse üretim
# takılmış sayılır ve açık bir hatayla durdurulur.
STALL_TIMEOUT_SECONDS = 30 * 60


def _is_oom_text(text: str) -> bool:
    text = (text or "").lower()
    return (
        "out of memory" in text
        or "cuda oom" in text
        or "cuda error" in text
        # Windows'ta RAM/sayfa dosyası yetmediğinde model yükleme bu metinlerle düşer.
        or "not enough memory" in text
        or "paging file" in text
        or "memoryerror" in text
        or "defaultcpuallocator" in text
    )


def _distribute(items: list[tuple[str, str, str]], n_workers: int) -> list[list[tuple[int, str, str, str]]]:
    """items'ı n_workers grubuna sırayla (round-robin) böler; her öğeye
    sonuçları doğru sıraya geri koyabilmek için orijinal indeksini iliştirir."""
    groups: list[list[tuple[int, str, str, str]]] = [[] for _ in range(n_workers)]
    for i, (text, voice, out_path) in enumerate(items):
        groups[i % n_workers].append((i, text, voice, out_path))
    return groups


def _wait_for_start(start_event) -> bool:
    """Bariyerin açılmasını bekler. Ana süreç (ör. API'nin render worker'ı) ölür ya da
    öldürülürse False döner — aksi halde bu process VRAM'i tutarak öksüz kalırdı."""
    import multiprocessing

    parent = multiprocessing.parent_process()
    while not start_event.wait(timeout=5):
        if parent is not None and not parent.is_alive():
            return False
    return True


def _worker_entry(
    worker_index: int,
    worker_items: list[tuple[int, str, str, str]],
    result_queue,
    retry_incomplete: bool = False,
    start_event=None,
) -> None:
    """Bir alt process içinde çalışır ve durum/sonuç olaylarını kuyruğa yazar.

    Olay protokolü: ("status", "loading"|"ready"|"started"|"load_failed", worker, ek)
    ve ("result", indeks, ok, oom, detay). Model yüklenemezse "load_failed" gönderilir —
    ana döngü bunu görmezse bariyeri (start_event) hiç açamaz ve diğer worker'lar sonsuza
    dek beklerdi."""
    from app.tts.coqui_provider import CoquiTTSProvider

    result_queue.put(("status", "loading", worker_index, None))
    try:
        _select_cuda_device(worker_index)
        provider = CoquiTTSProvider(gpu=True, retry_incomplete=retry_incomplete)
    except Exception as exc:
        result_queue.put(("status", "load_failed", worker_index, f"{type(exc).__name__}: {exc}"))
        return
    result_queue.put(("status", "ready", worker_index, None))
    # Modeller sırayla yüklenir fakat tüm worker'lar hazır olana kadar üretime
    # başlamaz. Böylece üç model gerçekten paralel seslendirirken eşzamanlı
    # checkpoint/CUDA yüklemesinin Windows'taki yüksek geçici bellek tepesi
    # ve native 0xC0000005 çökmesi önlenir.
    if start_event is not None and not _wait_for_start(start_event):
        return

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
    retry_incomplete: bool = False,
    _ctx=None,
    _worker=None,
) -> dict[int, tuple[bool, bool, str | None]]:
    """Verilen grupları gerçek ayrı process'lerde çalıştırır, sonuçları toplar.
    Test edilebilirlik için synthesize_parallel'dan ayrı bir fonksiyon —
    testler bunun yerine sahte (gerçek process açmayan) bir sürüm geçirebilir; `_ctx`
    (Process/Queue/Event sağlayan bir multiprocessing benzeri) ve `_worker` ile de
    gerçek process açmadan bu orkestrasyonun kendisi test edilebilir.

    progress_cb(tamamlanan, toplam): hangi worker'dan geldiğine bakmaksızın,
    her öğe sonucu kuyruktan alındıkça çağrılır — böylece uzun bir render'da
    kullanıcı arayüzü, tüm parti bitene kadar değil, öğe öğe ilerleme görür.

    DEĞİŞMEZ: her worker EN GEÇ şu durumlardan birine ulaşır ve ana döngü hepsini görür —
    "hazır", "yüklenemedi", sürecin ölmesi ya da zaman aşımı. Böylece bariyer (start_event)
    hiçbir koşulda sonsuza dek kapalı kalmaz (eskiden bir worker modeli yükleyemeyip sessizce
    çıkınca hazır olan worker'lar ve ana döngü sonsuza dek beklerdi: "0/N'de takılma").
    Yükleme başarısızlığında varsayılan (retry_incomplete=False) mod hemen açık bir hatayla
    durur; tekrar koruması açıksa hazır olan worker'lar kendi öğelerini bitirir ve eksik
    kalanlar üst katmanda daha az worker'la yeniden denenir.
    """
    import multiprocessing as mp
    import time

    ctx = _ctx or mp
    worker_target = _worker or _worker_entry
    result_queue = ctx.Queue()
    start_event = ctx.Event()
    non_empty = [g for g in groups if g]
    worker_total = len(non_empty)
    worker_groups = {number: group for number, group in enumerate(non_empty, start=1)}
    total_items = sum(len(g) for g in non_empty)
    procs: dict[int, object] = {}
    ready: set[int] = set()
    outcomes: dict[int, tuple[bool, bool, str | None]] = {}
    state = {"loading_stopped": False, "loading_started_at": time.monotonic()}

    def start_next_worker() -> None:
        number = len(procs) + 1
        process = ctx.Process(
            target=worker_target,
            args=(number, worker_groups[number], result_queue, retry_incomplete, start_event),
        )
        process.start()
        procs[number] = process
        state["loading_started_at"] = time.monotonic()

    def fail_items(numbers, detail: str, recoverable: bool) -> None:
        for number in numbers:
            for idx, *_rest in worker_groups[number]:
                outcomes.setdefault(idx, (False, recoverable, detail))

    def terminate_all() -> None:
        for process in procs.values():
            if process.is_alive():
                process.terminate()
        start_event.set()

    def stop_loading(detail: str, recoverable: bool) -> None:
        """Bir worker yüklenemedi/öldü: artık yeni worker başlatılmaz ve bariyer açılır."""
        if state["loading_stopped"]:
            return
        state["loading_stopped"] = True
        # Hiç başlatılamamış worker'ların öğeleri de eksik kalır (kök nedeni taşırlar).
        fail_items(
            [n for n in worker_groups if n not in procs],
            f"{detail} — bu worker hiç başlatılmadı",
            recoverable,
        )
        if retry_incomplete:
            # Zaten hazır olanlar kendi öğelerini bitirir; eksikler üst katmanda daha az
            # worker'la yeniden denenir.
            start_event.set()
        else:
            # Varsayılan (hızlı/eski) mod: hata durumunda açıkça durur. Yarım kalan işi
            # sürdürmenin anlamı yok — biten sesler zaten slayt başına önbellekte kalır.
            for number in procs:
                fail_items([number], f"{detail} — üretim durduruldu", recoverable)
            terminate_all()

    def report_loading() -> None:
        if status_cb:
            status_cb(f"XTTS {worker_total}× modelleri sırayla yükleniyor · {len(ready)}/{worker_total} hazır")

    start_next_worker()
    last_event_at = time.monotonic()
    dead_polls = 0
    while len(outcomes) < total_items:
        try:
            event = result_queue.get(timeout=_POLL_SECONDS)
        except queue.Empty:
            now = time.monotonic()
            # 1) Sonuç üretmeden ölen worker'lar. Süreç kapanmadan hemen önce yazılan son
            #    olaylar kuyruğa geç düşebilir; bu yüzden ARKA ARKAYA iki boş turda karar verilir.
            dead = [
                n for n, p in procs.items()
                if p.exitcode is not None and any(idx not in outcomes for idx, *_r in worker_groups[n])
            ]
            if dead:
                dead_polls += 1
                if dead_polls >= 2:
                    dead_polls = 0
                    summary = ", ".join(f"worker {n}: exit {procs[n].exitcode}" for n in dead)
                    detail = f"XTTS worker sonuç üretmeden kapandı ({summary})"
                    fail_items(dead, detail, True)
                    if not start_event.is_set():
                        stop_loading(detail, True)
                continue
            dead_polls = 0
            # 2) Takılmış model yüklemesi.
            loading = len(procs)
            if (
                not state["loading_stopped"] and not start_event.is_set()
                and loading not in ready and now - state["loading_started_at"] > LOAD_TIMEOUT_SECONDS
            ):
                detail = f"XTTS worker {loading} modeli {LOAD_TIMEOUT_SECONDS // 60} dakikada yükleyemedi (zaman aşımı)"
                fail_items([loading], detail, True)
                if procs[loading].is_alive():
                    procs[loading].terminate()
                stop_loading(detail, True)
                continue
            # 3) Hiçbir ilerleme yok.
            if now - last_event_at > STALL_TIMEOUT_SECONDS:
                detail = f"XTTS üretimi {STALL_TIMEOUT_SECONDS // 60} dakikadır hiç ilerlemedi (zaman aşımı)"
                fail_items(list(worker_groups), detail, True)
                terminate_all()
            continue

        last_event_at = time.monotonic()
        dead_polls = 0
        if event[0] == "status":
            _kind, kind_state, number, extra = event
            if kind_state == "loading":
                report_loading()
            elif kind_state == "ready":
                ready.add(number)
                report_loading()
                if not state["loading_stopped"]:
                    if len(procs) < worker_total:
                        start_next_worker()
                    elif len(ready) == worker_total:
                        start_event.set()
            elif kind_state == "load_failed":
                detail = f"XTTS worker {number} modeli yükleyemedi: {extra}"
                recoverable = number > 1 or _is_oom_text(extra)
                fail_items([number], detail, recoverable)
                stop_loading(detail, recoverable)
            elif kind_state == "started" and status_cb:
                status_cb(f"XTTS {worker_total}× seslendiriliyor · slayt {extra + 1}/{total_items} başladı")
            continue

        _kind, idx, ok, is_oom, detail = event
        if idx in outcomes:
            continue
        outcomes[idx] = (ok, is_oom, detail)
        if ok and progress_cb:
            successful = sum(1 for result in outcomes.values() if result[0])
            progress_cb(successful, total_items)

    for process in procs.values():
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
    return outcomes


def synthesize_parallel(
    items: list[tuple[str, str, Path]],
    n_workers: int,
    _run_workers_fn=None,
    progress_cb=None,
    status_cb=None,
    retry_incomplete: bool = False,
) -> list[SynthResult]:
    """items: (text, voice, out_path). Tüm öğeler aynı sesi kullanmalı (bir
    render işi zaten tek bir ses kullanır). n_workers <= 1 ise (ya da tek
    öğe varsa) her zaman güvenli olan sıralı/tekil yönteme düşer.

    ``retry_incomplete=True`` seçilirse CUDA belleğine sığmayan grup daha az
    worker'la yeniden denenir. Varsayılan False olduğunda ve OOM DIŞI her
    hatada render açıkça durur; böylece pahalı sürpriz tekrarlar yapılmaz.

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
        provider = CoquiTTSProvider(gpu=True, retry_incomplete=retry_incomplete)
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
        outcomes = _run_workers(
            groups,
            progress_cb,
            status_cb,
            retry_incomplete=retry_incomplete,
        )
    else:
        outcomes = _run_workers_fn(groups, progress_cb)

    failures = [(i, is_oom, detail) for i, (ok, is_oom, detail) in outcomes.items() if not ok]
    if not failures:
        return [SynthResult(duration=0.0, words=None) for _ in items]

    if retry_incomplete and failures and all(is_oom for _, is_oom, _ in failures) and n_workers > 1:
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

        synthesize_parallel(
            retry_items,
            retry_n,
            _run_workers_fn,
            retry_progress,
            status_cb,
            retry_incomplete=True,
        )
        return [SynthResult(duration=0.0, words=None) for _ in items]

    _, _, detail = failures[0]
    raise RuntimeError(detail or "Coqui paralel üretim beklenmedik şekilde başarısız oldu.")
