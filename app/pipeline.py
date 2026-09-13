import hashlib
import json
import re
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.asset_integrity import quarantine_orphan_assets, write_asset_manifest
from app.config import PROJECTS_DIR, VideoOptions
from app.models import RawSection, Slide, SynthResult, WordTiming
from app.parsers import parse_source
from app.tts import get_provider
from app.video.slide_renderer import render_slide
from app.tts.pronunciation import effective_map as pronunciation_effective_map, normalize_pronunciation
from app.video.subtitle import estimate_word_timings, realign_word_timings, write_ass
from app.video.video_builder import build_segment, concat_audio, concat_videos, ffprobe_duration
from app.quality_gate import save_quality_report


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
        "code": slide.code,
        # background_image/embedded_image DİĞER metadata alanlarından farklı:
        # gerçekten render edilen görüntüyü değiştiriyor (sayfa modu / diyagram
        # çıkarma), o yüzden kasıtlı olarak dahil.
        "background_image": slide.background_image,
        "embedded_image": slide.embedded_image,
        "narration": slide.narration,
        "level": slide.level,
    }


def _slide_hash(slide: Slide, tts_provider: str, voice: str, rate: str, opts: VideoOptions) -> str:
    payload = json.dumps(_renderable_signature(slide), ensure_ascii=False, sort_keys=True) + \
        f"|{tts_provider}|{voice}|{rate}|{opts.width}x{opts.height}@{opts.fps}" \
        f"|sub={opts.subtitles}|fade={opts.fade_transitions}|kb={opts.ken_burns}" \
        f"|theme={opts.theme_preset}|accent={opts.accent_rgb}"
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


def render_video(pdir: Path, slides: list[Slide], tts_provider_name: str, voice: str,
                  rate: str, opts: VideoOptions, progress_cb=None) -> tuple[Path, Path]:
    assets = pdir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    quarantined = quarantine_orphan_assets(pdir, len(slides))
    provider = get_provider(tts_provider_name)
    pronunciation_map = pronunciation_effective_map()

    breadcrumbs = []
    chap = ""
    for s in slides:
        if s.level == "chapter":
            chap = s.title
        breadcrumbs.append(chap if s.level != "chapter" else "")

    segment_paths = []
    audio_paths = []
    total = len(slides)

    for i, slide in enumerate(slides, start=1):
        if progress_cb:
            progress_cb(i, total, slide.title)

        narration = slide.narration.strip() or slide.title
        narration_for_tts = normalize_pronunciation(narration, mapping=pronunciation_map)

        h = _slide_hash(slide, tts_provider_name, voice, rate, opts)
        narration_hash = _narration_hash(narration_for_tts, tts_provider_name, voice, rate)
        hash_file = assets / f"slide_{i:03d}.hash"
        narration_hash_file = assets / f"slide_{i:03d}.narration_hash"
        words_cache_file = assets / f"slide_{i:03d}.words.json"
        seg_path = assets / f"segment_{i:03d}.mp4"
        audio_path = assets / f"slide_{i:03d}.mp3"
        img_path = assets / f"slide_{i:03d}.png"
        srt_path = assets / f"slide_{i:03d}.ass"

        if (
            hash_file.exists()
            and hash_file.read_text().strip() == h
            and seg_path.exists()
            and audio_path.exists()
        ):
            segment_paths.append(seg_path)
            audio_paths.append(audio_path)
            continue

        # Yalnızca tema/altyazı/geçiş gibi görsel ayarlar değiştiyse (anlatım/ses/
        # telaffuz ayarları AYNI kaldıysa) sesi yeniden üretme — özellikle yavaş/
        # ücretli bir sağlayıcıda (Agent CLI, ElevenLabs, Coqui) bu ciddi zaman/
        # maliyet kazandırır.
        audio_reusable = (
            narration_hash_file.exists()
            and narration_hash_file.read_text().strip() == narration_hash
            and audio_path.exists()
        )
        if audio_reusable:
            duration = ffprobe_duration(audio_path)
            synth = SynthResult(duration=duration, words=_load_cached_words(words_cache_file))
        else:
            synth = provider.synthesize(narration_for_tts, voice, audio_path, rate)
            duration = ffprobe_duration(audio_path)
            narration_hash_file.write_text(narration_hash, encoding="utf-8")
            _save_cached_words(words_cache_file, synth.words)

        # Kod bloğu bulunan slaytlarda altyazı kutusu ekranın alt kısmındaki kod
        # kutusuyla çakışabileceğinden, o slaytlarda altyazı gösterilmez.
        use_subtitles = opts.subtitles and not slide.code
        ass_path = None
        if use_subtitles:
            if synth.words:
                words = realign_word_timings(synth.words, narration)
            else:
                words = estimate_word_timings(narration, duration)
            write_ass(words, srt_path, opts.width, opts.height, margin_v=60)
            ass_path = srt_path

        render_slide(slide, i, total, breadcrumbs[i - 1], img_path,
                     width=opts.width, height=opts.height, accent=opts.accent_rgb,
                     theme_preset=opts.theme_preset)

        build_segment(img_path, audio_path, duration, seg_path, opts, ass_path)

        hash_file.write_text(h, encoding="utf-8")
        segment_paths.append(seg_path)
        audio_paths.append(audio_path)

    final_video = pdir / "ders.mp4"
    final_audio = pdir / "ders.mp3"
    concat_videos(segment_paths, final_video, assets / "_concat_video.txt")
    concat_audio(audio_paths, final_audio, assets / "_concat_audio.txt")
    write_asset_manifest(pdir, len(slides), quarantined)
    return final_video, final_audio
