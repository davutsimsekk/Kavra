import json
import re
from abc import ABC, abstractmethod

from app.config import WORDS_PER_MINUTE
from app.generation_checkpoint import source_fingerprint
from app.models import RawSection, Slide

JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

# Bütçe notu çok küçük bir parçaya düşse bile (ör. tek satırlık bir kaynak
# bölümü) modele anlamsız "2 kelimede anlat" gibi bir talimat gitmesin diye.
_MIN_CHUNK_BUDGET_WORDS = 60

_SINGLE_SLIDE_NOTE = (
    "SAYFA MODU AKTİF: Bu istek TEK bir kaynak sayfasını temsil ediyor ve bu "
    "sayfanın görüntüsü zaten olduğu gibi videoda slayt olarak kullanılacak. Bu "
    "yüzden TAM OLARAK 1 slayt üret — sayfayı asla birden fazla slayta bölme. "
    "\"narration\" alanına bu sayfanın TÜM içeriğini kapsayan tek, akıcı bir "
    "anlatım yaz (sayfa görsel ağırlıklıysa ve metin azsa, elindeki başlık/metin "
    "ipucundan mantıklı bir anlatım kur)."
)


def _page_batch_note(sections: list[RawSection]) -> str:
    if len(sections) == 1:
        return _SINGLE_SLIDE_NOTE
    mapping = [
        {"position": i + 1, "sourceSectionId": source_fingerprint(section),
         "title": section.title, "breadcrumb": section.breadcrumb}
        for i, section in enumerate(sections)
    ]
    return (
        "SAYFA MODU AKTİF: Kaynak sayfalarını birlikte okuyup tutarlı bir anlatım kur. "
        f"TAM OLARAK {len(sections)} slayt üret, her kaynak sayfasına bir slayt. "
        "Bu kural, genel slayt bölme kuralından önceliklidir. Sayfaları birleştirme veya atlama; giriş/bölüm ayıracı gibi ek slayt üretme. "
        "Her slayt yalnızca kendi sayfasını anlatsın; görüntüsü o PDF sayfası olacak. "
        "Her JSON slayt nesnesine sourceSectionIds alanını ekle: bu alan yalnızca "
        "o sayfanın aşağıdaki sourceSectionId değerini içeren tek elemanlı bir dizi olmalı. "
        "Kimlikleri aynen kopyala; sayfa başlıkları aynı olsa bile kimlikleri farklıdır. "
        "Yanıtı aşağıdaki kaynak sırasıyla ver. Sayfa eşlemesi:\n"
        + json.dumps(mapping, ensure_ascii=False)
    )


def _align_page_slides(slides: list[Slide], sections: list[RawSection]) -> list[Slide]:
    """Validate the entire response before persisting any part of a page batch."""
    if len(sections) == 1:
        result = [slides[0] if len(slides) == 1 else _merge_into_one_slide(slides, sections)]
    else:
        grouped = {source_fingerprint(section): [] for section in sections}
        for slide in slides:
            ids = slide.source_section_ids
            if len(ids) != 1 or not isinstance(ids[0], str) or ids[0] not in grouped:
                raise ValueError("PDF yanıtında sayfa kimliği eksik veya geçersiz. Bu parça kaydedilmedi; çağrı başına daha az sayfayla tekrar deneyebilirsin.")
            grouped[ids[0]].append(slide)
        if any(not group for group in grouped.values()):
            raise ValueError("PDF yanıtında bazı sayfaların anlatımı eksik. Bu parça kaydedilmedi; çağrı başına daha az sayfayla tekrar deneyebilirsin.")
        result = [
            group[0] if len(group) == 1 else _merge_into_one_slide(group, [section])
            for section in sections
            for group in [grouped[source_fingerprint(section)]]
        ]
    for slide, section in zip(result, sections):
        slide.background_image = section.page_image
        tag_slides_with_source([slide], [section])
    return result


