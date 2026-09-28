import hashlib
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from app.asset_integrity import quarantine_orphan_assets, write_asset_manifest
from app.config import PROJECTS_DIR, VideoOptions
from app.models import RawSection, Slide, SynthResult, WordTiming
from app.parsers import parse_source
from app.tts import get_provider
from app.tts.coqui_parallel import synthesize_parallel
from app.tts.chatterbox_parallel import synthesize_parallel as synthesize_chatterbox_parallel
from app.tts.remote import REMOTE_ENGINES, synthesize_remote
from app.video.slide_renderer import render_slide
from app.video.themes import resolve_theme
from app.tts.pronunciation import effective_map as pronunciation_effective_map, normalize_pronunciation
from app.video.subtitle import estimate_word_timings, realign_word_timings, write_ass
from app.video.video_builder import (
    build_segment, concat_audio, concat_videos, ffprobe_duration, write_chapters_file,
)
from app.quality_gate import save_quality_report

# 3. geçişte (slayt görseli + ffmpeg segmenti) aynı anda kaç slayt işlensin. Bu adım TTS'in
# aksine VRAM tüketmez (PIL çizimi + kısa bir ffmpeg segment encode'u); asıl sınır CPU çekirdek
# sayısı. os.cpu_count() bilinmiyorsa (nadiren None döner) güvenli bir varsayılana düş.
PASS3_MAX_WORKERS = min(4, max(1, (os.cpu_count() or 4)))

# Aşamalı madde gösterimi (bkz. _build_bullet_reveal_stages): _draw_bullet_cards zaten en
# fazla 6 maddeyi çiziyor, aynı sınır burada da geçerli. Bir aşama bu süreden kısa sürecekse
# (çok kısa anlatım + çok madde) okunamayacak kadar hızlı geçer diye reveal tamamen atlanır,
# slayt normal tek-görsel olarak render edilir.
BULLET_REVEAL_MAX_STAGES = 6
BULLET_REVEAL_MIN_STAGE_SECONDS = 0.6
# "comparison" hariç — bkz. render_one'daki reveal_stages ataması yorumu.
_REVEALABLE_LAYOUTS = {"bullets", "process", "definition"}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9ığüşöçİĞÜŞÖÇ_-]+", "_", name).strip("_")
    return s[:60] or "proje"


def project_dir_for(source_path: str | Path) -> Path:
    stem = slugify(Path(source_path).stem)
    d = PROJECTS_DIR / stem
    (d / "assets").mkdir(parents=True, exist_ok=True)
    return d


