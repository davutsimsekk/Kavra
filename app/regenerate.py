"""Targeted single-slide regeneration.

A generation chunk usually produces several slides from several sections at
once (see app.llm.base.tag_slides_with_source). Regenerating "one slide" means
finding every slide that shares that same source-section group and replacing
the whole group in one shot — otherwise the user would end up with duplicate
or orphaned siblings from two different generations of the same material.
"""

from __future__ import annotations

from app.generation_checkpoint import source_fingerprint
from app.models import RawSection, Slide


def resolve_sibling_range(slides: list[Slide], index: int) -> tuple[int, int]:
    """Return the [start, end) span of slides sharing slides[index]'s source group."""
    if not (0 <= index < len(slides)):
        raise ValueError("Geçersiz slayt indeksi.")
    target_ids = slides[index].source_section_ids
    start = index
    while start > 0 and slides[start - 1].source_section_ids == target_ids:
        start -= 1
    end = index + 1
    while end < len(slides) and slides[end].source_section_ids == target_ids:
        end += 1
    return start, end


def resolve_source_sections(
    sections: list[RawSection], source_ids: list[str]
) -> list[RawSection]:
    """Resolve stored fingerprints back to the current RawSection objects.

    Order follows the source document, not the stored id order, so a
    regenerate prompt reads the material the same way the original one did.
    """
    wanted = set(source_ids)
    return [section for section in sections if source_fingerprint(section) in wanted]


def apply_regeneration(
    slides: list[Slide], index: int, fresh_slides: list[Slide]
) -> list[Slide]:
    """Replace the sibling group containing `index` with freshly generated slides.

    Raises ValueError if the target slide has no recorded source (manually
    added slides can't be regenerated — there is nothing to regenerate from).
    """
    if not (0 <= index < len(slides)):
        raise ValueError("Geçersiz slayt indeksi.")
    target = slides[index]
    if not target.source_section_ids:
        raise ValueError(
            "Bu slaytın kayıtlı bir kaynağı yok (elle eklenmiş olabilir); yeniden üretilemez."
        )
    if not fresh_slides:
        raise ValueError("Yeniden üretim boş sonuç döndürdü.")
    start, end = resolve_sibling_range(slides, index)
    for slide in fresh_slides:
        if not (slide.background_image and slide.source_section_ids and set(slide.source_section_ids).issubset(target.source_section_ids)):
            slide.source_section_ids = list(target.source_section_ids)
            slide.source_titles = list(target.source_titles)
        slide.manually_edited = False
    return slides[:start] + fresh_slides + slides[end:]