def _merge_into_one_slide(slides: list[Slide], section_chunk: list[RawSection]) -> Slide:
    """single_slide_per_section talimatına rağmen model yine de >1 slayt
    döndürürse, içerik kaybetmemek için hepsini TEK slaytta birleştir.
    """
    if not slides:
        title = section_chunk[0].title if section_chunk else ""
        return Slide(title=title, bullets=[], code=None, narration="", level="topic")
    merged_bullets: list[str] = []
    for slide in slides:
        for bullet in slide.bullets:
            if bullet not in merged_bullets:
                merged_bullets.append(bullet)
    first = slides[0]
    return Slide(
        title=first.title,
        bullets=merged_bullets[:6],
        code=next((s.code for s in slides if s.code), None),
        narration=" ".join(s.narration.strip() for s in slides if s.narration.strip()),
        level=first.level,
    )


def tag_slides_with_source(slides: list[Slide], sections: list[RawSection]) -> None:
    """Stamp freshly generated slides with which source sections produced them.

    Uses the same fingerprint identity as app.generation_checkpoint so resume,
    checkpointing and "regenerate this slide" all agree on what a source is.
    """
    if not sections:
        return
    ids = [source_fingerprint(section) for section in sections]
    titles = [section.title for section in sections]
    for slide in slides:
        slide.source_section_ids = ids
        slide.source_titles = titles


def build_continuity_context(slides: list[Slide], max_recent: int = 6,
                              full_text_recent: int = 2, full_text_max_chars: int = 600) -> str:
    """Create a compact lesson memory that can be sent to stateless API calls.

    Claude Agent modunda --resume gerçek bir konuşma oturumunu sürdürdüğü için
    model önceki turların TAM metnini görür. Gemini/OpenAI gibi durumsuz
    sağlayıcılarda böyle bir oturum yok; bu fonksiyon onun yerine geçen yapay
    hafızadır. Uzun süre sadece madde özetleri (bullets) taşıdı — model önceki
    slaytların GERÇEK cümlelerini hiç görmediği için aynı cümleyi ya da çok
    benzerini fark etmeden tekrar üretebiliyordu (birebir/yakın tekrar,
    app.quality_gate'in "duplicate_sentence" kontrolünün yakaladığı durum).
    Bu yüzden en son birkaç slaydın TAM anlatım metni de (kısaltılmış olarak,
    maliyeti sınırlamak için) hafızaya ekleniyor.
    """
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

    parts = [
        "DERS BÜTÜNLÜĞÜ HAFIZASI (çıktıya kopyalama):",
        f"Önceden oluşturulan slayt sırası: {title_summary}",
        "Son slaytların kısa özeti:",
        "\n".join(recent_lines),
    ]

    full_text_lines = []
    for slide in slides[-full_text_recent:]:
        narration = (slide.narration or "").strip()
        if not narration:
            continue
        if len(narration) > full_text_max_chars:
            narration = narration[:full_text_max_chars].rstrip() + "…"
        full_text_lines.append(f'- {slide.title}: "{narration}"')
    if full_text_lines:
        parts.append(
            "En son birkaç slaydın TAM anlatım metni (bu cümleleri veya çok "
            "benzerlerini BİREBİR TEKRAR ÜRETME, aynı bilgiyi tekrar edeceksen "
            "farklı kelimelerle ve kısaca değin):"
        )
        parts.append("\n".join(full_text_lines))

    parts.append(
        "Yeni slaytlarda aynı terminolojiyi ve anlatım seviyesini koru, doğal bir "
        "geçiş kur ve önceki slaytları tekrar üretme."
    )
    return "\n".join(parts)


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


def _section_weight(section: RawSection) -> int:
    """Kaba bir "bu bölüm ne kadar kaynak içeriyor" ölçüsü (karakter sayısı).

    chunk_sections'ın chunk sınırlarını belirlemesinde VE süre-hedefi
    bütçelemesinde (bkz. _duration_budget_note) aynı ölçü kullanılır ki bir
    parçanın "kaynağın %X'i" payı, chunk'lama mantığıyla tutarlı olsun.
    """
    return len(section.text) + sum(len(c) for c in section.code_blocks) + len(section.title)


def chunk_sections(sections: list[RawSection], max_chars: int = 6000,
                   max_sections: int | None = None) -> list[list[RawSection]]:
    chunks: list[list[RawSection]] = []
    cur: list[RawSection] = []
    cur_len = 0
    for s in sections:
        s_len = _section_weight(s)
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


