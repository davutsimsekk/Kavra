import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

from app.config import CACHE_DIR, VideoOptions
from app.models import Slide
from app.pipeline import _slide_hash
from app.video.slide_renderer import render_slide
from app.video.themes import THEMES, THEME_LABELS, resolve_theme_key
from app.video.video_builder import build_segment, ffprobe_duration


class ThemeSelectionTests(unittest.TestCase):
    def test_expected_presets_exist(self):
        self.assertIn("auto", THEME_LABELS)
        self.assertIn("white", THEMES)
        self.assertIn("notebook", THEMES)
        self.assertIn("midnight", THEMES)
        self.assertIn("warm", THEMES)
        self.assertIn("mint", THEMES)
        self.assertIn("aurora", THEMES)

    def test_auto_uses_aurora_for_chapters(self):
        slide = Slide(title="Bölüm", level="chapter")
        self.assertEqual(resolve_theme_key("auto", slide), "aurora")

    def test_auto_uses_dark_theme_for_code(self):
        slide = Slide(title="Kod", code="int main() { return 0; }")
        self.assertEqual(resolve_theme_key("auto", slide), "midnight")

    def test_auto_selection_is_deterministic(self):
        slide = Slide(title="İşletim Sistemlerine Giriş")
        first = resolve_theme_key("auto", slide, 4)
        self.assertEqual(first, resolve_theme_key("auto", slide, 4))

    def test_theme_is_part_of_render_cache_key(self):
        slide = Slide(title="Konu")
        white = VideoOptions(theme_preset="white")
        dark = VideoOptions(theme_preset="midnight")
        self.assertNotEqual(
            _slide_hash(slide, "edge", "voice", "+0%", white),
            _slide_hash(slide, "edge", "voice", "+0%", dark),
        )


class ThemeRenderTests(unittest.TestCase):
    def setUp(self):
        (CACHE_DIR / "tmp").mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=CACHE_DIR / "tmp")
        self.out_dir = Path(self.temp_dir.name)
        self.slide = Slide(
            title="Bellek Yönetimi ve Pointer Mantığı",
            bullets=[
                "Pointer bir değişkenin bellekteki adresini saklar",
                "Doğru yaşam döngüsü bellek hatalarını önler",
                "Kod ile veri arasındaki ilişki görselleştirilir",
            ],
            code="int value = 42;\nint *ptr = &value;\nprintf(\"%d\", *ptr);",
            narration="Test anlatımı",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_all_presets_render_full_hd_images(self):
        for key in THEMES:
            with self.subTest(theme=key):
                out = self.out_dir / f"{key}.png"
                render_slide(self.slide, 2, 8, "C PROGRAMLAMA", out, theme_preset=key)
                self.assertTrue(out.exists())
                with Image.open(out) as image:
                    self.assertEqual(image.size, (1920, 1080))
                    self.assertEqual(image.mode, "RGB")

    def test_white_and_midnight_have_distinct_backgrounds(self):
        white = self.out_dir / "white.png"
        midnight = self.out_dir / "midnight.png"
        render_slide(self.slide, 1, 2, "", white, theme_preset="white")
        render_slide(self.slide, 1, 2, "", midnight, theme_preset="midnight")
        with Image.open(white) as white_img, Image.open(midnight) as dark_img:
            white_pixel = white_img.getpixel((10, 10))
            dark_pixel = dark_img.getpixel((10, 10))
        self.assertGreater(sum(white_pixel), 700)
        self.assertLess(sum(dark_pixel), 150)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg gerekli")
    def test_rendered_slide_builds_real_video_segment(self):
        image_path = self.out_dir / "slide.png"
        audio_path = self.out_dir / "silence.wav"
        video_path = self.out_dir / "segment.mp4"
        render_slide(self.slide, 1, 1, "", image_path, theme_preset="auto")

        with wave.open(str(audio_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(b"\x00\x00" * 44100)

        options = VideoOptions(
            width=640,
            height=360,
            fps=10,
            subtitles=False,
            fade_transitions=True,
            theme_preset="auto",
        )
        build_segment(image_path, audio_path, 1.0, video_path, options)

        self.assertTrue(video_path.exists())
        self.assertGreater(video_path.stat().st_size, 1000)
        self.assertGreater(ffprobe_duration(video_path), 0.8)


if __name__ == "__main__":
    unittest.main()
