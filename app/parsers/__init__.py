from pathlib import Path

from app.models import RawSection


def parse_source(path: str | Path) -> list[RawSection]:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".md":
        from app.parsers.md_parser import parse as p
    elif ext == ".pptx":
        from app.parsers.pptx_parser import parse as p
    elif ext == ".pdf":
        from app.parsers.pdf_parser import parse as p
    else:
        raise ValueError(f"Desteklenmeyen dosya türü: {ext} (.md, .pptx, .pdf destekleniyor)")
    return p(path)
