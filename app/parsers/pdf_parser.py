from pathlib import Path

import pymupdf

from app.models import RawSection


def _page_title(page) -> str | None:
    d = page.get_text("dict")
    best = None
    best_size = 0.0
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span["size"] > best_size and span["text"].strip():
                    best_size = span["size"]
                    best = span["text"].strip()
    return best


def parse(path: Path) -> list[RawSection]:
    doc = pymupdf.open(str(path))
    sections = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        if not text:
            continue
        title = _page_title(page) or f"Sayfa {i}"
        sections.append(RawSection(
            breadcrumb="",
            title=f"{i}. {title}",
            text=text,
            code_blocks=[],
            level=3,
        ))
    doc.close()
    return sections