def _duration_budget_note(chunk: list[RawSection], total_weight: int, target_minutes: float) -> str:
    """Bu chunk'a, kaynaktaki payıyla orantılı bir süre/kelime bütçesi ata.

    Varsayılan (süre hedefi kapalı) üretimde prompt'a hiç dokunulmaz — bu not
    sadece kullanıcı GUI'de "Süre hedefi" özelliğini açtığında eklenir.
    """
    chunk_weight = sum(_section_weight(s) for s in chunk)
    share = (chunk_weight / total_weight) if total_weight else 0.0
    chunk_minutes = max(target_minutes * share, 0.0)
    chunk_words = max(round(chunk_minutes * WORDS_PER_MINUTE), _MIN_CHUNK_BUDGET_WORDS)
    share_pct = round(share * 100, 1)
    return (
        f"SÜRE HEDEFİ MODU AKTİF: Tüm ders için toplam hedef süre ~{target_minutes:g} dakika. "
        f"Bu parça, seçili kaynağın yaklaşık %{share_pct:g}'ini oluşturuyor; buna göre bu parçaya "
        f"düşen anlatım payı toplamda yaklaşık {chunk_minutes:.1f} dakika (~{chunk_words} kelime, "
        "tüm 'narration' alanlarının TOPLAMI). Konuyu gerektiği kadar slayta böl ama toplam kelime "
        "sayısını bu bütçeye yakın tut (±%15 tolerans kabul edilebilir); bütçeyi doldurmak için "
        "gereksiz ayrıntı eklemene ya da her alt başlığı ayrı bir slayta dağıtmana gerek yok — "
        "bütçe dar geliyorsa en önemli noktaları öne çıkar, ikincil detayları kısalt."
    )


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
                          continuity_context: bool = True,
                          target_duration_minutes: float | None = None,
                          single_slide_per_section: bool = False) -> list[Slide]:
        import time

        if max_chars is None:
            # Bölüm sayısı açıkça belirtildiyse (ör. web UI "kaynak bölümü" alanı),
            # kullanıcının niyeti odur; eski karakter tabanlı 6000 sınırı sessizce
            # onu ezmesin diye çok daha yüksek bir güvenlik tavanı kullanılır.
            # Sadece karakter tabanlı (eski) kullanım için 6000 varsayılanı korunur.
            max_chars = 6000 if max_sections_per_chunk is None else 40_000
        # Source sections stay whole; page mode controls output correspondence,
        # independently of how many pages share an LLM request.
        chunks = [sections] if single_request and sections else chunk_sections(
            sections, max_chars, max_sections=max_sections_per_chunk
        )
        # Toplam ağırlık TÜM seçili kaynak üzerinden (chunk'lardan önce) hesaplanır
        # ki her chunk'a düşen pay, kullanıcının GUI'de seçtiği bütünün gerçek bir
        # yüzdesi olsun — chunk'lama stratejisi (karakter/bölüm sayısı) değişse bile.
        total_weight = sum(_section_weight(s) for s in sections) if target_duration_minutes else 0
        all_slides: list[Slide] = []
        lesson_memory = list(initial_context_slides or [])
        for i, chunk in enumerate(chunks, start=1):
            if progress_cb:
                progress_cb(i, len(chunks), chunk[0].title if chunk else "")
            effective_note = style_note
            if continuity_context and lesson_memory:
                memory_note = build_continuity_context(lesson_memory)
                effective_note = f"{style_note}\n\n{memory_note}".strip()
            if target_duration_minutes and total_weight:
                budget_note = _duration_budget_note(chunk, total_weight, target_duration_minutes)
                effective_note = f"{effective_note}\n\n{budget_note}".strip()
            if single_slide_per_section:
                effective_note = f"{effective_note}\n\n{_page_batch_note(chunk)}".strip()
            chunk_slides = self.generate(chunk, effective_note)
            if single_slide_per_section:
                chunk_slides = _align_page_slides(chunk_slides, chunk)
            else:
                tag_slides_with_source(chunk_slides, chunk)
            all_slides.extend(chunk_slides)
            lesson_memory.extend(chunk_slides)
            if chunk_result_cb:
                chunk_result_cb(i, len(chunks), chunk_slides)
            if chunk_completed_cb:
                chunk_completed_cb(i, len(chunks), chunk, chunk_slides)
            if i < len(chunks) and request_interval_sec > 0:
                time.sleep(request_interval_sec)
        return all_slides
