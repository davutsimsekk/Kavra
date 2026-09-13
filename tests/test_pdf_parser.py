import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from app.parsers import parse_source
from app.parsers.pdf_parser import parse as parse_pdf

_LONG_TEXT = "Bu sayfa kirk karakterlik esigi rahatca asan uzunlukta bir metin barindiriyor."


def _make_pdf(path: Path, page_texts: list[str | None]) -> None:
    doc = pymupdf.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 100), text, fontsize=18, fontname="helv")
    doc.save(str(path))


def _make_pdf_with_embedded_image(path: Path, text: str | None, image_size=(400, 300),
                                   also_add_small_icon: bool = False) -> None:
    """Sayfaya gerçek, gömülü bir raster görsel ekler (pymupdf insert_image) —
    ayrıştırıcının gerçekten pymupdf'in extract_image API'siyle uyumlu bir
    görsel çıkarabildiğini doğrulamak için."""
    import io

    from PIL import Image

    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 60), text, fontsize=16, fontname="helv")
    picture = Image.new("RGB", image_size, (30, 120, 200))
    buf = io.BytesIO()
    picture.save(buf, format="PNG")
    page.insert_image(pymupdf.Rect(72, 100, 72 + image_size[0], 100 + image_size[1]), stream=buf.getvalue())
    if also_add_small_icon:
        icon = Image.new("RGB", (24, 24), (200, 50, 50))
        icon_buf = io.BytesIO()
        icon.save(icon_buf, format="PNG")
        page.insert_image(pymupdf.Rect(500, 100, 524, 124), stream=icon_buf.getvalue())
    doc.save(str(path))
    doc.close()


class PdfParserNormalModeTests(unittest.TestCase):
    def test_pages_without_text_are_skipped_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf(pdf_path, ["Birinci sayfa", None, "Üçüncü sayfa"])

            sections = parse_pdf(pdf_path)

            self.assertEqual(len(sections), 2)
            self.assertTrue(all(section.page_image is None for section in sections))