def parse_and_cache(source_path: str | Path, project_name: str | Path | None = None,
                    page_mode: bool = False, vision_enrich: bool = False,
                    vision_api_key: str | None = None, vision_model: str | None = None,
                    extract_diagrams: bool = False,
                    ) -> tuple[Path, list[RawSection]]:
    pdir = project_dir_for(project_name or source_path)
    sections = parse_source(
        source_path, page_mode=page_mode, pdir=pdir, vision_enrich=vision_enrich,
        vision_api_key=vision_api_key, vision_model=vision_model,
        extract_diagrams=extract_diagrams,
    )
    raw_path = pdir / "raw_sections.json"
    raw_path.write_text(
        json.dumps([asdict(s) for s in sections], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (pdir / "source_path.txt").write_text(str(Path(source_path).resolve()), encoding="utf-8")
    return pdir, sections


def load_raw_sections(pdir: Path) -> list[RawSection]:
    data = json.loads((pdir / "raw_sections.json").read_text(encoding="utf-8"))
    return [RawSection(**d) for d in data]


def save_script(pdir: Path, slides: list[Slide]):
    path = pdir / "script.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps([s.to_dict() for s in slides], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp.replace(path)
    save_quality_report(pdir, slides)


SNAPSHOT_RETENTION = 10


def snapshot_script(pdir: Path, reason: str = "manual") -> Path | None:
    """Copy the current script.json aside before a risky mutation (e.g. regenerate).

    Cheap, file-level safety net — not the full browsable undo UI from the roadmap's
    "Snapshot ve undo" item, just enough that a bad regenerate never destroys data.
    Keeps only the most recent SNAPSHOT_RETENTION copies per project.
    """
    script_path = pdir / "script.json"
    if not script_path.exists():
        return None
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]+", "-", reason).strip("-") or "manual"
    snap_dir = pdir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = snap_dir / f"script-{stamp}-{safe_reason}.json"
    shutil.copy2(script_path, target)
    snapshots = sorted(snap_dir.glob("script-*.json"), key=lambda p: p.stat().st_mtime)
    for stale in snapshots[:-SNAPSHOT_RETENTION]:
        stale.unlink(missing_ok=True)
    return target


def list_snapshots(pdir: Path) -> list[dict]:
    """Newest-first list of recoverable script.json snapshots for this project."""
    snap_dir = pdir / "snapshots"
    if not snap_dir.exists():
        return []
    items = []
    for path in sorted(snap_dir.glob("script-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            slide_count = len(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            slide_count = None
        parts = path.stem.split("-", 4)  # script-YYYYMMDD-HHMMSS-ffffff-<reason>
        reason = parts[4] if len(parts) > 4 else "manual"
        items.append({
            "filename": path.name,
            "reason": reason,
            "slideCount": slide_count,
            "savedAt": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        })
    return items


def restore_snapshot(pdir: Path, filename: str) -> list[Slide]:
    """Restore script.json from a snapshot. The current state is snapshotted first,
    so a restore is itself always undoable."""
    snap_dir = (pdir / "snapshots").resolve()
    candidate = (snap_dir / filename).resolve()
    if candidate.parent != snap_dir or not candidate.is_file():
        raise ValueError("Snapshot bulunamadı.")
    snapshot_script(pdir, reason="before-restore")
    data = json.loads(candidate.read_text(encoding="utf-8"))
    slides = [Slide.from_dict(d) for d in data]
    save_script(pdir, slides)
    return slides


def load_script(pdir: Path) -> list[Slide]:
    data = json.loads((pdir / "script.json").read_text(encoding="utf-8"))
    return [Slide.from_dict(d) for d in data]


def _renderable_signature(slide: Slide) -> dict:
    """Only the fields that actually change rendered output.

    Deliberately NOT slide.to_dict() — Slide gains metadata fields over time
    (sourceSectionIds, manuallyEdited, ...) that don't affect audio/image/
    segment output at all. Hashing the full dict would silently invalidate
    every existing render cache the moment such a field is added, forcing a
    full (possibly hours-long) re-render for no real reason. This happened
    once already (2026-09-13) when source tracking was added; keep this
    narrow on purpose.
    """
    return {
        "title": slide.title,
        "bullets": slide.bullets,
        "layout": slide.layout,
        "code": slide.code,
        # background_image/embedded_image DİĞER metadata alanlarından farklı:
        # gerçekten render edilen görüntüyü değiştiriyor (sayfa modu / diyagram
        # çıkarma), o yüzden kasıtlı olarak dahil.
        "background_image": slide.background_image,
        "embedded_image": slide.embedded_image,
        # Görselin kendisini değiştirmez ama render'daki etiketi değiştirir (bkz.
        # app/video/slide_renderer.py "İNTERNETTEN GÖRSEL"/"YAPAY ZEKA GÖRSELİ" ayrımı).
        "image_source": slide.image_source,
        "narration": slide.narration,
        "level": slide.level,
    }


def _slide_hash(slide: Slide, tts_provider: str, voice: str, rate: str, opts: VideoOptions) -> str:
    payload = json.dumps(_renderable_signature(slide), ensure_ascii=False, sort_keys=True) + \
        f"|{tts_provider}|{voice}|{rate}|{opts.width}x{opts.height}@{opts.fps}" \
        f"|sub={opts.subtitles}|fade={opts.fade_transitions}|kb={opts.ken_burns}" \
        f"|theme={opts.theme_preset}|accent={opts.accent_rgb}|reveal={opts.bullet_reveal}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _narration_hash(narration_for_tts: str, tts_provider: str, voice: str, rate: str) -> str:
    """Only the inputs that actually affect the synthesized audio.

    Kept separate from _slide_hash so a theme/subtitle/fade change (which does
    NOT change this hash) can skip re-synthesizing audio entirely — valuable
    once a provider is slow or paid (Agent CLI, ElevenLabs, Coqui).

    Takes the text AFTER pronunciation normalization, not the raw narration:
    that's the string actually sent to the TTS engine, so editing the
    pronunciation dictionary correctly invalidates only the slides whose
    normalized output actually changed — everything else keeps its cached
    audio automatically, with no separate "dictionary version" to track.
    """
    payload = f"{narration_for_tts}|{tts_provider}|{voice}|{rate}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _load_cached_words(path: Path) -> list[WordTiming] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [WordTiming(text=w["text"], start=w["start"], end=w["end"]) for w in data]
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _save_cached_words(path: Path, words: list[WordTiming] | None) -> None:
    if not words:
        path.unlink(missing_ok=True)
        return
    payload = json.dumps(
        [{"text": w.text, "start": w.start, "end": w.end} for w in words], ensure_ascii=False
    )
    path.write_text(payload, encoding="utf-8")


def _build_bullet_reveal_stages(slide: Slide, index: int, total: int, breadcrumb: str,
                                opts: VideoOptions, duration: float,
                                stage_dir: Path) -> list[tuple[Path, float]] | None:
    """Her aşamada bir madde daha görünen bir dizi görsel üretir. Zamanlama, o maddenin
    narrasyonda GERÇEKTEN ne zaman anlatıldığına göre değil (narration serbest metin,
    maddelerle konum eşleşmesi garanti değil), slaytın toplam ses süresi eşit aralıklara
    bölünerek belirlenir — bkz. VideoOptions.bullet_reveal."""
    stage_count = min(len(slide.bullets), BULLET_REVEAL_MAX_STAGES)
    if stage_count < 2:
        return None
    stage_duration = duration / stage_count
    if stage_duration < BULLET_REVEAL_MIN_STAGE_SECONDS:
        return None
    stage_dir.mkdir(parents=True, exist_ok=True)
    stages = []
    for k in range(1, stage_count + 1):
        stage_slide = replace(slide, bullets=slide.bullets[:k])
        stage_path = stage_dir / f"slide_{index:03d}_stage{k}.png"
        render_slide(stage_slide, index, total, breadcrumb, stage_path,
                     width=opts.width, height=opts.height, accent=opts.accent_rgb,
                     theme_preset=opts.theme_preset)
        stages.append((stage_path, stage_duration))
    return stages


def render_video(pdir: Path, slides: list[Slide], tts_provider_name: str, voice: str,
                  rate: str, opts: VideoOptions, progress_cb=None, status_cb=None,
                  force_audio: bool = False) -> tuple[Path, Path]:
    assets = pdir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    quarantined = quarantine_orphan_assets(pdir, len(slides))
    # Coqui + birden fazla paralel worker seçiliyse (bkz.
    # VideoOptions.coqui_parallel_workers), her worker kendi model kopyasını
    # AYRI bir process'te yükleyecek — bu durumda burada TEK BİR provider'ı
    # ekstra yüklemek (kullanılmayacak ~2GB/30sn'lik bir israf) gereksiz.
    remote_tts = opts.tts_backend == "remote"
    if remote_tts and tts_provider_name not in REMOTE_ENGINES:
        raise ValueError(f"'{tts_provider_name}' motoru uzak GPU'da çalıştırılamaz (yalnızca {', '.join(REMOTE_ENGINES)}).")
    coqui_parallel = tts_provider_name == "coqui" and opts.coqui_parallel_workers > 1 and not remote_tts
    chatterbox_parallel = tts_provider_name == "chatterbox"
    provider = None if coqui_parallel or chatterbox_parallel or remote_tts else get_provider(tts_provider_name)
    pronunciation_map = pronunciation_effective_map()

    breadcrumbs = []
    chap = ""
    for s in slides:
        if s.level == "chapter":
            chap = s.title
        breadcrumbs.append(chap if s.level != "chapter" else "")

    total = len(slides)

    # 1. geçiş: her slaytın önbellek durumunu belirle; henüz sesi olmayanları
    # topla ("pending") — bkz. 2. geçiş.
    plan: list[dict] = []
    pending: list[tuple[int, str, str, Path]] = []
    for i, slide in enumerate(slides, start=1):
        narration = slide.narration.strip() or slide.title
        narration_for_tts = normalize_pronunciation(narration, mapping=pronunciation_map)

        h = _slide_hash(slide, tts_provider_name, voice, rate, opts)
        narration_hash = _narration_hash(narration_for_tts, tts_provider_name, voice, rate)
        entry = {
            "slide": slide, "index": i, "narration": narration,
            "hash": h, "narration_hash": narration_hash,
            "hash_file": assets / f"slide_{i:03d}.hash",
            "narration_hash_file": assets / f"slide_{i:03d}.narration_hash",
            "words_cache_file": assets / f"slide_{i:03d}.words.json",
            "seg_path": assets / f"segment_{i:03d}.mp4",
            "audio_path": assets / f"slide_{i:03d}.mp3",
            "img_path": assets / f"slide_{i:03d}.png",
            "srt_path": assets / f"slide_{i:03d}.ass",
            "fully_cached": False, "audio_reused": False, "synth": None,
        }

        if (
            not force_audio
            and entry["hash_file"].exists() and entry["hash_file"].read_text().strip() == h
            and entry["seg_path"].exists() and entry["audio_path"].exists()
        ):
            entry["fully_cached"] = True
            plan.append(entry)
            continue

        # Yalnızca tema/altyazı/geçiş gibi görsel ayarlar değiştiyse (anlatım/ses/
        # telaffuz ayarları AYNI kaldıysa) sesi yeniden üretme — özellikle yavaş/
        # ücretli bir sağlayıcıda (Agent CLI, ElevenLabs, Coqui) bu ciddi zaman/
        # maliyet kazandırır.
        audio_reusable = (
            not force_audio
            and entry["narration_hash_file"].exists()
            and entry["narration_hash_file"].read_text().strip() == narration_hash
            and entry["audio_path"].exists()
        )
        cached_duration = None
        if audio_reusable:
            try:
                cached_duration = ffprobe_duration(entry["audio_path"])
            except (OSError, ValueError, subprocess.SubprocessError):
                # Yarıda kesilmiş/native çökmüş bir TTS yazımı bozuk MP3
                # bırakmış olabilir; onu geçerli cache sanma.
                audio_reusable = False

        if audio_reusable:
            entry["audio_reused"] = True
            entry["synth"] = SynthResult(
                duration=cached_duration,
                words=_load_cached_words(entry["words_cache_file"]),
            )
        else:
            # Hash'i sentezden önce yazmak güvenlidir: yeniden kullanım ayrıca
            # geçerli bir MP3 ister. Böylece paralel partinin ortasında süreç
            # kapanırsa tamamlanan sesler bir sonraki çalıştırmada korunur.
            entry["audio_path"].unlink(missing_ok=True)
            entry["words_cache_file"].unlink(missing_ok=True)
            entry["narration_hash_file"].write_text(narration_hash, encoding="utf-8")
            pending.append((len(plan), narration_for_tts, voice, entry["audio_path"]))
        plan.append(entry)

    # 2. geçiş: bekleyen sesleri üret. Coqui + birden fazla paralel worker
    # seçiliyse (bkz. VideoOptions.coqui_parallel_workers), bağımsız
    # process'lerde aynı anda üretilir — diğer sağlayıcılar ve tekli Coqui
    # kullanımı etkilenmez, eskisi gibi tek tek üretir.
    if pending:
        items = [(text, v, out_path) for _idx, text, v, out_path in pending]
        total_pending = len(items)

        def on_tts_progress(done: int, done_total: int) -> None:
            # Bu geçişin kendi (0..total_pending) sayacı var, 3. geçişin
            # (0..total) sayacından FARKLI — kasıtlı: kullanıcı arayüzü bu
            # geçişte "Seslendiriliyor" mesajını görüp ilerlemenin gerçekten
            # ilerlediğini anlar; 3. geçiş başlayınca sayaç kendi ölçeğine
            # döner. Hiç ilerleme göstermemekten (eski davranış — büyük
            # projelerde "0/0 Başlatılıyor" olarak saatlerce donmuş görünüyordu)
            # çok daha iyi.
            if progress_cb:
                progress_cb(done, done_total, "Seslendiriliyor")

        def on_tts_status(message: str) -> None:
            if status_cb:
                status_cb(message)

        if remote_tts:
            results = synthesize_remote(
                items,
                tts_provider_name,
                rate,
                opts.remote_tts_concurrency,
                progress_cb=on_tts_progress,
                status_cb=on_tts_status,
            )
        elif coqui_parallel:
            results = synthesize_parallel(
                items,
                opts.coqui_parallel_workers,
                progress_cb=on_tts_progress,
                status_cb=on_tts_status,
            )
        elif chatterbox_parallel:
            results = synthesize_chatterbox_parallel(
                items,
                opts.chatterbox_parallel_workers,
                progress_cb=on_tts_progress,
                status_cb=on_tts_status,
            )
        else:
            results = []
            for i, (text, v, out_path) in enumerate(items, start=1):
                results.append(provider.synthesize(text, v, out_path, rate))
                on_tts_progress(i, total_pending)
        for (plan_index, *_rest), synth in zip(pending, results):
            plan[plan_index]["synth"] = synth

    # 3. geçiş: her slayt için görsel/segment üretimi. Slaytlar birbirinden bağımsız
    # (her biri kendi img_path/seg_path'ine yazar, paylaşılan durum yok — render_slide
    # her çağrıda kendi font nesnelerini oluşturur, ffmpeg zaten ayrı bir süreç), bu yüzden
    # yeni üretilecek slaytlar PASS3_MAX_WORKERS kadar iş parçacığında paralel işlenir.
    # Önbellekten gelenler (fully_cached) anında tamamlandığından sıralı bırakıldı.
    pending = [entry for entry in plan if not entry["fully_cached"]]
    done = 0
    if progress_cb:
        for entry in plan:
            if entry["fully_cached"]:
                progress_cb(entry["index"], total, entry["slide"].title)

    def render_one(entry: dict) -> None:
        i = entry["index"]
        slide = entry["slide"]
        narration = entry["narration"]
        synth = entry["synth"]
        audio_path = entry["audio_path"]
        img_path = entry["img_path"]
        srt_path = entry["srt_path"]
        seg_path = entry["seg_path"]

        duration = ffprobe_duration(audio_path)
        entry["duration"] = duration
        if not entry["audio_reused"]:
            entry["narration_hash_file"].write_text(entry["narration_hash"], encoding="utf-8")
            _save_cached_words(entry["words_cache_file"], synth.words)

        # Kod bloğu bulunan slaytlarda altyazı kutusu ekranın alt kısmındaki kod
        # kutusuyla çakışabileceğinden, o slaytlarda altyazı gösterilmez.
        use_subtitles = opts.subtitles and not slide.code
        ass_path = None
        if use_subtitles:
            if synth.words:
                words = realign_word_timings(synth.words, narration)
            else:
                words = estimate_word_timings(narration, duration)
            # Kelime bazlı vurgulama, tema ne kullanıyorsa onun accent rengini alır — render_slide
            # ile aynı öncelik: opts.accent_rgb verilmişse görseldeki gibi o zorlanır.
            accent = opts.accent_rgb or resolve_theme(opts.theme_preset, slide, i).accent
            write_ass(words, srt_path, opts.width, opts.height, margin_v=60, accent=accent)
            ass_path = srt_path

        reveal_stages = None
        # Aşamalı gösterim, maddeleri OLDUĞU SIRAYLA teker teker biriktiren (bullets[:k])
        # formatlarda çalışır — "bullets" (kart listesi) ve "process" (numaralı akış) bu şekilde
        # doğal bir şekilde büyür; "definition" de her önek geçerli bir alt küme "Terim: Tanım"
        # çifti olduğundan (bkz. _parse_definition_pairs) sorunsuz çalışır. "comparison" KASITLI
        # OLARAK dışında bırakıldı: "---" ayracından önceki aşamalar geçici olarak düz madde
        # kartlarına düşer, ayraç bullets[:k]'ya girer girmez görünüm aniden iki sütuna sıçrar —
        # aynı slayt içinde göze batan bir şekil değişikliği olurdu (bkz. compact modda da aynı
        # gerekçeyle dışlanması, app/video/slide_renderer.py _draw_content_layout). "callout"/
        # "formula"/"emphasis" zaten her zaman TEK maddeden oluştuğundan aşağıdaki
        # `len(bullets) >= 2` koşuluyla doğal olarak elenir, ayrıca hariç tutulmalarına gerek yok.
        if opts.bullet_reveal and slide.layout in _REVEALABLE_LAYOUTS and slide.level != "chapter" \
                and not slide.background_image and len(slide.bullets) >= 2:
            reveal_stages = _build_bullet_reveal_stages(
                slide, i, total, breadcrumbs[i - 1], opts, duration, assets / "_reveal",
            )

        if reveal_stages is not None:
            # img_path'i de dolu tutuyoruz (son aşamanın kopyası) — tüm maddeleri gösteren
            # bir slayt PNG'si her zaman var olsun (thumbnail/teşhis amaçlı), reveal'a özgü
            # bir "eksik dosya" davranışı çıkmasın.
            shutil.copyfile(reveal_stages[-1][0], img_path)
        else:
            render_slide(slide, i, total, breadcrumbs[i - 1], img_path,
                         width=opts.width, height=opts.height, accent=opts.accent_rgb,
                         theme_preset=opts.theme_preset)

        build_segment(img_path, audio_path, duration, seg_path, opts, ass_path,
                     reveal_stages=reveal_stages)

        entry["hash_file"].write_text(entry["hash"], encoding="utf-8")

    if pending:
        workers = min(PASS3_MAX_WORKERS, len(pending))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pass3") as pool:
            futures = {pool.submit(render_one, entry): entry for entry in pending}
            try:
                for future in as_completed(futures):
                    future.result()  # bir slayt başarısız olursa hemen fırlat, render'ı durdur
                    done += 1
                    if progress_cb:
                        entry = futures[future]
                        progress_cb(done, total, entry["slide"].title)
            except BaseException:
                # Bir görev hata verince ya da render iptal edilince bekleyen diğer görevleri
                # başlatma; halihazırda çalışanların (ffmpeg alt süreçleri) bitmesi beklenir,
                # süreç ağacının tamamen kapatılması API tarafındaki iptal mekanizmasının işi.
                for pending_future in futures:
                    pending_future.cancel()
                raise

    # segment_paths/audio_paths, paralel tamamlanma sırasından bağımsız olarak DAİMA slayt
    # index sırasında olmalı — concat_videos/concat_audio bu listeyi olduğu gibi birleştirir.
    segment_paths = [entry["seg_path"] for entry in plan]
    audio_paths = [entry["audio_path"] for entry in plan]

    # "chapter" slaytlarını video konteynerine (VLC/mpv gibi oynatıcıların gösterdiği)
    # atlanabilir bölüm işaretleri olarak gömüyoruz — fully_cached slaytlarda süre daha
    # önce hesaplanmadığından burada tek seferlik ucuz bir ffprobe ile tamamlanıyor.
    chapters: list[tuple[float, str]] = []
    cursor = 0.0
    for entry in plan:
        entry_duration = entry.get("duration")
        if entry_duration is None:
            entry_duration = ffprobe_duration(entry["audio_path"])
        if entry["slide"].level == "chapter":
            chapters.append((cursor, entry["slide"].title))
        cursor += entry_duration

    chapters_file = None
    if chapters:
        chapters_file = assets / "_chapters.txt"
        write_chapters_file(chapters, cursor, chapters_file)

    final_video = pdir / "ders.mp4"
    final_audio = pdir / "ders.mp3"
    concat_videos(segment_paths, final_video, assets / "_concat_video.txt", chapters_file=chapters_file)
    concat_audio(audio_paths, final_audio, assets / "_concat_audio.txt")
    write_asset_manifest(pdir, len(slides), quarantined)
    return final_video, final_audio
