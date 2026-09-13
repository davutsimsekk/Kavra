import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.models import Slide
from app.video.slide_renderer import render_slide


class BackgroundImageRenderTests(unittest.TestCase):
    def test_slide_with_background_image_uses_source_page_untouched_by_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # A4-ish oranda (1240x1754), 16:9 hedef kareden belirgin şekilde farklı
            # — letterbox mantığının gerçekten devreye girdiğini doğrular.
            source = Image.new("RGB", (1240, 1754), (10, 20, 30))
            source_path = tmp_path / "page_001.png"
            source.save(source_path)

            out_path = tmp_path / "slide_001.png"
            slide = Slide(title="Sayfa 1", narration="anlatım", background_image=str(source_path))

            render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)

            self.assertTrue(out_path.exists())
            with Image.open(out_path) as result:
                self.assertEqual(result.size, (1920, 1080))
                # Sağ/sol pillarbox şeritleri siyah kalmalı (kaynak resim ortalanmış).
                self.assertEqual(result.getpixel((2, 540)), (0, 0, 0))

    def test_missing_background_image_falls_back_to_theme_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "slide_001.png"
            slide = Slide(
                title="Normal slayt", bullets=["madde"], narration="anlatım",
                background_image=str(Path(tmp) / "olmayan.png"),
            )

            render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)

            self.assertTrue(out_path.exists())
            with Image.open(out_path) as result:
                self.assertEqual(result.size, (1920, 1080))


class EmbeddedImageRenderTests(unittest.TestCase):
    def test_embedded_image_is_pasted_into_the_right_panel_alongside_bullets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            distinct_color = (30, 120, 200)
            source = Image.new("RGB", (500, 400), distinct_color)
            source_path = tmp_path / "diagram.png"
            source.save(source_path)

            out_path = tmp_path / "slide_001.png"
            slide = Slide(
                title="Diyagramlı slayt", bullets=["Madde bir", "Madde iki"],
                narration="anlatım", embedded_image=str(source_path),
            )

            render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)

            with Image.open(out_path) as result:
                self.assertEqual(result.size, (1920, 1080))
                # Sağ panelde bir yerde kaynak görselin kendine özgü rengi görünmeli.
                right_half = result.crop((1000, 300, 1900, 950))
                colors = right_half.getcolors(maxcolors=1_000_000) or []
                found = any(color == distinct_color for _count, color in colors)
                self.assertTrue(found, "Çıkarılmış görselin rengi sağ panelde bulunamadı")

    def test_code_takes_priority_over_embedded_image_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = Image.new("RGB", (300, 200), (200, 50, 50))
            source_path = tmp_path / "diagram.png"
            source.save(source_path)

            out_path = tmp_path / "slide_001.png"
            slide = Slide(
                title="Kod ve görsel birlikte", bullets=["madde"], code="print('merhaba')",
                narration="anlatım", embedded_image=str(source_path),
            )

            render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)

            with Image.open(out_path) as result:
                self.assertEqual(result.size, (1920, 1080))

    def test_missing_embedded_image_file_falls_back_to_plain_bullets(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "slide_001.png"
            slide = Slide(
                title="Normal slayt", bullets=["madde"], narration="anlatım",
                embedded_image=str(Path(tmp) / "olmayan.png"),
            )

            render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)

            with Image.open(out_path) as result:
                self.assertEqual(result.size, (1920, 1080))

    def test_chapter_slide_with_embedded_image_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = Image.new("RGB", (300, 200), (10, 200, 10))
            source_path = tmp_path / "diagram.png"
            source.save(source_path)

            out_path = tmp_path / "slide_001.png"
            slide = Slide(
                title="Bölüm Başlığı", level="chapter", narration="anlatım",
                embedded_image=str(source_path),
            )

            render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)

            with Image.open(out_path) as result:
                self.assertEqual(result.size, (1920, 1080))


if __name__ == "__main__":
    unittest.main()