class PdfParserPageModeTests(unittest.TestCase):
    def test_page_mode_requires_pdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf(pdf_path, ["Tek sayfa"])
            with self.assertRaises(ValueError):
                parse_pdf(pdf_path, page_mode=True)

    def test_page_mode_keeps_text_free_pages_and_rasterizes_each_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proje"
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf(pdf_path, ["Birinci sayfa", None, "Üçüncü sayfa"])

            sections = parse_pdf(pdf_path, page_mode=True, pdir=pdir)

            self.assertEqual(len(sections), 3)
            for section in sections:
                self.assertIsNotNone(section.page_image)
                self.assertTrue(Path(section.page_image).exists())
            self.assertEqual(
                {Path(s.page_image).name for s in sections},
                {"page_001.png", "page_002.png", "page_003.png"},
            )

    def test_dispatcher_rejects_page_mode_for_non_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "kaynak.md"
            md_path.write_text("# Başlık\nMetin", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_source(md_path, page_mode=True, pdir=Path(tmp))


class PdfParserVisionEnrichTests(unittest.TestCase):
    def test_disabled_by_default_never_calls_vision_captioning(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf(pdf_path, [None, _LONG_TEXT])
            with patch("app.vision_caption.caption_page_image") as fake_caption:
                sections = parse_pdf(pdf_path)
                fake_caption.assert_not_called()
            self.assertEqual(len(sections), 1)

    def test_sparse_text_page_is_captioned_and_merged_into_section_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf(pdf_path, [None])
            with patch("app.vision_caption.caption_page_image") as fake_caption:
                fake_caption.return_value = "Diyagramda A kutusundan B kutusuna bir ok var."
                sections = parse_pdf(pdf_path, vision_enrich=True, vision_api_key="test-key")

            self.assertEqual(fake_caption.call_count, 1)
            self.assertEqual(len(sections), 1)
            self.assertIn("Diyagramda A kutusundan B kutusuna bir ok var.", sections[0].text)

    def test_pages_with_substantial_text_are_not_sent_for_captioning(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf(pdf_path, [_LONG_TEXT])
            with patch("app.vision_caption.caption_page_image") as fake_caption:
                sections = parse_pdf(pdf_path, vision_enrich=True, vision_api_key="test-key")
                fake_caption.assert_not_called()
            # pymupdf insert_text sayfa genişliğini aşan kısmı sessizce kırpabiliyor;
            # burada asıl test edilen "eşiği geçen metin captioning'e gitmiyor" olduğu
            # için tam metin eşitliği yerine sadece eşiği geçtiğini doğruluyoruz.
            self.assertGreaterEqual(len(sections[0].text), 40)
            self.assertNotIn("[Görsel açıklaması]", sections[0].text)

    def test_page_mode_and_vision_enrich_together_set_both_image_and_caption(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proje"
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf(pdf_path, [None])
            with patch("app.vision_caption.caption_page_image") as fake_caption:
                fake_caption.return_value = "Boş bir tahta görüntüsü."
                sections = parse_pdf(
                    pdf_path, page_mode=True, pdir=pdir, vision_enrich=True, vision_api_key="test-key",
                )

            self.assertEqual(len(sections), 1)
            self.assertIsNotNone(sections[0].page_image)
            self.assertTrue(Path(sections[0].page_image).exists())
            self.assertIn("Boş bir tahta görüntüsü.", sections[0].text)

    def test_dispatcher_rejects_vision_enrich_for_non_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "kaynak.md"
            md_path.write_text("# Başlık\nMetin", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_source(md_path, vision_enrich=True, vision_api_key="test-key")


class PdfParserDiagramExtractionTests(unittest.TestCase):
    def test_disabled_by_default_leaves_embedded_image_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf_with_embedded_image(pdf_path, _LONG_TEXT)
            sections = parse_pdf(pdf_path)
            self.assertIsNone(sections[0].embedded_image)

    def test_extracts_real_embedded_raster_image_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proje"
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf_with_embedded_image(pdf_path, _LONG_TEXT)

            sections = parse_pdf(pdf_path, pdir=pdir, extract_diagrams=True)

            self.assertIsNotNone(sections[0].embedded_image)
            image_path = Path(sections[0].embedded_image)
            self.assertTrue(image_path.exists())
            self.assertGreater(image_path.stat().st_size, 0)

    def test_small_icon_sized_images_are_filtered_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proje"
            pdf_path = Path(tmp) / "kaynak.pdf"
            # Sadece küçük bir ikon var, gerçek diyagram boyutunda görsel yok.
            _make_pdf_with_embedded_image(pdf_path, _LONG_TEXT, image_size=(20, 20))

            sections = parse_pdf(pdf_path, pdir=pdir, extract_diagrams=True)

            self.assertIsNone(sections[0].embedded_image)

    def test_largest_qualifying_image_is_chosen_over_a_small_icon(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proje"
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf_with_embedded_image(
                pdf_path, _LONG_TEXT, image_size=(500, 400), also_add_small_icon=True,
            )

            sections = parse_pdf(pdf_path, pdir=pdir, extract_diagrams=True)

            self.assertIsNotNone(sections[0].embedded_image)
            from PIL import Image
            with Image.open(sections[0].embedded_image) as extracted:
                self.assertEqual(extracted.size, (500, 400))

    def test_page_with_no_text_but_a_diagram_is_kept_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proje"
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf_with_embedded_image(pdf_path, text=None)

            sections = parse_pdf(pdf_path, pdir=pdir, extract_diagrams=True)

            self.assertEqual(len(sections), 1)
            self.assertIsNotNone(sections[0].embedded_image)

    def test_page_mode_skips_diagram_extraction_entirely(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proje"
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf_with_embedded_image(pdf_path, _LONG_TEXT)

            sections = parse_pdf(pdf_path, pdir=pdir, page_mode=True, extract_diagrams=True)

            self.assertIsNotNone(sections[0].page_image)
            self.assertIsNone(sections[0].embedded_image)
            self.assertFalse((pdir / "extracted_images").exists())

    def test_requires_pdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "kaynak.pdf"
            _make_pdf_with_embedded_image(pdf_path, _LONG_TEXT)
            with self.assertRaises(ValueError):
                parse_pdf(pdf_path, extract_diagrams=True)

    def test_dispatcher_rejects_extract_diagrams_for_non_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "kaynak.md"
            md_path.write_text("# Başlık\nMetin", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_source(md_path, extract_diagrams=True, pdir=Path(tmp))


if __name__ == "__main__":
    unittest.main()
