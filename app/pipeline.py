import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from app.config import PROJECTS_DIR, VideoOptions
from app.models import RawSection, Slide
from app.parsers import parse_source
from app.tts import get_provider
from app.video.slide_renderer import render_slide
from app.tts.pronunciation import normalize_pronunciation
from app.video.subtitle import estimate_word_timings, realign_word_timings, write_ass
from app.video.video_builder import build_segment, concat_audio, concat_videos, ffprobe_duration


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9ığüşöçİĞÜŞÖÇ_-]+", "_", name).strip("_")
    return s[:60] or "proje"


def project_dir_for(source_path: str | Path) -> Path:
    stem = slugify(Path(source_path).stem)
    d = PROJECTS_DIR / stem
    (d / "assets").mkdir(parents=True, exist_ok=True)
    return d


def parse_and_cache(source_path: str | Path,
                    project_name: str | Path | None = None) -> tuple[Path, list[RawSection]]:
    pdir = project_dir_for(project_name or source_path)
    sections = parse_source(source_path)
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


def load_script(pdir: Path) -> list[Slide]:
    data = json.loads((pdir / "script.json").read_text(encoding="utf-8"))
    return [Slide.from_dict(d) for d in data]


def _slide_hash(slide: Slide, tts_provider: str, voice: str, rate: str, opts: VideoOptions) -> str:
    payload = json.dumps(slide.to_dict(), ensure_ascii=False, sort_keys=True) + \
        f"|{tts_provider}|{voice}|{rate}|{opts.width}x{opts.height}@{opts.fps}" \
        f"|sub={opts.subtitles}|fade={opts.fade_transitions}|kb={opts.ken_burns}" \
        f"|theme={opts.theme_preset}|accent={opts.accent_rgb}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def render_video(pdir: Path, slides: list[Slide], tts_provider_name: str, voice: str,
                  rate: str, opts: VideoOptions, progress_cb=None) -> tuple[Path, Path]:
    assets = pdir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    provider = get_provider(tts_provider_name)

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

        h = _slide_hash(slide, tts_provider_name, voice, rate, opts)
        hash_file = assets / f"slide_{i:03d}.hash"
        seg_path = assets / f"segment_{i:03d}.mp4"
        audio_path = assets / f"slide_{i:03d}.mp3"
        img_path = assets / f"slide_{i:03d}.png"
        srt_path = assets / f"slide_{i:03d}.ass"

        if hash_file.exists() and hash_file.read_text().strip() == h and seg_path.exists():
            segment_paths.append(seg_path)
            audio_paths.append(audio_path)
            continue

        narration = slide.narration.strip() or slide.title
        narration_for_tts = normalize_pronunciation(narration)
        synth = provider.synthesize(narration_for_tts, voice, audio_path, rate)
        duration = ffprobe_duration(audio_path)

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
    return final_video, final_audio
