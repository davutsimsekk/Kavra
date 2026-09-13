from pathlib import Path

import pymupdf

from app.models import RawSection

# Bu kadar az metin çıkarılan bir sayfa muhtemelen görsel ağırlıklı (diyagram,
# ekran görüntüsü, fotoğraf) — vision_enrich açıkken bu eşiğin altındaki
# sayfalar Gemini'ye gönderilip bir açıklama alınır. Normal modda (vision_enrich
# kapalı) böyle sayfalar hâlâ olduğu gibi ele alınır (metin yoksa atlanır).
_SPARSE_TEXT_CHARS = 40
_PAGE_IMAGE_DPI = 150
# "Diyagram/görsel çıkar" modunda hangi gömülü görsellerin gerçek bir
# diyagram/grafik olma ihtimali yüksek, hangileri muhtemelen ikon/logo/
# ayraç grafiği — bunları elemek için kaba boyut/oran eşikleri.
_MIN_DIAGRAM_WIDTH = 150
_MIN_DIAGRAM_HEIGHT = 120
_MAX_DIAGRAM_ASPECT_RATIO = 6.0


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


def _rasterize(page, dpi: int = _PAGE_IMAGE_DPI) -> tuple[bytes, "pymupdf.Pixmap"]:
    pixmap = page.get_pixmap(dpi=dpi)
    return pixmap.tobytes("png"), pixmap


def _largest_embedded_diagram(doc: "pymupdf.Document", page) -> tuple[bytes, str] | None:
    """Sayfaya PDF'in kendi içine GÖMÜLÜ olan raster görselleri tarar (pymupdf
    page.get_images) ve boyutça en büyük, ikon/logo olma ihtimali düşük olanı
    ham baytlarıyla döndürür. Hiçbir görsel üretilmiyor — kaynağın kendi
    piksellerinden bire bir bir çıkarım.

    Dönüş: (görsel_baytları, uzantı) ya da uygun aday yoksa None.
    """
    best: tuple[int, int, str] | None = None  # (alan, xref, ext) — sadece boyut karşılaştırması için
    best_bytes: bytes | None = None
    for image_info in page.get_images(full=True):
        xref, width, height = image_info[0], image_info[2], image_info[3]
        if width < _MIN_DIAGRAM_WIDTH or height < _MIN_DIAGRAM_HEIGHT:
            continue
        aspect = max(width, height) / max(min(width, height), 1)
        if aspect > _MAX_DIAGRAM_ASPECT_RATIO:
            continue
        area = width * height
        if best is None or area > best[0]:
            try:
                extracted = doc.extract_image(xref)
            except Exception:
                continue
            best = (area, xref, extracted["ext"])
            best_bytes = extracted["image"]
    if best is None or best_bytes is None:
        return None
    return best_bytes, best[2]


def parse(path: Path, page_mode: bool = False, pdir: Path | None = None,
          vision_enrich: bool = False, vision_api_key: str | None = None,
          vision_model: str | None = None, extract_diagrams: bool = False) -> list[RawSection]:
    """page_mode=True: her sayfayı olduğu gibi (yüksek çözünürlüklü PNG) rasterize
    edip RawSection.page_image'e yazar — "sayfaları birebir slayt olarak kullan"
    özelliği bu görüntüyü render_slide'da doğrudan slayt arka planı yapar. pdir
    (proje dizini) sadece page_mode=True iken gereklidir.

    vision_enrich=True: metni çok az/hiç olmayan (muhtemelen diyagram/ekran
    görüntüsü ağırlıklı) sayfalar Gemini'ye gönderilip metne çevrilir ve
    RawSection.text'e eklenir — anlatım LLM'i (hangi sağlayıcı seçilirse
    seçilsin) resmi hiç görmez, sadece bu METNİ görür (app/vision_caption.py).

    extract_diagrams=True: sayfada PDF'in içine GÖMÜLÜ gerçek bir raster
    görsel (diyagram/grafik) varsa ham haliyle çıkarılıp RawSection.embedded_image'e
    yazılır — hiçbir şey üretilmiyor, page_mode'un aksine sadece o görsel, tüm
    sayfa değil. page_mode ile birlikte anlamsız (tüm sayfa zaten arka plan
    olacağından) — page_mode açıkken atlanır. pdir gerekli.
    """
    if page_mode and pdir is None:
        raise ValueError("page_mode için proje dizini (pdir) gerekli.")
    if extract_diagrams and pdir is None:
        raise ValueError("extract_diagrams için proje dizini (pdir) gerekli.")

    page_dir = None
    if page_mode:
        page_dir = pdir / "source_pages"
        page_dir.mkdir(parents=True, exist_ok=True)

    diagram_dir = None
    if extract_diagrams and not page_mode:
        diagram_dir = pdir / "extracted_images"
        diagram_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(str(path))
    try:
        sections = []
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            is_sparse = len(text) < _SPARSE_TEXT_CHARS
            # Sayfa modunda görsel-ağırlıklı (metni az/yok) sayfalar da slayt
            # olarak kalmalı; vision_enrich ya da extract_diagrams açıkken de
            # aynı şekilde — metni olmasa bile bir görseli/diyagramı olabilir.
            # Hiçbiri açık değilse eski davranış (metin yoksa atla) korunuyor.
            if not text and not page_mode and not vision_enrich and not extract_diagrams:
                continue

            title = _page_title(page) or f"Sayfa {i}"
            page_image = None
            png_bytes = None
            if page_mode or (vision_enrich and is_sparse):
                png_bytes, pixmap = _rasterize(page)
                if page_mode:
                    image_path = page_dir / f"page_{i:03d}.png"
                    pixmap.save(str(image_path))
                    page_image = str(image_path)

            if vision_enrich and is_sparse and png_bytes:
                from app.vision_caption import DEFAULT_VISION_MODEL, caption_page_image

                caption = caption_page_image(
                    png_bytes, api_key=vision_api_key, model=vision_model or DEFAULT_VISION_MODEL,
                )
                text = f"{text}\n\n[Görsel açıklaması]\n{caption}".strip() if text else caption
                if pdir is not None:
                    from app.cost_ledger import record as _record_cost

                    # Gemini bu SDK yolunda görsel isteği başına gerçek maliyeti
                    # bildirmiyor — bu yüzden usd yok, sadece istek sayısı tutuluyor.
                    _record_cost(pdir, provider="gemini-vision", kind="vision_caption", requests=1)

            embedded_image = None
            if diagram_dir is not None:
                found = _largest_embedded_diagram(doc, page)
                if found:
                    image_bytes, ext = found
                    image_path = diagram_dir / f"page_{i:03d}.{ext}"
                    image_path.write_bytes(image_bytes)
                    embedded_image = str(image_path)

            if not text and not page_mode and not embedded_image:
                continue

            sections.append(RawSection(
                breadcrumb="",
                title=f"{i}. {title}",
                text=text,
                code_blocks=[],
                level=3,
                page_image=page_image,
                embedded_image=embedded_image,
            ))
        return sections
    finally:
        # vision_enrich bir sayfada hata verirse (ör. API kotası) doc açık
        # kalmasın — Windows'ta bu, geçici yükleme dosyasının silinememesine
        # (PermissionError) yol açıyordu; bu gece zaten bir kez PIL için aynı
        # sınıf hatayı slide_renderer'da düzelttik.
        doc.close()
