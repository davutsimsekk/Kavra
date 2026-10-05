"""Tam AI arka plan (Slide.ai_background_full) — "yalnızca yazıları ekle, geri kalan tüm slayt
(eski beyaz içerik paneli dahil) yapay zeka olsun" isteği. Üç parça: (1) üretim/uygunluk/dosya
yönetimi (app/image_enrichment.py), (2) render (app/video/slide_renderer.py — panelsiz, buzlu cam,
görsele göre otomatik açık/koyu yazı), (3) API + render önbellek imzası."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

import studio_web.api as api_module
from app.image_enrichment import (
    ENRICHMENT_MODES,
    MAX_FULL_BACKGROUND_ATTEMPTS,
    _contains_text_or_panels,
    _full_background_generation_prompt,
    _visual_concept,
    eligibility_for_mode,
    eligible_for_background,
    eligible_for_full_background,
    enrich_slides_with_images,
    find_background_for_slide,
)
from app.models import Slide
from app.pipeline import _slide_hash
from app.config import VideoOptions
from app.video import slide_renderer as sr
from app.video.slide_renderer import render_slide
from studio_web.api import app


class FullBackgroundModelTests(unittest.TestCase):
    def test_flag_round_trips_and_defaults_to_false_for_old_scripts(self):
        slide = Slide(title="x", ai_background_image="a.png", ai_background_full=True)
        self.assertTrue(Slide.from_dict(slide.to_dict()).ai_background_full)
        old_script = {"title": "x", "aiBackgroundImage": "a.png"}
        self.assertFalse(Slide.from_dict(old_script).ai_background_full)


class FullBackgroundPromptTests(unittest.TestCase):
    def test_prompt_demands_a_calm_center_and_forbids_panels_and_all_text(self):
        prompt = _full_background_generation_prompt(Slide(title="Sinyal iletimi"), "Flowing luminous waves")
        self.assertIn("16:9", prompt)
        self.assertIn("central ~70%", prompt)
        self.assertIn("outer edges and corners", prompt)
        # Model kendi "beyaz kutusunu" çizip renderer'ın okunabilirlik katmanıyla çakışmasın.
        for forbidden in ("panel", "card", "frame", "white box", "ABSOLUTELY NO TEXT", "ghosted"):
            self.assertIn(forbidden, prompt)

    def test_slide_title_and_narration_never_appear_verbatim_in_the_image_prompt(self):
        """Gerçek üretimde başlık tırnak içinde verilince model onu görselin ortasına hayalet yazı
        olarak çizdi ve sahte etiketli bir diyagram+çerçeve ekledi (iki denemenin ikisinde de)."""
        slide = Slide(title="Sıfırdan FreeRTOS Projesi Oluşturma Adımları",
                      narration="Önce yalın bir temel proje oluştururuz ve çekirdek dosyalarını ekleriz.")
        prompt = _full_background_generation_prompt(slide, "Soft glowing circuit-like light trails")
        self.assertNotIn("FreeRTOS", prompt)
        self.assertNotIn("Oluşturma", prompt)
        self.assertNotIn("temel proje", prompt)
        self.assertIn("Soft glowing circuit-like light trails", prompt)

    def test_without_a_concept_the_prompt_falls_back_to_purely_abstract(self):
        self.assertIn("Purely abstract", _full_background_generation_prompt(Slide(title="x"), None))


class VisualConceptTests(unittest.TestCase):
    @patch("app.image_enrichment._client")
    def test_returns_a_single_cleaned_sentence(self, client):
        client.return_value.models.generate_content.return_value = MagicMock(
            text="  Soft teal light\nflowing across layered translucent shapes.  ")
        usage = {}
        concept = _visual_concept(Slide(title="T", narration="N"), "key", "model", usage)
        self.assertEqual(concept, "Soft teal light flowing across layered translucent shapes.")
        self.assertEqual(usage["requests"], 1)

    @patch("app.image_enrichment._client", side_effect=RuntimeError("ağ yok"))
    def test_failure_returns_none_instead_of_stopping_generation(self, _client):
        self.assertIsNone(_visual_concept(Slide(title="T"), "key", "model"))


class TextCheckTests(unittest.TestCase):
    @patch("app.image_enrichment._client")
    def test_yes_means_text_or_panels_were_found(self, client):
        client.return_value.models.generate_content.return_value = MagicMock(text="YES")
        self.assertTrue(_contains_text_or_panels(b"png", "key"))
        client.return_value.models.generate_content.return_value = MagicMock(text="no.")
        self.assertFalse(_contains_text_or_panels(b"png", "key"))

    @patch("app.image_enrichment._client", side_effect=RuntimeError("kota"))
    def test_a_failing_check_never_blocks_generation(self, _client):
        # Kontrol bir güvenlik ağıdır: çağrı başarısızsa görsel kabul edilir (açık-kapı).
        self.assertFalse(_contains_text_or_panels(b"png", "key"))


class FindFullBackgroundFlowTests(unittest.TestCase):
    slide = Slide(title="Başlık", narration="Anlatım")

    @patch("app.image_enrichment._contains_text_or_panels", return_value=False)
    @patch("app.image_enrichment._generate_image", return_value=(b"img", "png"))
    @patch("app.image_enrichment._visual_concept", return_value="Calm teal gradients")
    def test_clean_first_attempt_is_accepted_and_counts_every_api_call(self, concept, generate, check):
        usage = {"requests": 0}
        result = find_background_for_slide(self.slide, "key", full=True, usage=usage)
        self.assertEqual(result, (b"img", "png", "generated"))
        self.assertIn("COMPLETE 16:9", generate.call_args.args[0])
        self.assertIn("Calm teal gradients", generate.call_args.args[0])
        # _visual_concept ve _contains_text_or_panels sahte olduğundan yalnızca görsel çağrısı sayılır.
        self.assertEqual(usage["requests"], 1)

    @patch("app.image_enrichment._contains_text_or_panels", side_effect=[True, True, False])
    @patch("app.image_enrichment._generate_image", return_value=(b"img", "png"))
    @patch("app.image_enrichment._visual_concept", return_value=None)
    def test_image_with_text_is_regenerated_until_a_clean_one_comes_back(self, concept, generate, check):
        result = find_background_for_slide(self.slide, "key", full=True)
        self.assertIsNotNone(result)
        self.assertEqual(generate.call_count, 3)

    @patch("app.image_enrichment._contains_text_or_panels", return_value=True)
    @patch("app.image_enrichment._generate_image", return_value=(b"img", "png"))
    @patch("app.image_enrichment._visual_concept", return_value=None)
    def test_if_every_attempt_has_text_the_slide_stays_without_a_background(self, concept, generate, check):
        """Hayalet yazılı bir arka plan, arka plansız bir slayttan KÖTÜDÜR."""
        self.assertIsNone(find_background_for_slide(self.slide, "key", full=True))
        self.assertEqual(generate.call_count, MAX_FULL_BACKGROUND_ATTEMPTS)

    @patch("app.image_enrichment._contains_text_or_panels", return_value=False)
    @patch("app.image_enrichment._generate_image", side_effect=[None, (b"img", "png")])
    @patch("app.image_enrichment._visual_concept", return_value=None)
    def test_a_response_without_an_image_is_retried(self, concept, generate, check):
        self.assertIsNotNone(find_background_for_slide(self.slide, "key", full=True))
        self.assertEqual(generate.call_count, 2)

    @patch("app.image_enrichment._generate_image", return_value=(b"img", "png"))
    def test_veil_mode_is_unchanged_one_call_no_concept_no_check(self, generate):
        usage = {}
        find_background_for_slide(self.slide, "key", full=False, usage=usage)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(usage["requests"], 1)
        self.assertNotIn("COMPLETE 16:9", generate.call_args.args[0])


class FullBackgroundEligibilityTests(unittest.TestCase):
    def test_mode_is_registered_and_dispatches_to_its_own_eligibility(self):
        self.assertIn("full_background", ENRICHMENT_MODES)
        self.assertIs(eligibility_for_mode("full_background"), eligible_for_full_background)
        self.assertIs(eligibility_for_mode("background"), eligible_for_background)

    def test_same_exclusions_as_the_veil_background(self):
        self.assertFalse(eligible_for_full_background(Slide(title="x", code="int a;")))
        self.assertFalse(eligible_for_full_background(Slide(title="x", background_image="p.png")))
        self.assertFalse(eligible_for_full_background(Slide(title="x", embedded_image="e.png")))
        self.assertTrue(eligible_for_full_background(Slide(title="x", level="chapter")))

    def test_existing_veil_background_can_be_converted_without_force(self):
        veil = Slide(title="x", ai_background_image="old.png", ai_background_full=False)
        self.assertFalse(eligible_for_background(veil))          # eski mod: force ister
        self.assertTrue(eligible_for_full_background(veil))      # yeni mod: dönüştürür

    def test_slide_that_is_already_full_is_skipped_unless_forced(self):
        full = Slide(title="x", ai_background_image="f.png", ai_background_full=True)
        self.assertFalse(eligible_for_full_background(full))
        self.assertTrue(eligible_for_full_background(full, force=True))


class FullBackgroundEnrichmentTests(unittest.TestCase):
    @patch("app.image_enrichment.find_background_for_slide")
    def test_full_mode_sets_the_flag_and_names_the_file_by_content(self, find_background):
        find_background.return_value = (b"full-bytes", "png", "generated")
        slides = [Slide(title="Normal", narration="anlatım")]
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            with patch("app.pipeline.save_script"):
                enrich_slides_with_images(pdir, slides, "key", mode="full_background")
            path = Path(slides[0].ai_background_image)
            self.assertTrue(slides[0].ai_background_full)
            self.assertTrue(path.is_file())
            self.assertIn("_background_full_", path.name)
            self.assertEqual(find_background.call_args.kwargs["full"], True)

    @patch("app.image_enrichment.find_background_for_slide")
    def test_force_regeneration_gets_a_new_path_so_the_render_cache_cannot_go_stale(self, find_background):
        slides = [Slide(title="Normal", narration="anlatım")]
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            with patch("app.pipeline.save_script"):
                find_background.return_value = (b"first-image", "png", "generated")
                enrich_slides_with_images(pdir, slides, "key", mode="full_background")
                first = slides[0].ai_background_image
                find_background.return_value = (b"second-image", "png", "generated")
                enrich_slides_with_images(pdir, slides, "key", mode="full_background", force=True)
                second = slides[0].ai_background_image
            self.assertNotEqual(first, second)
            # Artık kullanılmayan eski tam-ekran dosyası birikmemeli.
            self.assertFalse(Path(first).exists())
            self.assertTrue(Path(second).exists())

    @patch("app.cost_ledger.record")
    @patch("app.image_enrichment.find_background_for_slide")
    def test_cost_ledger_records_the_real_number_of_api_calls_per_slide(self, find_background, record):
        def fake(slide, api_key, full=False, usage=None):
            usage["requests"] = 5   # konsept + 2 görsel + 2 kontrol
            return b"bytes", "png", "generated"

        find_background.side_effect = fake
        slides = [Slide(title="x", narration="y")]
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            with patch("app.pipeline.save_script"):
                enrich_slides_with_images(pdir, slides, "key", mode="full_background")
        self.assertEqual(record.call_args.kwargs["requests"], 5)
        self.assertEqual(record.call_args.kwargs["kind"], "image_background_full")

    @patch("app.image_enrichment.find_background_for_slide")
    def test_veil_mode_clears_the_flag_when_replacing_a_full_background(self, find_background):
        find_background.return_value = (b"veil-bytes", "png", "generated")
        slide = Slide(title="x", narration="y", ai_background_image="old.png", ai_background_full=True)
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            with patch("app.pipeline.save_script"):
                enrich_slides_with_images(pdir, [slide], "key", mode="background", force=True)
            self.assertFalse(slide.ai_background_full)


def _solid(path: Path, color, size=(640, 360)):
    Image.new("RGB", size, color).save(path)


class FullBackgroundRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _render(self, slide: Slide, name="out.png", **kwargs) -> Image.Image:
        out = self.dir / name
        render_slide(slide, 1, 3, "TEST", out, width=1920, height=1080, theme_preset="notebook", **kwargs)
        return Image.open(out).convert("RGB")

    def test_no_white_content_panel_is_drawn_over_the_art(self):
        art = self.dir / "teal.png"
        _solid(art, (20, 120, 150))
        base = dict(title="Başlık", layout="process", bullets=["bir", "iki", "üç"], ai_background_image=str(art))
        veil = self._render(Slide(**base), "veil.png")
        full = self._render(Slide(**base, ai_background_full=True), "full.png")
        # İçerik panelinin ortasında (yazı olmayan boş bir nokta): eski modda panel orayı açık
        # renge boyar, tam modda sanatın kendi rengi kalır.
        point = (1500, 640)
        self.assertGreater(sum(veil.getpixel(point)), sum(full.getpixel(point)) + 150)

    def test_art_stays_visible_in_the_corners(self):
        art = self.dir / "gradient.png"
        image = Image.new("RGB", (640, 360), (0, 0, 0))
        ImageDraw.Draw(image).rectangle((0, 0, 40, 40), fill=(255, 40, 40))   # sol üst köşede canlı kırmızı
        image.save(art)
        out = self._render(Slide(title="x", bullets=["a"], ai_background_image=str(art), ai_background_full=True))
        r, g, b = out.getpixel((15, 15))
        self.assertGreater(r, 150)
        self.assertLess(g, 110)

    def test_dark_art_switches_to_light_text_and_light_art_to_dark_text(self):
        def title_pixels(art_color):
            art = self.dir / f"art_{art_color[0]}.png"
            _solid(art, art_color)
            out = self._render(Slide(title="MMMMMMMM", bullets=["a"], ai_background_image=str(art),
                                     ai_background_full=True), f"o_{art_color[0]}.png")
            return out.crop((96, 120, 700, 190)).getextrema()   # kanal başına (min, max)

        dark_extremes = sum(band[1] for band in title_pixels((8, 14, 30)))
        light_extremes = sum(band[0] for band in title_pixels((235, 240, 250)))
        self.assertGreater(dark_extremes, 600)   # koyu zeminde en parlak piksel ≈ beyaz yazı
        self.assertLess(light_extremes, 200)     # açık zeminde en koyu piksel ≈ koyu yazı

    def test_missing_art_file_falls_back_to_the_normal_slide_instead_of_crashing(self):
        out = self._render(Slide(title="x", bullets=["a"], ai_background_image=str(self.dir / "yok.png"),
                                 ai_background_full=True))
        self.assertEqual(out.size, (1920, 1080))

    def test_every_layout_and_chapter_renders_over_full_art(self):
        art = self.dir / "mix.png"
        _solid(art, (90, 130, 180))
        cases = [
            Slide(title="b", layout="bullets", bullets=["a", "b"]),
            Slide(title="p", layout="process", bullets=["a", "b", "c"]),
            Slide(title="d", layout="definition", bullets=["Terim: Tanım"]),
            Slide(title="c", layout="comparison", bullets=["A", "x", "---", "B", "y"]),
            Slide(title="e", layout="emphasis", bullets=["Tek vurgu cümlesi"]),
            Slide(title="f", layout="formula", bullets=["F = m*a: Kuvvet"]),
            Slide(title="k", layout="callout", bullets=["UYARI: dikkat"]),
            Slide(title="Bölüm", level="chapter", bullets=["x", "y"]),
        ]
        for slide in cases:
            with self.subTest(layout=slide.layout, level=slide.level):
                slide.ai_background_image = str(art)
                slide.ai_background_full = True
                self.assertEqual(self._render(slide).size, (1920, 1080))

    def test_bullet_reveal_stages_keep_the_full_background(self):
        from dataclasses import replace
        art = self.dir / "reveal.png"
        _solid(art, (20, 120, 150))
        slide = Slide(title="x", bullets=["a", "b", "c"], ai_background_image=str(art), ai_background_full=True)
        partial = replace(slide, bullets=slide.bullets[:1])
        self.assertTrue(partial.ai_background_full)
        self.assertEqual(self._render(partial).size, (1920, 1080))


class ReadabilityHelpersTests(unittest.TestCase):
    def test_contrast_ratio_matches_known_extremes(self):
        self.assertAlmostEqual(sr._contrast_ratio((0, 0, 0), (255, 255, 255)), 21.0, delta=0.5)
        self.assertAlmostEqual(sr._contrast_ratio((120, 120, 120), (120, 120, 120)), 1.0, delta=0.01)

    def test_halo_is_skipped_on_flat_backgrounds_but_used_when_contrast_is_marginal(self):
        # Düz zeminde (rozet, cam kart) halo yalnızca yazıyı bulandırır — gerçek çıktıda rozet
        # rakamlarını ve küçük etiketleri lekeledi.
        self.assertEqual(sr._halo_need(contrast=4.0, busyness=2.0), 0.0)
        self.assertEqual(sr._halo_need(contrast=3.6, busyness=30.0), 1.0)
        self.assertEqual(sr._halo_need(contrast=12.0, busyness=2.0), 0.0)

    def test_low_contrast_text_is_recolored_locally(self):
        # Koyu köşedeki küçük yazı: tema yazı rengi (koyu) orada okunamaz -> açık renge geçer.
        img = Image.new("RGB", (400, 120), (12, 18, 36))
        glass = sr._GlassDraw(img, ImageDraw.Draw(img, "RGBA"))
        font = ImageFont.truetype(str(sr.FONT_BODY), 40)
        glass.text((20, 30), "MMMMMM", fill=(20, 30, 50), font=font)
        brightest = sum(band[1] for band in img.crop((20, 30, 380, 90)).getextrema())
        self.assertGreater(brightest, 600)

    def test_glass_proxy_does_not_crash_when_shapes_hang_off_the_canvas(self):
        img = Image.new("RGB", (300, 200), (100, 120, 140))
        glass = sr._GlassDraw(img, ImageDraw.Draw(img, "RGBA"))
        glass.rounded_rectangle((-50, -40, 120, 90), radius=20, fill=(255, 255, 255), outline=(10, 10, 10))
        glass.rounded_rectangle((250, 150, 400, 300), radius=20, fill=(255, 255, 255))
        font = ImageFont.truetype(str(sr.FONT_BODY), 30)
        glass.text((-10, -5), "taşan yazı", fill=(0, 0, 0), font=font)
        glass.text((280, 190), "taşan yazı", fill=(0, 0, 0), font=font)

    def test_thin_bars_are_not_turned_into_glass(self):
        img = Image.new("RGB", (300, 100), (200, 40, 40))
        glass = sr._GlassDraw(img, ImageDraw.Draw(img, "RGBA"))
        glass.rounded_rectangle((10, 40, 290, 48), radius=4, fill=(20, 200, 20))   # ilerleme çubuğu
        r, g, b = img.getpixel((150, 44))
        self.assertGreater(g, 150)   # opak yeşil kaldı, blur+tint ile yıkanmadı


class FullBackgroundCacheKeyTests(unittest.TestCase):
    def test_flag_changes_the_hash_but_only_when_set(self):
        opts = VideoOptions()
        plain = Slide(title="x", bullets=["a"], ai_background_image="a.png")
        full = Slide(title="x", bullets=["a"], ai_background_image="a.png", ai_background_full=True)
        self.assertNotEqual(
            _slide_hash(plain, "edge", "v", "+0%", opts), _slide_hash(full, "edge", "v", "+0%", opts),
        )

    def test_existing_slides_keep_their_old_hash(self):
        """Alan her slayta koşulsuz yazılsaydı tüm mevcut render önbellekleri geçersiz kalırdı."""
        from app.pipeline import _renderable_signature
        self.assertNotIn("ai_background_full", _renderable_signature(Slide(title="x")))
        self.assertIn("ai_background_full", _renderable_signature(Slide(title="x", ai_background_full=True)))


class FullBackgroundApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")

    def _project(self, slides):
        from tests.test_web_api import _temp_project
        return _temp_project(slides)

    def test_eligible_count_accepts_the_new_mode_and_counts_veil_slides(self):
        slides = [
            Slide(title="a", narration="x"),
            Slide(title="b", narration="x", ai_background_image="o.png", ai_background_full=False),
            Slide(title="c", narration="x", ai_background_image="f.png", ai_background_full=True),
            Slide(title="kod", code="int a;"),
        ]
        with self._project(slides) as pdir:
            response = self.client.get(f"/api/projects/{pdir.name}/enrich-images/eligible-count?mode=full_background")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["eligibleCount"], 2)

    def test_unknown_mode_is_rejected_with_a_message_naming_all_modes(self):
        with self._project([Slide(title="a", narration="x")]) as pdir:
            response = self.client.get(f"/api/projects/{pdir.name}/enrich-images/eligible-count?mode=bogus")
            self.assertEqual(response.status_code, 400)
            self.assertIn("full_background", response.json()["detail"])

    def test_job_reports_the_mode_and_which_slides_got_a_full_background(self):
        def fake_enrich(pdir, slides, api_key, force=False, progress_cb=None, mode="support"):
            slides[0].ai_background_image = str(pdir / "assets" / "backgrounds" / "slide_001_background_full_ab.png")
            slides[0].ai_background_full = True
            return slides

        with self._project([Slide(title="x", narration="y")]) as pdir:
            with patch.object(api_module, "get_api_key", return_value="test-key"), \
                 patch("app.image_enrichment.enrich_slides_with_images", side_effect=fake_enrich):
                response = self.client.post(
                    f"/api/projects/{pdir.name}/enrich-images",
                    headers={"Origin": "http://127.0.0.1:5173"}, json={"mode": "full_background"},
                )
                self.assertEqual(response.status_code, 200)
                job_id = response.json()["jobId"]
                for _ in range(60):
                    job = self.client.get(f"/api/jobs/{job_id}").json()
                    if job["status"] in {"complete", "failed"}:
                        break
                    time.sleep(0.05)
                self.assertEqual(job["status"], "complete", job.get("error"))
                self.assertEqual(job["result"]["mode"], "full_background")
                self.assertEqual(job["result"]["imagesAdded"][0]["source"], "generated")
                self.assertTrue(job["result"]["slides"][0]["aiBackgroundFull"])


if __name__ == "__main__":
    unittest.main()
