import tempfile
import unittest
from pathlib import Path

from app.models import Slide
from app.render_estimate import estimate_render


class RenderEstimateTests(unittest.TestCase):
    def test_local_provider_reports_free_and_no_cost_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            slides = [Slide(title="a", narration="on iki kelime " * 6)]

            estimate = estimate_render(pdir, slides, "edge")

            self.assertTrue(estimate["providerIsLocal"])
            self.assertIn("Yerel/ücretsiz", estimate["providerCostNote"])
            self.assertEqual(estimate["slidesReusable"], 0)
            self.assertEqual(estimate["slidesToRender"], 1)

    def test_cloud_provider_never_claims_an_exact_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            estimate = estimate_render(pdir, [Slide(title="a", narration="x")], "elevenlabs")

            self.assertFalse(estimate["providerIsLocal"])
            self.assertNotRegex(estimate["providerCostNote"], r"\$\d")  # asla uydurma fiyat yok
            self.assertIn("kotanı", estimate["providerCostNote"])

    def test_disk_estimate_uses_real_existing_segment_sizes_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            assets = pdir / "assets"
            assets.mkdir()
            (assets / "segment_001.mp4").write_bytes(b"x" * (1024 * 1024 * 4))  # 4MB gerçek örnek
            slides = [Slide(title="a", narration="x"), Slide(title="b", narration="y")]

            estimate = estimate_render(pdir, slides, "edge")

            # 1 slayt zaten hazır (4MB'lık örnek), 1 slayt eksik -> tahmin ~4MB olmalı
            self.assertEqual(estimate["slidesToRender"], 1)
            self.assertAlmostEqual(estimate["estimatedDiskMb"], 4.0, delta=0.2)

    def test_word_count_and_minutes_scale_with_narration_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            slides = [Slide(title="a", narration=" ".join(["kelime"] * 132))]

            estimate = estimate_render(pdir, slides, "piper")

            self.assertEqual(estimate["wordsTotal"], 132)
            self.assertEqual(estimate["estimatedMinutes"], 1.0)


if __name__ == "__main__":
    unittest.main()
