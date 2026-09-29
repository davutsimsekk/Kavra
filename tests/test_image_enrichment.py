"""app/image_enrichment.py — slaytlara internetten bulunan/yapay zeka ile üretilen
görsel ekleme. Kritik güvenlik varsayımı: model bir görsel URL'ini HALÜSİNE EDEBİLİR
(gerçek bir Gemini çağrısıyla ampirik olarak doğrulandı — gerçekmiş gibi görünen ama
var olmayan bir Wikimedia linki üretti). Bu yüzden testlerin çoğu "indirme başarısız/
geçersiz olursa sessizce yapay zekaya düş, asla çökme" davranışını doğruluyor."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.image_enrichment import (
    _ACCEPTED_CONTENT_TYPES,
    _decide_and_search,
    _download_image,
    _generate_image,
    eligible_for_background,
    eligible_for_enrichment,
    enrich_slides_with_images,
    find_background_for_slide,
    find_image_for_slide,
)
from app.models import Slide


def _text_response(text: str) -> MagicMock:
    return MagicMock(text=text)


def _image_response(data: bytes, mime_type: str = "image/png") -> MagicMock:
    part = MagicMock()
    part.inline_data.data = data
    part.inline_data.mime_type = mime_type
    part.text = None
    return MagicMock(candidates=[MagicMock(content=MagicMock(parts=[part]))])


class DecidePromptSafetyTests(unittest.TestCase):
    def test_generation_branch_explicitly_forbids_technical_diagrams(self):
        # Regresyon: gerçek bir Gemini çağrısında bu kısıtlama yokken model "Pointer
        # Kavramı" için etiketli/sayılı bir "teknik diyagram" ürettirmişti — üretilen
        # görselde geçersiz bir onaltılık değer bile vardı (uydurma bilgi). Bu talimat
        # olmadan tekrar aynı şeye düşülmesin diye bekleniyor.
        from app.image_enrichment import _DECIDE_PROMPT
        self.assertIn("ASLA teknik bir diyagram", _DECIDE_PROMPT)
        self.assertIn("YANLIŞ/UYDURMA bilgi", _DECIDE_PROMPT)

    def test_fallback_generation_prompt_also_forbids_technical_diagrams(self):
        from app.image_enrichment import _fallback_generation_prompt
        prompt = _fallback_generation_prompt(Slide(title="Pointer Kavramı"))
        self.assertIn("NOT a technical diagram", prompt)
        self.assertIn("no numbers", prompt)


class DecideAndSearchTests(unittest.TestCase):
    @patch("google.genai.Client")
    def test_parses_a_real_url_response(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = _text_response(
            "GERÇEK: https://upload.wikimedia.org/wikipedia/commons/x/photo.jpg"
        )
        result = _decide_and_search(Slide(title="x", narration="y"), "key", "model")
        self.assertEqual(result, ("url", "https://upload.wikimedia.org/wikipedia/commons/x/photo.jpg"))

    @patch("google.genai.Client")
    def test_parses_a_generation_prompt_response(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = _text_response(
            "ÜRET: a clean flat illustration of a computer chip"
        )
        result = _decide_and_search(Slide(title="x", narration="y"), "key", "model")
        self.assertEqual(result, ("prompt", "a clean flat illustration of a computer chip"))

    @patch("google.genai.Client")
    def test_none_response_means_no_image_needed(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = _text_response("YOK")
        result = _decide_and_search(Slide(title="x", narration="y"), "key", "model")
        self.assertIsNone(result)

    @patch("google.genai.Client")
    def test_unparseable_response_defaults_to_no_image_not_a_crash(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = _text_response(
            "Bu konuda emin değilim, belki bir görsel olabilir."
        )
        result = _decide_and_search(Slide(title="x", narration="y"), "key", "model")
        self.assertIsNone(result)

    @patch("google.genai.Client")
    def test_empty_text_response_does_not_crash(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = _text_response(None)
        result = _decide_and_search(Slide(title="x", narration="y"), "key", "model")
        self.assertIsNone(result)


class DownloadImageTests(unittest.TestCase):
    def test_accepted_content_types_are_the_ones_pil_and_paste_fitted_image_can_handle(self):
        # SVG kasıtlı olarak dışarıda: PIL açamaz, _paste_fitted_image de raster bekler.
        self.assertNotIn("image/svg+xml", _ACCEPTED_CONTENT_TYPES)

    @patch("requests.get")
    def test_valid_jpeg_is_accepted(self, get):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (10, 10), (255, 0, 0)).save(buf, format="JPEG")
        jpeg_bytes = buf.getvalue()
        get.return_value = MagicMock(status_code=200, headers={"Content-Type": "image/jpeg"}, content=jpeg_bytes)

        result = _download_image("https://example.com/real.jpg")
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "jpg")

    @patch("requests.get")
    def test_hallucinated_url_returning_404_is_rejected(self, get):
        get.return_value = MagicMock(status_code=404, headers={}, content=b"")
        self.assertIsNone(_download_image("https://upload.wikimedia.org/does/not/exist.svg"))

    @patch("requests.get")
    def test_html_error_page_disguised_as_200_is_rejected(self, get):
        # Bazı CDN'ler 404 yerine 200 + HTML hata sayfası döndürür — content-type
        # kontrolü bunu da eler.
        get.return_value = MagicMock(status_code=200, headers={"Content-Type": "text/html"}, content=b"<html>")
        self.assertIsNone(_download_image("https://example.com/fake.jpg"))

    @patch("requests.get")
    def test_svg_content_type_is_rejected(self, get):
        get.return_value = MagicMock(status_code=200, headers={"Content-Type": "image/svg+xml"}, content=b"<svg/>")
        self.assertIsNone(_download_image("https://example.com/diagram.svg"))

    @patch("requests.get")
    def test_content_type_matches_but_bytes_are_not_a_real_image(self, get):
        get.return_value = MagicMock(status_code=200, headers={"Content-Type": "image/jpeg"}, content=b"not-a-real-jpeg")
        self.assertIsNone(_download_image("https://example.com/corrupt.jpg"))

    @patch("requests.get")
    def test_oversized_download_is_rejected(self, get):
        from app import image_enrichment
        get.return_value = MagicMock(
            status_code=200, headers={"Content-Type": "image/jpeg"},
            content=b"x" * (image_enrichment._MAX_DOWNLOAD_BYTES + 100),
        )
        self.assertIsNone(_download_image("https://example.com/huge.jpg"))

    @patch("requests.get", side_effect=Exception("network unreachable"))
    def test_network_error_is_swallowed_not_raised(self, get):
        import requests
        get.side_effect = requests.RequestException("network unreachable")
        self.assertIsNone(_download_image("https://example.com/whatever.jpg"))


class GenerateImageTests(unittest.TestCase):
    @patch("google.genai.Client")
    def test_returns_bytes_and_extension_from_inline_data(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = _image_response(b"fake-png-bytes", "image/png")
        result = _generate_image("a nice illustration", "key", "model")
        self.assertEqual(result, (b"fake-png-bytes", "png"))

    @patch("google.genai.Client")
    def test_no_candidates_returns_none(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = MagicMock(candidates=[])
        self.assertIsNone(_generate_image("prompt", "key", "model"))


class FindImageForSlideTests(unittest.TestCase):
    @patch("app.image_enrichment._download_image")
    @patch("app.image_enrichment._decide_and_search")
    def test_valid_search_result_is_used_directly(self, decide, download):
        decide.return_value = ("url", "https://example.com/real.jpg")
        download.return_value = (b"real-bytes", "jpg")
        result = find_image_for_slide(Slide(title="x"), "key")
        self.assertEqual(result, (b"real-bytes", "jpg", "search"))

    @patch("app.image_enrichment._generate_image")
    @patch("app.image_enrichment._download_image")
    @patch("app.image_enrichment._decide_and_search")
    def test_hallucinated_url_falls_back_to_generation(self, decide, download, generate):
        # Bu, modülün var olma sebebi olan güvenlik davranışı: arama sonucu indirilemezse
        # (halüsine edilmiş URL) render'ı bozmak yerine yapay zeka üretimine düşülür.
        decide.return_value = ("url", "https://upload.wikimedia.org/fake/nonexistent.svg")
        download.return_value = None
        generate.return_value = (b"generated-bytes", "png")

        result = find_image_for_slide(Slide(title="Pointer Kavramı"), "key")

        self.assertEqual(result, (b"generated-bytes", "png", "generated"))
        generate.assert_called_once()

    @patch("app.image_enrichment._generate_image")
    @patch("app.image_enrichment._decide_and_search")
    def test_generation_prompt_decision_calls_generate_directly(self, decide, generate):
        decide.return_value = ("prompt", "a clean flat illustration")
        generate.return_value = (b"generated-bytes", "png")

        result = find_image_for_slide(Slide(title="x"), "key")

        self.assertEqual(result, (b"generated-bytes", "png", "generated"))
        generate.assert_called_once_with("a clean flat illustration", "key", "gemini-3.1-flash-lite-image")

    @patch("app.image_enrichment._decide_and_search")
    def test_no_image_needed_returns_none(self, decide):
        decide.return_value = None
        self.assertIsNone(find_image_for_slide(Slide(title="x"), "key"))

    @patch("app.image_enrichment._generate_image")
    @patch("app.image_enrichment._download_image")
    @patch("app.image_enrichment._decide_and_search")
    def test_both_search_and_generation_failing_returns_none_not_an_exception(self, decide, download, generate):
        decide.return_value = ("url", "https://example.com/fake.jpg")
        download.return_value = None
        generate.return_value = None
        self.assertIsNone(find_image_for_slide(Slide(title="x"), "key"))


class SlideImageSourceModelTests(unittest.TestCase):
    def test_default_image_source_is_none(self):
        self.assertIsNone(Slide(title="x").image_source)

    def test_round_trips_through_to_dict_and_from_dict(self):
        slide = Slide(title="x", embedded_image="/p.png", image_source="generated")
        self.assertEqual(Slide.from_dict(slide.to_dict()).image_source, "generated")

    def test_old_saved_data_without_image_source_key_defaults_to_none(self):
        old_style = {"title": "Eski proje slaydı", "embeddedImage": "/p.png"}
        self.assertIsNone(Slide.from_dict(old_style).image_source)

    def test_ai_background_round_trips_without_changing_source_page_field(self):
        slide = Slide(title="x", ai_background_image="/background.png")
        restored = Slide.from_dict(slide.to_dict())
        self.assertEqual(restored.ai_background_image, "/background.png")
        self.assertIsNone(restored.background_image)


class EligibilityTests(unittest.TestCase):
    def test_chapter_slide_is_never_eligible(self):
        # _draw_chapter embedded_image'i hiç okumaz -- oraya görsel eklemek boşa gider.
        self.assertFalse(eligible_for_enrichment(Slide(title="x", level="chapter")))

    def test_slide_with_code_is_never_eligible(self):
        # render kod örneğini her zaman önceliklendirir, embedded_image gösterilmez.
        self.assertFalse(eligible_for_enrichment(Slide(title="x", code="int a = 1;")))

    def test_page_mode_slide_is_never_eligible(self):
        self.assertFalse(eligible_for_enrichment(Slide(title="x", background_image="/p.png")))

    def test_slide_with_existing_embedded_image_is_skipped_by_default(self):
        self.assertFalse(eligible_for_enrichment(Slide(title="x", embedded_image="/existing.png")))

    def test_force_allows_overwriting_an_existing_embedded_image(self):
        self.assertTrue(eligible_for_enrichment(Slide(title="x", embedded_image="/existing.png"), force=True))

    def test_plain_topic_slide_is_eligible(self):
        self.assertTrue(eligible_for_enrichment(Slide(title="x", level="topic")))

    def test_support_image_is_not_added_on_top_of_ai_background(self):
        self.assertFalse(eligible_for_enrichment(Slide(title="x", ai_background_image="/bg.png")))

    def test_background_allows_chapter_but_excludes_code_page_and_support_image(self):
        self.assertTrue(eligible_for_background(Slide(title="Bölüm", level="chapter")))
        self.assertFalse(eligible_for_background(Slide(title="Kod", code="int x;")))
        self.assertFalse(eligible_for_background(Slide(title="Sayfa", background_image="/page.png")))
        self.assertFalse(eligible_for_background(Slide(title="Görsel", embedded_image="/image.png")))

    def test_background_force_only_overwrites_an_existing_background(self):
        slide = Slide(title="x", ai_background_image="/old.png")
        self.assertFalse(eligible_for_background(slide))
        self.assertTrue(eligible_for_background(slide, force=True))


class EnrichSlidesWithImagesTests(unittest.TestCase):
    @patch("app.image_enrichment.find_background_for_slide")
    def test_background_mode_writes_separate_asset_and_model_field(self, find_background):
        find_background.return_value = (b"background-bytes", "png", "generated")
        slides = [Slide(title="Bölüm", level="chapter")]
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            with patch("app.pipeline.save_script") as save_script:
                result = enrich_slides_with_images(pdir, slides, "key", mode="background")
            expected = pdir / "assets" / "backgrounds" / "slide_001_background.png"
            self.assertEqual(result[0].ai_background_image, str(expected))
            self.assertIsNone(result[0].embedded_image)
            self.assertTrue(expected.is_file())
            save_script.assert_called_once_with(pdir, slides)

    @patch("app.image_enrichment._generate_image")
    def test_background_prompt_requires_widescreen_negative_space_and_no_text(self, generate):
        generate.return_value = (b"bytes", "png")
        result = find_background_for_slide(
            Slide(title="Yerçekimi", narration="Kütleler birbirini çeker."), "key",
        )
        self.assertEqual(result, (b"bytes", "png", "generated"))
        prompt = generate.call_args.args[0]
        self.assertIn("16:9", prompt)
        self.assertIn("negative space", prompt)
        self.assertIn("No text", prompt)

    @patch("app.image_enrichment.find_image_for_slide")
    def test_only_eligible_slides_are_attempted_and_script_is_saved_incrementally(self, find_image):
        find_image.side_effect = [(b"bytes-1", "png", "search")]  # sadece 1 uygun slayt var

        slides = [
            Slide(title="Bölüm", level="chapter"),
            Slide(title="Kod slaydı", code="int a = 1;"),
            Slide(title="Normal slayt", narration="test"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            with patch("app.pipeline.save_script") as save_script:
                result = enrich_slides_with_images(pdir, slides, "key")
                save_script.assert_called_once_with(pdir, slides)

            self.assertEqual(find_image.call_count, 1)
            self.assertEqual(result[2].embedded_image, str(pdir / "assets" / "images" / "slide_003.png"))
            self.assertEqual(result[2].image_source, "search")
            self.assertIsNone(result[0].embedded_image)
            self.assertIsNone(result[1].embedded_image)
            self.assertTrue((pdir / "assets" / "images" / "slide_003.png").is_file())

    @patch("app.image_enrichment.find_image_for_slide")
    def test_one_slide_failing_does_not_stop_the_rest(self, find_image):
        # Paralel çalıştığından hangi slaydın önce tamamlanacağı garanti değil — bu
        # yüzden side_effect bir ÇAĞRI SIRASI listesi DEĞİL, slaydın kendisine bakan bir
        # fonksiyon: "Birinci" her zaman hata verir, "İkinci" her zaman başarılı olur.
        def fake_find_image(slide, api_key):
            if slide.title == "Birinci":
                raise RuntimeError("API hatası")
            return (b"bytes", "jpg", "generated")

        find_image.side_effect = fake_find_image
        slides = [Slide(title="Birinci"), Slide(title="İkinci")]
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            with patch("app.pipeline.save_script"):
                result = enrich_slides_with_images(pdir, slides, "key")
        self.assertIsNone(result[0].embedded_image)
        self.assertIsNotNone(result[1].embedded_image)

    @patch("app.image_enrichment.find_image_for_slide")
    def test_progress_callback_reports_every_slide_exactly_once(self, find_image):
        find_image.return_value = None
        slides = [Slide(title="A"), Slide(title="B")]
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            with patch("app.pipeline.save_script"):
                enrich_slides_with_images(pdir, slides, "key", progress_cb=lambda *a: calls.append(a))
        # Paralel tamamlanma sırası garanti değil — ama her slayt tam olarak bir kez,
        # doğru "total" (2) ile raporlanmalı. "done" sayaçları da {1, 2} olmalı (sırası
        # tamamlanma sırasına göre değişebilir).
        self.assertEqual(len(calls), 2)
        self.assertEqual({c[0] for c in calls}, {1, 2})
        self.assertEqual({c[1] for c in calls}, {2})
        self.assertEqual({c[2] for c in calls}, {"A", "B"})

    @patch("app.image_enrichment.find_image_for_slide")
    def test_slides_are_actually_enriched_concurrently_not_one_after_another(self, find_image):
        import threading
        import time as time_module

        windows: list[tuple[float, float]] = []
        lock = threading.Lock()

        def fake_find_image(slide, api_key):
            start = time_module.monotonic()
            time_module.sleep(0.15)
            with lock:
                windows.append((start, time_module.monotonic()))
            return None

        find_image.side_effect = fake_find_image
        slides = [Slide(title=f"Slayt {i}", narration="x") for i in range(4)]
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            with patch("app.pipeline.save_script"):
                t0 = time_module.monotonic()
                enrich_slides_with_images(pdir, slides, "key")
                total_wall = time_module.monotonic() - t0

        self.assertEqual(len(windows), 4)
        # Sıralı çalışsaydı toplam süre >= 4 * 0.15 olurdu; paralellik sayesinde belirgin kısa.
        self.assertLess(total_wall, 4 * 0.15 * 0.75, f"toplam süre={total_wall:.2f}s, sıralı gibi görünüyor")
        overlap = any(a_start < b_end and b_start < a_end
                      for i, (a_start, a_end) in enumerate(windows)
                      for b_start, b_end in windows[i + 1:])
        self.assertTrue(overlap, f"pencereler: {windows}")

    @patch("app.image_enrichment.find_image_for_slide")
    def test_save_script_and_record_cost_are_never_called_concurrently(self, find_image):
        # KRİTİK güvenlik testi: script.json/cost_ledger.json'a yazma ASLA aynı anda iki
        # iş parçacığından olmamalı (race condition -> bozuk JSON riski). save_script'i
        # sarmalayıp aynı anda ikinci bir çağrı gelirse tespit ediyoruz.
        import threading

        find_image.side_effect = lambda slide, api_key: (b"bytes", "jpg", "generated")
        slides = [Slide(title=f"Slayt {i}", narration="x") for i in range(6)]
        call_lock = threading.Lock()
        concurrent_calls = {"active": 0, "max_seen": 0}
        real_save_script_calls = []

        def guarded_save_script(pdir, saved_slides):
            with call_lock:
                concurrent_calls["active"] += 1
                concurrent_calls["max_seen"] = max(concurrent_calls["max_seen"], concurrent_calls["active"])
            import time as time_module
            time_module.sleep(0.01)  # yarış durumunu yakalama ihtimalini artırmak için
            real_save_script_calls.append(1)
            with call_lock:
                concurrent_calls["active"] -= 1

        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            with patch("app.pipeline.save_script", side_effect=guarded_save_script):
                enrich_slides_with_images(pdir, slides, "key")

        self.assertEqual(concurrent_calls["max_seen"], 1)
        self.assertEqual(len(real_save_script_calls), 6)


if __name__ == "__main__":
    unittest.main()
