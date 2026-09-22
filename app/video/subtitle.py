import re
from pathlib import Path

from app.models import WordTiming

WORDS_PER_CAPTION = 9


def _fmt(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def realign_word_timings(tts_words: list[WordTiming], original_text: str) -> list[WordTiming]:
    """tts_words, fonetik olarak değiştirilmiş metinden üretilmiş olabilir (bkz.
    app/tts/pronunciation.py). Altyazıda kullanıcının yazdığı ORİJİNAL kelimelerin
    görünmesi için, zamanlamaları koruyup metni orijinal kelimelerle eşliyoruz.
    Kelime sayısı uyuşmazsa (nadiren olur) güvenli şekilde tts_words'ü olduğu gibi
    döndürür."""
    original_tokens = original_text.split()
    if len(original_tokens) != len(tts_words):
        return tts_words
    return [
        WordTiming(text=orig, start=w.start, end=w.end)
        for orig, w in zip(original_tokens, tts_words)
    ]


def estimate_word_timings(text: str, duration: float) -> list[WordTiming]:
    words = re.findall(r"\S+", text)
    if not words:
        return []
    total_chars = sum(len(w) for w in words) or 1
    t = 0.0
    timings = []
    for w in words:
        w_dur = duration * (len(w) / total_chars)
        timings.append(WordTiming(text=w, start=t, end=t + w_dur))
        t += w_dur
    return timings


def write_srt(words: list[WordTiming], out_path: Path):
    if not words:
        out_path.write_text("", encoding="utf-8")
        return
    lines = []
    idx = 1
    i = 0
    while i < len(words):
        group = words[i:i + WORDS_PER_CAPTION]
        start = group[0].start
        end = group[-1].end
        caption = " ".join(w.text for w in group)
        lines.append(str(idx))
        lines.append(f"{_fmt(start)} --> {_fmt(end)}")
        lines.append(caption)
        lines.append("")
        idx += 1
        i += WORDS_PER_CAPTION
    out_path.write_text("\n".join(lines), encoding="utf-8")


def escape_ass_text(text: str) -> str:
    r"""Altyazı metnini ASS'de olduğu gibi görünecek şekilde kaçışlar.

    ASS'de `{...}` bir geçersiz kılma bloğudur (içindeki metin kaybolur) ve `\N`, `\n`, `\h`
    dizileri satır sonu/boşluk sayılır. libass ile sınandı: `\{` `\}` süslü parantezi doğru gösterir;
    `\\` çalışmaz, bu yüzden ters eğik çizgiden sonra sıfır genişlikli boşluk (U+200B) konur."""
    text = text.replace("\\", "\\\u200b")
    return text.replace("{", "\\{").replace("}", "\\}")


def _fmt_ass(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


ASS_HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Segoe UI,{fontsize},&H00F2F2F2,&H000000FF,&H00000000,&HB0101010,0,0,0,0,100,100,0,0,3,2,0,2,80,80,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(words: list[WordTiming], out_path: Path, width: int, height: int,
              margin_v: int = 60, fontsize: int = 44):
    header = ASS_HEADER_TEMPLATE.format(w=width, h=height, fontsize=fontsize, marginv=margin_v)
    events = []
    if words:
        i = 0
        while i < len(words):
            group = words[i:i + WORDS_PER_CAPTION]
            start, end = group[0].start, group[-1].end
            caption = escape_ass_text(" ".join(w.text for w in group).replace("\n", " "))
            events.append(f"Dialogue: 0,{_fmt_ass(start)},{_fmt_ass(end)},Default,,0,0,0,,{caption}")
            i += WORDS_PER_CAPTION
    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
