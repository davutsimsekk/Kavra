"""app/vision_narration.py — PDF sayfalarını görüntü+metin birlikte kullanarak anlatan,
metin-tabanlı üretimden ayrı, isteğe bağlı bir mod. Sayfa görüntüsü ayrıştırma sırasında
DEĞİL, bu özellik tetiklendiğinde kaynak PDF'ten ON-DEMAND rasterize edilir."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from app.models import RawSection, Slide
from app.vision_narration import (
    _build_prompt,
    _cache_key,
    _rasterize_page,
    _resolve_source_pdf_path,
    eligible_sections,
    generate_slide_for_page,
    generate_slides_with_vision,
    page_number_from_title,
)


def _make_pdf(path: Path, page_count: int = 3):
    import pymupdf
    doc = pymupdf.open()
    for i in range(page_count):
        page = doc.new_page()
        page.insert_text((72, 72), f"Sayfa {i + 1} metni")
    doc.save(str(path))
    doc.close()


def _slide_json_response(slide_dict: dict) -> MagicMock:
    return MagicMock(text=json.dumps([slide_dict], ensure_ascii=False))


class PageNumberFromTitleTests(unittest.TestCase):
    def test_extracts_leading_page_number(self):
        self.assertEqual(page_number_from_title("3. Giriş Bölümü"), 3)

    def test_handles_multi_digit_page_numbers(self):
        self.assertEqual(page_number_from_title("142. Sonuç"), 142)

    def test_manually_added_slide_without_page_prefix_returns_none(self):
        self.assertIsNone(page_number_from_title("Elle Eklenen Slayt"))

    def test_markdown_style_title_without_number_returns_none(self):
        self.assertIsNone(page_number_from_title("Giriş"))


class BuildPromptTests(unittest.TestCase):
    def test_includes_vision_safety_rules_and_page_content(self):
        section = RawSection(breadcrumb="", title="1. Pointer'lar", text="Pointer anlatımı burada.")
        prompt = _build_prompt(section, neighbor_context="", style_note="")
        self.assertIn("GÖRÜNTÜYE BAK", prompt)
        self.assertIn("ASLA TAHMİN ETME", prompt)
        self.assertIn("Pointer'lar", prompt)
        self.assertIn("Pointer anlatımı burada.", prompt)

    def test_includes_neighbor_context_when_given(self):
        section = RawSection(breadcrumb="", title="1. x", text="y")
        prompt = _build_prompt(section, neighbor_context="Önceki sayfada değişkenler anlatıldı.", style_note="")
        self.assertIn("Önceki sayfada değişkenler anlatıldı.", prompt)

    def test_omits_context_block_when_empty(self):
        section = RawSection(breadcrumb="", title="1. x", text="y")
        prompt = _build_prompt(section, neighbor_context="", style_note="")
        self.assertNotIn("BAĞLAM", prompt)


class RasterizePageTests(unittest.TestCase):
    def test_rasterizes_and_caches_a_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "kaynak.pdf"
            _make_pdf(pdf_path, page_count=2)
            pdir = tmp_path / "proje"
            pdir.mkdir()

            image_path = _rasterize_page(pdf_path, 1, pdir)
            self.assertTrue(image_path.is_file())
            with Image.open(image_path) as img:
                self.assertGreater(img.width, 0)

    def test_cached_page_is_not_rasterized_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "kaynak.pdf"
            _make_pdf(pdf_path, page_count=2)
            pdir = tmp_path / "proje"
            pdir.mkdir()

            first = _rasterize_page(pdf_path, 1, pdir)
            first_bytes = first.read_bytes()
            with patch("pymupdf.open") as fake_open:
                second = _rasterize_page(pdf_path, 1, pdir)
                fake_open.assert_not_called()
            self.assertEqual(second.read_bytes(), first_bytes)

    def test_out_of_range_page_number_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "kaynak.pdf"
            _make_pdf(pdf_path, page_count=2)
            pdir = tmp_path / "proje"
            pdir.mkdir()

            with self.assertRaises(ValueError):
                _rasterize_page(pdf_path, 99, pdir)


class GenerateSlideForPageTests(unittest.TestCase):
    def test_missing_api_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "p.png"
            Image.new("RGB", (10, 10)).save(image_path)
            with self.assertRaises(ValueError):
                generate_slide_for_page(image_path, RawSection(breadcrumb="", title="1. x", text="y"),
                                        "", "", api_key="")

    @patch("google.genai.Client")
    def test_returns_a_slide_from_the_json_response(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = _slide_json_response(
            {"title": "Pointer'lar", "layout": "definition",
             "bullets": ["Pointer: Bir adres tutan değişken"], "narration": "x", "level": "topic"}
        )
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "p.png"
            Image.new("RGB", (10, 10)).save(image_path)
            slide = generate_slide_for_page(
                image_path, RawSection(breadcrumb="", title="1. x", text="y"), "", "", api_key="key",
            )
        self.assertEqual(slide.title, "Pointer'lar")
        self.assertEqual(slide.layout, "definition")

    @patch("google.genai.Client")
    def test_empty_json_array_response_raises(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = MagicMock(text="[]")
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "p.png"
            Image.new("RGB", (10, 10)).save(image_path)
            with self.assertRaises(ValueError):
                generate_slide_for_page(image_path, RawSection(breadcrumb="", title="1. x", text="y"),
                                        "", "", api_key="key")

    @patch("time.sleep", return_value=None)
    @patch("google.genai.Client")
    def test_retries_on_rate_limit_then_succeeds(self, client_cls, _sleep):
        from google.genai import errors as genai_errors
        rate_limit_error = genai_errors.ClientError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}})
        client_cls.return_value.models.generate_content.side_effect = [
            rate_limit_error,
            _slide_json_response({"title": "x", "narration": "y"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "p.png"
            Image.new("RGB", (10, 10)).save(image_path)
            slide = generate_slide_for_page(image_path, RawSection(breadcrumb="", title="1. x", text="y"),
                                            "", "", api_key="key")
        self.assertEqual(slide.title, "x")
        self.assertEqual(client_cls.return_value.models.generate_content.call_count, 2)


class EligibleSectionsTests(unittest.TestCase):
    def test_only_sections_with_a_page_number_prefix_are_eligible(self):
        sections = [
            RawSection(breadcrumb="", title="1. Giriş", text="x"),
            RawSection(breadcrumb="", title="Elle eklenen", text="x"),
            RawSection(breadcrumb="", title="2. Devam", text="x"),
        ]
        result = eligible_sections(Path("."), sections)
        self.assertEqual([s.title for s in result], ["1. Giriş", "2. Devam"])


class ResolveSourcePdfPathForCourseProjectsTests(unittest.TestCase):
    """Ders (course) projelerindeki video çalışma alanlarının KENDİ source_path.txt'i
    yok — bkz. app/course_projects.py import_source/create_video, hiçbiri bunu yazmıyor.
    Bu yüzden bu özellik eklenmeden önce course projelerinde görsel tabanlı anlatım HER
    ZAMAN "kaynak dosya yolu bulunamadı" hatası veriyordu, gerçek bir kullanıcı raporuyla
    bulundu. _resolve_source_pdf_path video.json'daki sourceIds üzerinden proje kökündeki
    <proje>/sources/<id>/document.* dosyasına geri gidebilmeli."""

    def _course_project(self, tmp: Path):
        pdir = tmp / "ders-proje"
        pdir.mkdir()
        (pdir / "sources").mkdir()
        (pdir / "videos").mkdir()
        return pdir

    def _add_source(self, pdir: Path, source_id: str, filename: str, source_type: str) -> Path:
        sdir = pdir / "sources" / source_id
        sdir.mkdir()
        document = sdir / filename
        document.write_bytes(b"x")
        (sdir / "source.json").write_text(
            json.dumps({"id": source_id, "filename": filename, "type": source_type}),
            encoding="utf-8",
        )
        return document

    def _video_dir(self, pdir: Path, video_id: str, source_ids: list[str]) -> Path:
        vdir = pdir / "videos" / video_id
        vdir.mkdir()
        (vdir / "video.json").write_text(json.dumps({"id": video_id, "sourceIds": source_ids}), encoding="utf-8")
        return vdir

    def test_legacy_source_path_txt_takes_priority_over_video_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._course_project(Path(tmp))
            document = self._add_source(pdir, "aaaaaaaaaaaa", "document.pdf", "pdf")
            vdir = self._video_dir(pdir, "bbbbbbbbbbbb", ["aaaaaaaaaaaa"])
            legacy_path = Path(tmp) / "baska_bir_kaynak.pdf"
            (vdir / "source_path.txt").write_text(str(legacy_path), encoding="utf-8")
            self.assertEqual(_resolve_source_pdf_path(vdir), legacy_path)
            self.assertNotEqual(_resolve_source_pdf_path(vdir), document)

    def test_single_pdf_source_resolves_to_its_immutable_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._course_project(Path(tmp))
            document = self._add_source(pdir, "aaaaaaaaaaaa", "document.pdf", "pdf")
            vdir = self._video_dir(pdir, "bbbbbbbbbbbb", ["aaaaaaaaaaaa"])
            self.assertEqual(_resolve_source_pdf_path(vdir), document)

    def test_video_with_only_non_pdf_sources_raises_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._course_project(Path(tmp))
            self._add_source(pdir, "aaaaaaaaaaaa", "document.md", "md")
            vdir = self._video_dir(pdir, "bbbbbbbbbbbb", ["aaaaaaaaaaaa"])
            with self.assertRaises(ValueError) as raised:
                _resolve_source_pdf_path(vdir)
            self.assertIn("uygun bir PDF bulunamadı", str(raised.exception))

    def test_video_with_two_pdf_sources_refuses_rather_than_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._course_project(Path(tmp))
            self._add_source(pdir, "aaaaaaaaaaaa", "document.pdf", "pdf")
            self._add_source(pdir, "cccccccccccc", "document.pdf", "pdf")
            vdir = self._video_dir(pdir, "bbbbbbbbbbbb", ["aaaaaaaaaaaa", "cccccccccccc"])
            with self.assertRaises(ValueError) as raised:
                _resolve_source_pdf_path(vdir)
            self.assertIn("birden fazla PDF kaynağından", str(raised.exception))

    def test_video_with_one_pdf_and_one_non_pdf_source_resolves_to_the_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._course_project(Path(tmp))
            document = self._add_source(pdir, "aaaaaaaaaaaa", "document.pdf", "pdf")
            self._add_source(pdir, "cccccccccccc", "document.md", "md")
            vdir = self._video_dir(pdir, "bbbbbbbbbbbb", ["aaaaaaaaaaaa", "cccccccccccc"])
            self.assertEqual(_resolve_source_pdf_path(vdir), document)

    def test_neither_source_path_txt_nor_video_json_raises_original_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proje"
            pdir.mkdir()
            with self.assertRaises(ValueError) as raised:
                _resolve_source_pdf_path(pdir)
            self.assertIn("kaynak dosya yolu bulunamadı", str(raised.exception))

    def test_end_to_end_generate_slides_with_vision_works_for_a_course_video(self):
        """Gerçek bir uçtan uca senaryo: gerçek bir PDF, gerçek rasterize, sahte model
        yanıtı — course projesindeki bir videonun görsel tabanlı anlatımı artık gerçekten
        çalışıyor (eskiden HER ZAMAN "kaynak dosya yolu bulunamadı" hatası veriyordu)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdir = self._course_project(tmp_path)
            sdir = pdir / "sources" / "aaaaaaaaaaaa"
            sdir.mkdir()
            _make_pdf(sdir / "document.pdf", page_count=1)
            (sdir / "source.json").write_text(
                json.dumps({"id": "aaaaaaaaaaaa", "filename": "document.pdf", "type": "pdf"}),
                encoding="utf-8",
            )
            vdir = pdir / "videos" / "bbbbbbbbbbbb"
            vdir.mkdir()
            (vdir / "video.json").write_text(
                json.dumps({"id": "bbbbbbbbbbbb", "sourceIds": ["aaaaaaaaaaaa"]}), encoding="utf-8"
            )
            sections = [RawSection(breadcrumb="", title="1. Giriş", text="Sayfa metni")]

            with patch("app.vision_narration.generate_slide_for_page") as generate_slide:
                generate_slide.return_value = Slide(title="Üretilen", narration="Anlatım")
                slides = generate_slides_with_vision(vdir, sections, "key")

            self.assertEqual(len(slides), 1)
            self.assertEqual(slides[0].title, "Üretilen")


