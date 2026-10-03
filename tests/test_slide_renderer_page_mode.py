import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from app.models import Slide
from app.video import slide_renderer as sr
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

    def test_missing_background_image_fails_instead_of_silently_redesigning(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "slide_001.png"
            slide = Slide(
                title="Normal slayt", bullets=["madde"], narration="anlatım",
                background_image=str(Path(tmp) / "olmayan.png"),
            )

            with self.assertRaisesRegex(FileNotFoundError, "PDF sayfa görseli"):
                render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)
            self.assertFalse(out_path.exists())



class EmbeddedImageRenderTests(unittest.TestCase):
    def test_embedded_image_is_placed_in_a_band_below_the_full_width_content(self):
        """§10.0.16: görsel artık sağ yarıda değil, tam genişlikli içeriğin ALTINDA bir
        şeritte — bu, "comparison" gibi genişlik gerektiren formatların görsel varken de
        tam olarak render edilebilmesini sağlıyor (bkz. aşağıdaki karşılaştırma testi).
        Piksel rengine bakmak yerine gerçek çağrıyı (_paste_fitted_image'a verilen alan)
        yakalıyoruz — bu, tam sayısal sabitlere bağımlı, kırılgan bir piksel testinden
        daha dayanıklı."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = Image.new("RGB", (500, 400), (30, 120, 200))
            source_path = tmp_path / "diagram.png"
            source.save(source_path)

            out_path = tmp_path / "slide_001.png"
            slide = Slide(
                title="Diyagramlı slayt", bullets=["Madde bir", "Madde iki"],
                narration="anlatım", embedded_image=str(source_path),
            )

            with patch.object(sr, "_paste_fitted_image", wraps=sr._paste_fitted_image) as spy:
                render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)

            (_img, _path, area) = spy.call_args[0]
            x1, y1, x2, y2 = area
            # Bant, slaydın ALT kısmında (üst yarının epey altında) olmalı.
            self.assertGreater(y1, 1080 * 0.55)
            # Tam genişlikli içerik artık sol kenara (106px) kadar iniyor — eski sabit
            # sağ-yarı bölünmesi (x1 >= ~940) artık geçerli değil.
            self.assertLess(x1, 960)

    def test_comparison_layout_renders_as_two_columns_even_with_an_embedded_image(self):
        """Regresyon — bkz. KAVRA_PROJECT_HANDOFF.md §10.0.15/§10.0.16: bu bug bulunup
        düzeltilmeden önce, görsel varken "comparison" HİÇ denenmiyor, sessizce düz madde
        listesine düşüyordu (ve bu fallback bir slaytın en somut maddelerini sessizce
        kaybediyordu). Artık _draw_comparison_layout'un GERÇEKTEN çağrıldığını (bullets'a
        düşmediğini) doğrudan doğruluyoruz."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = Image.new("RGB", (500, 400), (30, 120, 200))
            source_path = tmp_path / "diagram.png"
            source.save(source_path)

            out_path = tmp_path / "slide_001.png"
            slide = Slide(
                title="Stack vs Heap", layout="comparison", narration="anlatım",
                embedded_image=str(source_path),
                bullets=["Yığın (Stack)", "Hızlı erişim", "---", "Öbek (Heap)", "Esnek boyut"],
            )

            with patch.object(sr, "_draw_comparison_layout", wraps=sr._draw_comparison_layout) as spy:
                render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)

            spy.assert_called_once()

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

    def test_definition_layout_with_embedded_image_renders_in_the_left_panel(self):
        # bkz. app/image_enrichment.py — internetten bulunan/üretilen görseller de
        # embedded_image üzerinden gelir; definition gibi diğer metin formatları artık
        # (code ile olduğu gibi) burada da denenir, düz madde listesine zorlanmaz.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = Image.new("RGB", (500, 400), (30, 120, 200))
            source_path = tmp_path / "diagram.png"
            source.save(source_path)

            out_path = tmp_path / "slide_001.png"
            slide = Slide(
                title="Pointer Tanımı", layout="definition", narration="anlatım",
                embedded_image=str(source_path),
                bullets=["Pointer: Bir değişkenin bellekteki adresini tutan değişken"],
            )
            render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)
            with Image.open(out_path) as result:
                self.assertEqual(result.size, (1920, 1080))

    def test_image_label_reflects_image_source(self):
        cases = {
            None: "KAYNAK GÖRSEL",
            "search": "İNTERNETTEN GÖRSEL",
            "generated": "YAPAY ZEKA GÖRSELİ",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = Image.new("RGB", (300, 200), (10, 200, 10))
            source_path = tmp_path / "diagram.png"
            source.save(source_path)

            for image_source, expected_label in cases.items():
                with self.subTest(image_source=image_source):
                    out_path = tmp_path / f"slide_{image_source}.png"
                    slide = Slide(
                        title="x", narration="anlatım", embedded_image=str(source_path),
                        image_source=image_source,
                    )
                    drawn_texts = []
                    original_text = ImageDraw.ImageDraw.text

                    def spy(self, xy, text, *a, **kw):
                        drawn_texts.append(text)
                        return original_text(self, xy, text, *a, **kw)

                    with patch.object(ImageDraw.ImageDraw, "text", spy):
                        render_slide(slide, 1, 1, "", out_path, width=1920, height=1080)
                    self.assertIn(expected_label, drawn_texts)

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
