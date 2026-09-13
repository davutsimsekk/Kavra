"""Chapter-level exports for long lectures.

Reuses the segment_NNN.mp4 / slide_NNN.mp3 files a full render already
produced — no re-render, no TTS/LLM cost. Just grouping, ffmpeg concat of
existing files, and duration arithmetic for YouTube chapter timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models import Slide
from app.pipeline import slugify
from app.video.video_builder import concat_audio, concat_videos, ffprobe_duration

CHAPTERS_DIR_NAME = "chapters"


@dataclass
class Chapter:
    index: int  # 1-based
    title: str
    start: int  # 0-based inclusive slide index
    end: int  # 0-based exclusive slide index

    @property
    def slide_count(self) -> int:
        return self.end - self.start


def identify_chapters(slides: list[Slide]) -> list[Chapter]:
    """Group slides at each level=="chapter" marker.

    Slides before the first marker (if any) become an implicit "Giriş"
    chapter, so a per-chapter export never silently drops material.
    """
    if not slides:
        return []
    boundaries = [i for i, s in enumerate(slides) if s.level == "chapter"]
    if not boundaries or boundaries[0] != 0:
        boundaries = [0, *boundaries]
    boundaries.append(len(slides))
    boundaries = sorted(set(boundaries))

    chapters: list[Chapter] = []
    for start, end in zip(boundaries, boundaries[1:]):
        if start >= end:
            continue
        title = (slides[start].title or "").strip() if slides[start].level == "chapter" else ""
        title = title or ("Giriş" if not chapters and slides[start].level != "chapter" else f"Bölüm {len(chapters) + 1}")
        chapters.append(Chapter(len(chapters) + 1, title, start, end))
    return chapters


def format_timestamp(seconds: float) -> str:
    total = max(int(seconds), 0)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_youtube_chapters(chapters: list[Chapter], segment_durations: list[float]) -> str:
    """segment_durations[i] is the duration of the (i+1)-th rendered segment."""
    cumulative = [0.0]
    for duration in segment_durations:
        cumulative.append(cumulative[-1] + duration)
    lines = [f"{format_timestamp(cumulative[chapter.start])} {chapter.title}" for chapter in chapters]
    return "\n".join(lines) + "\n"


def _segment_paths(assets: Path, chapter: Chapter) -> list[Path]:
    return [assets / f"segment_{i + 1:03d}.mp4" for i in range(chapter.start, chapter.end)]


def _audio_paths(assets: Path, chapter: Chapter) -> list[Path]:
    return [assets / f"slide_{i + 1:03d}.mp3" for i in range(chapter.start, chapter.end)]


def missing_render_indexes(pdir: Path, slide_count: int) -> list[int]:
    """1-based slide numbers that don't have a rendered segment yet."""
    assets = pdir / "assets"
    return [i + 1 for i in range(slide_count) if not (assets / f"segment_{i + 1:03d}.mp4").exists()]


def export_all_chapters(pdir: Path, slides: list[Slide]) -> dict[str, Any]:
    chapters = identify_chapters(slides)
    if not chapters:
        raise ValueError("Bölümlere ayrılacak slayt yok.")

    missing = missing_render_indexes(pdir, len(slides))
    if missing:
        raise ValueError(
            f"{len(missing)} slayt henüz render edilmemiş (ör. slayt {missing[0]}). "
            "Önce 3. sekmeden tam bir render tamamla."
        )

    assets = pdir / "assets"
    durations = [ffprobe_duration(assets / f"segment_{i + 1:03d}.mp4") for i in range(len(slides))]

    chapters_dir = pdir / CHAPTERS_DIR_NAME
    chapters_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for chapter in chapters:
        slug = slugify(chapter.title) or f"bolum-{chapter.index}"
        stem = f"{chapter.index:02d}-{slug}"
        video_out = chapters_dir / f"{stem}.mp4"
        audio_out = chapters_dir / f"{stem}.mp3"
        video_list = chapters_dir / f"_concat_{stem}_v.txt"
        audio_list = chapters_dir / f"_concat_{stem}_a.txt"
        concat_videos(_segment_paths(assets, chapter), video_out, video_list)
        concat_audio(_audio_paths(assets, chapter), audio_out, audio_list)
        video_list.unlink(missing_ok=True)
        audio_list.unlink(missing_ok=True)
        results.append({
            "index": chapter.index,
            "title": chapter.title,
            "slideRange": [chapter.start + 1, chapter.end],
            "slideCount": chapter.slide_count,
            "videoFile": video_out.name,
            "audioFile": audio_out.name,
        })

    youtube_text = build_youtube_chapters(chapters, durations)
    yt_path = chapters_dir / "youtube-chapters.txt"
    temp = yt_path.with_suffix(".txt.tmp")
    temp.write_text(youtube_text, encoding="utf-8")
    temp.replace(yt_path)

    return {"chapters": results, "youtubeChaptersFile": yt_path.name}