class GenerateSlidesWithVisionTests(unittest.TestCase):
    def _project(self, tmp: Path, pdf_path: Path, sections: list[RawSection]):
        pdir = tmp / "proje"
        pdir.mkdir()
        (pdir / "source_path.txt").write_text(str(pdf_path), encoding="utf-8")
        return pdir

    def test_missing_source_path_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proje"
            pdir.mkdir()
            with self.assertRaises(ValueError):
                generate_slides_with_vision(pdir, [], "key")

    def test_non_pdf_source_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdir = tmp_path / "proje"
            pdir.mkdir()
            (pdir / "source_path.txt").write_text(str(tmp_path / "kaynak.md"), encoding="utf-8")
            with self.assertRaises(ValueError):
                generate_slides_with_vision(pdir, [], "key")

    def test_missing_pdf_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdir = tmp_path / "proje"
            pdir.mkdir()
            (pdir / "source_path.txt").write_text(str(tmp_path / "olmayan.pdf"), encoding="utf-8")
            with self.assertRaises(ValueError):
                generate_slides_with_vision(pdir, [], "key")

    @patch("app.vision_narration.generate_slide_for_page")
    def test_cache_hit_does_not_record_a_request(self, generate_slide):
        from app.cost_ledger import summarize_project

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "kaynak.pdf"
            _make_pdf(pdf_path, page_count=1)
            sections = [RawSection(breadcrumb="", title="1. Giriş", text="metin")]
            pdir = self._project(tmp_path, pdf_path, sections)

            generate_slide.return_value = Slide(title="x", narration="y")
            generate_slides_with_vision(pdir, sections, "key")
            requests_after_first = summarize_project(pdir)["byProvider"]["gemini-vision"]["requests"]
            self.assertEqual(requests_after_first, 1)

            generate_slides_with_vision(pdir, sections, "key")  # ikinci çalıştırma -> önbellek isabeti
            requests_after_second = summarize_project(pdir)["byProvider"]["gemini-vision"]["requests"]
            self.assertEqual(requests_after_second, 1)  # artmadı

    @patch("app.vision_narration.generate_slide_for_page")
    def test_cached_page_skips_the_api_call(self, generate_slide):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "kaynak.pdf"
            _make_pdf(pdf_path, page_count=1)
            sections = [RawSection(breadcrumb="", title="1. Giriş", text="metin")]
            pdir = self._project(tmp_path, pdf_path, sections)

            generate_slide.return_value = Slide(title="Üretilen", narration="x")
            first = generate_slides_with_vision(pdir, sections, "key")
            self.assertEqual(generate_slide.call_count, 1)
            self.assertEqual(first[0].title, "Üretilen")

            second = generate_slides_with_vision(pdir, sections, "key")
            self.assertEqual(generate_slide.call_count, 1)  # ikinci çalıştırmada artmadı
            self.assertEqual(second[0].title, "Üretilen")

    @patch("app.vision_narration.generate_slide_for_page")
    def test_one_page_failing_falls_back_to_a_plain_text_slide_not_a_crash(self, generate_slide):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "kaynak.pdf"
            _make_pdf(pdf_path, page_count=2)
            sections = [
                RawSection(breadcrumb="", title="1. Birinci", text="birinci metin"),
                RawSection(breadcrumb="", title="2. İkinci", text="ikinci metin"),
            ]
            pdir = self._project(tmp_path, pdf_path, sections)

            generate_slide.side_effect = [RuntimeError("API hatası"), Slide(title="İkinci Üretildi", narration="x")]
            result = generate_slides_with_vision(pdir, sections, "key")

            self.assertEqual(result[0].title, "1. Birinci")  # yedek: bölüm başlığı+metni
            self.assertEqual(result[0].narration, "birinci metin")
            self.assertEqual(result[1].title, "İkinci Üretildi")

    @patch("app.vision_narration.generate_slide_for_page")
    def test_source_section_ids_are_tagged_for_regeneration(self, generate_slide):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "kaynak.pdf"
            _make_pdf(pdf_path, page_count=1)
            sections = [RawSection(breadcrumb="", title="1. Giriş", text="metin")]
            pdir = self._project(tmp_path, pdf_path, sections)

            generate_slide.return_value = Slide(title="x", narration="y")
            result = generate_slides_with_vision(pdir, sections, "key")
            self.assertTrue(result[0].source_section_ids)

    @patch("app.vision_narration.generate_slide_for_page")
    def test_progress_callback_reports_done_total_and_title(self, generate_slide):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "kaynak.pdf"
            _make_pdf(pdf_path, page_count=2)
            sections = [
                RawSection(breadcrumb="", title="1. A", text="a"),
                RawSection(breadcrumb="", title="2. B", text="b"),
            ]
            pdir = self._project(tmp_path, pdf_path, sections)
            generate_slide.return_value = Slide(title="x", narration="y")
            calls = []
            generate_slides_with_vision(pdir, sections, "key", progress_cb=lambda *a: calls.append(a))
            self.assertEqual(calls, [(1, 2, "1. A"), (2, 2, "2. B")])


if __name__ == "__main__":
    unittest.main()
