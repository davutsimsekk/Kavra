from pathlib import Path

from app.models import RawSection


def parse_source(path: str | Path, page_mode: bool = False, pdir: Path | None = None,
                  vision_enrich: bool = False, vision_api_key: str | None = None,
                  vision_model: str | None = None, extract_diagrams: bool = False) -> list[RawSection]:
    path = Path(path)
    ext = path.suffix.lower()
    if page_mode and ext != ".pdf":
        raise ValueError(
            "Sayfaları birebir slayt olarak kullanma modu şu an yalnızca PDF için destekleniyor."
        )
    if vision_enrich and ext != ".pdf":
        raise ValueError("Görsel anlama şu an yalnızca PDF için destekleniyor.")
    if extract_diagrams and ext != ".pdf":
        raise ValueError("Diyagram/görsel çıkarma şu an yalnızca PDF için destekleniyor.")
    if ext == ".md":
        from app.parsers.md_parser import parse as p
        return p(path)
    if ext == ".pptx":
        from app.parsers.pptx_parser import parse as p
        return p(path)
    if ext == ".pdf":
        from app.parsers.pdf_parser import parse as p
        return p(
            path, page_mode=page_mode, pdir=pdir, vision_enrich=vision_enrich,
            vision_api_key=vision_api_key, vision_model=vision_model,
            extract_diagrams=extract_diagrams,
        )
    raise ValueError(f"Desteklenmeyen dosya türü: {ext} (.md, .pptx, .pdf destekleniyor)")
