"""Deterministic study-material exports derived straight from script.json.

No LLM calls, no extra cost — everything here is a reshaping of slide data the
user already generated. Per the roadmap: "İlk sürüm LLM çağrısı yapmadan slayt
başlıkları, maddeleri ve anlatımdan temel çıktı oluşturmalıdır."
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import WORDS_PER_MINUTE
from app.models import Slide, WordTiming
from app.video.subtitle import WORDS_PER_CAPTION, estimate_word_timings
from app.video.video_builder import ffprobe_duration

EXPORTS_DIR_NAME = "exports"
# Bu iki dışa aktarım türü, diğerlerinin aksine (salt slayt metni) render
# edilmiş ses/zamanlama varlıklarını da okur — write_export'ta ayrıca işaretlenir.
_PDIR_AWARE_KINDS = {"srt", "vtt"}


def _clean(text: str) -> str:
    return (text or "").strip()


def build_markdown_notes(slides: list[Slide]) -> str:
    """A readable lecture-notes document: chapters as H1, topics as H2."""
    lines: list[str] = []
    for slide in slides:
        title = _clean(slide.title) or "Başlıksız"
        if slide.level == "chapter":
            lines.append(f"# {title}\n")
        else:
            lines.append(f"## {title}\n")
        for bullet in slide.bullets:
            bullet = _clean(bullet)
            if bullet:
                lines.append(f"- {bullet}")
        if slide.bullets:
            lines.append("")
        if slide.code:
            lines.append("```")
            lines.append(slide.code.rstrip("\n"))
            lines.append("```\n")
        narration = _clean(slide.narration)
        if narration:
            lines.append(narration + "\n")
    return "\n".join(lines).strip() + "\n"


def build_plain_transcript(slides: list[Slide]) -> str:
    """Just the spoken words, in order — for reading, searching or feeding elsewhere."""
    parts: list[str] = []
    for slide in slides:
        narration = _clean(slide.narration)
        if not narration:
            continue
        title = _clean(slide.title)
        parts.append(f"[{title}]\n{narration}" if title else narration)
    return "\n\n".join(parts).strip() + "\n"


def build_anki_tsv(slides: list[Slide]) -> str:
    """Front/back cards: slide title -> its narration. Plain-text Anki import format.

    Anki's TSV importer treats a literal tab or newline inside a field as a
    column/row break, so both are neutralized per field.
    """

    def field(text: str) -> str:
        return _clean(text).replace("\t", " ").replace("\r\n", "<br>").replace("\n", "<br>")

    rows = ["Front\tBack"]
    for slide in slides:
        if slide.level == "chapter":
            continue
        title = field(slide.title)
        narration = field(slide.narration)
        if not title or not narration:
            continue
        back = narration
        if slide.bullets:
            bullet_html = "<br>".join(f"• {field(b)}" for b in slide.bullets if _clean(b))
            if bullet_html:
                back = f"{bullet_html}<br><br>{narration}"
        rows.append(f"{title}\t{back}")
    return "\n".join(rows) + "\n"


def build_quiz_markdown(slides: list[Slide]) -> str:
    """A printable self-test: numbered recall prompts, then an answer key.

    Deterministic recall prompt per topic slide ("X hakkında ne öğrendin?");
    the answer key holds the narration so it can be printed and folded away
    while studying.
    """
    questions: list[str] = []
    answers: list[str] = []
    number = 0
    for slide in slides:
        if slide.level == "chapter":
            continue
        title = _clean(slide.title)
        narration = _clean(slide.narration)
        if not title or not narration:
            continue
        number += 1
        questions.append(f"{number}. **{title}** hakkında öğrendiklerini kısaca anlat.")
        answers.append(f"{number}. **{title}**\n\n   {narration}")

    if not questions:
        return "# Kendini Test Et\n\nHenüz test edilecek bir slayt yok.\n"

    lines = ["# Kendini Test Et", "", "## Sorular", ""]
    lines.extend(questions)
    lines.append("")
    lines.append("## Cevap Anahtarı")
    lines.append("")
    lines.extend(answers)
    return "\n".join(lines).strip() + "\n"


def _load_cached_words(path: Path) -> list[WordTiming] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [WordTiming(text=w["text"], start=w["start"], end=w["end"]) for w in data]
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _grouped_captions(words: list[WordTiming]) -> list[tuple[float, float, str]]:
    groups = []
    i = 0
    while i < len(words):
        group = words[i:i + WORDS_PER_CAPTION]
        groups.append((group[0].start, group[-1].end, " ".join(w.text for w in group)))
        i += WORDS_PER_CAPTION
    return groups


def _full_video_captions(pdir: Path, slides: list[Slide]) -> list[tuple[float, float, str]]:
    """Video genelinde kümülatif zamanlamalı altyazı grupları (start, end, metin).

    Her slaydın kelimeleri KENDİ İÇİNDE gruplanıp sonra kümülatif ofsetle kaydırılır
    — bu yüzden tek bir altyazı satırı asla iki farklı slaydın kelimelerini birden
    içermez (kısa slaytlarda bu olursa altyazı, slayt geçişinin üzerinden akan garip
    bir satır olurdu).

    Render edilmiş slaytlarda gerçek TTS kelime zamanlamasını (slide_NNN.words.json,
    app/pipeline.py'nin render sırasında yazdığı önbellek) kullanır — bu yüzden bu
    dışa aktarım render'dan SONRA en isabetli halini alır. Henüz render edilmemiş
    slaytlar için kelime sayısından kaba bir süre tahmini yapılır (aynı WORDS_PER_MINUTE
    sabiti, render_estimate ve süre-hedefi bütçelemesiyle paylaşılıyor) — böylece dışa
    aktarım render beklemeden de çalışır, sadece daha az kesin olur.
    """
    assets = pdir / "assets"
    offset = 0.0
    all_groups: list[tuple[float, float, str]] = []
    for i, slide in enumerate(slides, start=1):
        narration = (slide.narration or "").strip() or slide.title
        audio_path = assets / f"slide_{i:03d}.mp3"
        cached_words = _load_cached_words(assets / f"slide_{i:03d}.words.json")
        duration = ffprobe_duration(audio_path) if audio_path.exists() else None
        if duration is None:
            word_count = len(narration.split()) or 1
            duration = max(word_count / WORDS_PER_MINUTE * 60, 0.1)
            cached_words = None  # süresi tahmini olan bir slaytta önbellek zamanlaması güvenilmez
        words = cached_words if cached_words else estimate_word_timings(narration, duration)
        for start, end, caption in _grouped_captions(words):
            all_groups.append((start + offset, end + offset, caption))
        offset += duration
    return all_groups


def _fmt_srt_time(t: float) -> str:
    h, rem = divmod(max(t, 0.0), 3600)
    m, s = divmod(rem, 60)
    ms = round((s - int(s)) * 1000)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def _fmt_vtt_time(t: float) -> str:
    h, rem = divmod(max(t, 0.0), 3600)
    m, s = divmod(rem, 60)
    ms = round((s - int(s)) * 1000)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}.{ms:03d}"


def build_srt(pdir: Path, slides: list[Slide]) -> str:
    """Ayrı, indirilebilir bir .srt altyazı dosyası (videodaki yakılmış altyazıdan
    bağımsız) — YouTube'a ayrıca yüklenebilir, otomatik çeviriye ve aramaya izin verir.
    """
    lines = []
    for idx, (start, end, caption) in enumerate(_full_video_captions(pdir, slides), start=1):
        lines.append(str(idx))
        lines.append(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}")
        lines.append(caption)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_vtt(pdir: Path, slides: list[Slide]) -> str:
    """WebVTT — .srt ile aynı içerik, web video oynatıcıları (ör. <track>) için."""
    lines = ["WEBVTT", ""]
    for start, end, caption in _full_video_captions(pdir, slides):
        lines.append(f"{_fmt_vtt_time(start)} --> {_fmt_vtt_time(end)}")
        lines.append(caption)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


EXPORT_BUILDERS = {
    "notes": ("ders-notlari.md", "text/markdown", build_markdown_notes),
    "transcript": ("transkript.txt", "text/plain", build_plain_transcript),
    "anki": ("anki-kartlari.tsv", "text/tab-separated-values", build_anki_tsv),
    "quiz": ("kendini-test-et.md", "text/markdown", build_quiz_markdown),
    "srt": ("altyazi.srt", "application/x-subrip", build_srt),
    "vtt": ("altyazi.vtt", "text/vtt", build_vtt),
}


def write_export(pdir: Path, kind: str, slides: list[Slide]) -> Path:
    """Render one export kind to disk (atomic) and return its path."""
    if kind not in EXPORT_BUILDERS:
        raise ValueError(f"Bilinmeyen dışa aktarım türü: {kind}")
    filename, _media_type, builder = EXPORT_BUILDERS[kind]
    content = builder(pdir, slides) if kind in _PDIR_AWARE_KINDS else builder(slides)
    exports_dir = pdir / EXPORTS_DIR_NAME
    exports_dir.mkdir(parents=True, exist_ok=True)
    path = exports_dir / filename
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)
    return path
