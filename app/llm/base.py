import json
import re
from abc import ABC, abstractmethod

from app.models import RawSection, Slide

JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def build_continuity_context(slides: list[Slide], max_recent: int = 6) -> str:
    """Create a compact lesson memory that can be sent to stateless API calls."""
    if not slides:
        return ""

    visible_titles = [s.title.strip() for s in slides if s.title.strip()]
    if len(visible_titles) > 60:
        omitted = len(visible_titles) - 60
        title_summary = f"({omitted} eski başlık atlandı) " + " → ".join(visible_titles[-60:])
    else:
        title_summary = " → ".join(visible_titles)

    recent_lines = []
    for slide in slides[-max_recent:]:
        bullets = "; ".join(slide.bullets[:4])
        recent_lines.append(f"- {slide.title}: {bullets}" if bullets else f"- {slide.title}")

    return (
        "DERS BÜTÜNLÜĞÜ HAFIZASI (çıktıya kopyalama):\n"
        f"Önceden oluşturulan slayt sırası: {title_summary}\n"
        "Son slaytların kısa özeti:\n"
        + "\n".join(recent_lines)
        + "\nYeni slaytlarda aynı terminolojiyi ve anlatım seviyesini koru, doğal bir "
          "geçiş kur ve önceki slaytları tekrar üretme."
    )


def extract_json_array(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and isinstance(parsed.get("slides"), list):
            return parsed["slides"]
    except json.JSONDecodeError:
        pass
    m = JSON_ARRAY_RE.search(text)
    if m:
        return json.loads(m.group(0))
    raise ValueError("Modelin çıktısından geçerli bir JSON dizisi çıkarılamadı.")


def chunk_sections(sections: list[RawSection], max_chars: int = 6000,
                   max_sections: int | None = None) -> list[list[RawSection]]:
    chunks: list[list[RawSection]] = []
    cur: list[RawSection] = []
    cur_len = 0
    for s in sections:
        s_len = len(s.text) + sum(len(c) for c in s.code_blocks) + len(s.title)
        section_limit_reached = max_sections is not None and len(cur) >= max_sections
        if cur and (cur_len + s_len > max_chars or section_limit_reached):
            chunks.append(cur)
            cur = []
            cur_len = 0
        cur.append(s)
        cur_len += s_len
    if cur:
        chunks.append(cur)
    return chunks


class NarrationGenerator(ABC):
    @abstractmethod
    def generate(self, sections: list[RawSection], style_note: str = "") -> list[Slide]:
        ...

    def generate_chunked(self, sections: list[RawSection], style_note: str = "",
                          max_chars: int | None = None, progress_cb=None,
                          single_request: bool = False,
                          request_interval_sec: float = 4.0,
                          max_sections_per_chunk: int | None = None,
                          chunk_result_cb=None,
                          chunk_completed_cb=None,
                          initial_context_slides: list[Slide] | None = None,
                          continuity_context: bool = True) -> list[Slide]:
        import time

        if max_chars is None:
            # Bölüm sayısı açıkça belirtildiyse (ör. web UI "kaynak bölümü" alanı),
            # kullanıcının niyeti odur; eski karakter tabanlı 6000 sınırı sessizce
            # onu ezmesin diye çok daha yüksek bir güvenlik tavanı kullanılır.
            # Sadece karakter tabanlı (eski) kullanım için 6000 varsayılanı korunur.
            max_chars = 6000 if max_sections_per_chunk is None else 40_000
        chunks = [sections] if single_request else chunk_sections(
            sections, max_chars, max_sections=max_sections_per_chunk
        )
        all_slides: list[Slide] = []
        lesson_memory = list(initial_context_slides or [])
        for i, chunk in enumerate(chunks, start=1):
            if progress_cb:
                progress_cb(i, len(chunks), chunk[0].title if chunk else "")
            effective_note = style_note
            if continuity_context and lesson_memory:
                memory_note = build_continuity_context(lesson_memory)
                effective_note = f"{style_note}\n\n{memory_note}".strip()
            chunk_slides = self.generate(chunk, effective_note)
            all_slides.extend(chunk_slides)
            lesson_memory.extend(chunk_slides)
            if chunk_result_cb:
                chunk_result_cb(i, len(chunks), chunk_slides)
            if chunk_completed_cb:
                chunk_completed_cb(i, len(chunks), chunk, chunk_slides)
            if i < len(chunks) and request_interval_sec > 0:
                time.sleep(request_interval_sec)
        return all_slides
