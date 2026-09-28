"""Aşamalı madde gösterimi (bkz. VideoOptions.bullet_reveal, app/pipeline.py
_build_bullet_reveal_stages, app/video/video_builder.py build_segment'in reveal_stages dalı).

Zamanlama narrasyon içeriğine değil slaytın toplam ses süresine eşit bölünerek belirlendiği
için burada test edilen "senkron", kelime bazlı vurgulamadaki gibi gerçek narrasyon içeriği
eşleşmesi değil — bkz. modül docstring'leri."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import VideoOptions
from app.models import Slide, SynthResult
from app.pipeline import (
    BULLET_REVEAL_MAX_STAGES,
    _build_bullet_reveal_stages,
    _slide_hash,
    project_dir_for,
    render_video,
    save_script,
)


class BuildBulletRevealStagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stage_dir = Path(self.tmp.name) / "_reveal"

    def test_fewer_than_two_bullets_returns_none(self):
        slide = Slide(title="Tek madde", bullets=["Tek madde"])
        result = _build_bullet_reveal_stages(slide, 1, 1, "", VideoOptions(), 5.0, self.stage_dir)
        self.assertIsNone(result)

    def test_no_bullets_returns_none(self):
        slide = Slide(title="Madde yok")
        result = _build_bullet_reveal_stages(slide, 1, 1, "", VideoOptions(), 5.0, self.stage_dir)
        self.assertIsNone(result)

    def test_too_many_bullets_for_the_duration_returns_none(self):
        slide = Slide(title="Çok madde", bullets=[f"Madde {i}" for i in range(5)])
        # 1.0s / 5 madde = 0.2s/aşama < BULLET_REVEAL_MIN_STAGE_SECONDS (0.6s) -> okunamaz, atla.
        result = _build_bullet_reveal_stages(slide, 1, 1, "", VideoOptions(), 1.0, self.stage_dir)
        self.assertIsNone(result)

    def test_bullet_count_is_capped_at_max_stages(self):
        slide = Slide(title="Çok fazla madde", bullets=[f"Madde {i}" for i in range(10)])
        seen_bullet_counts = []

        def fake_render_slide(slide, index, total, breadcrumb, out_path, **kw):
            seen_bullet_counts.append(len(slide.bullets))
            Path(out_path).write_bytes(b"\x89PNG\r\n")

        with patch("app.pipeline.render_slide", side_effect=fake_render_slide):
            result = _build_bullet_reveal_stages(slide, 1, 1, "", VideoOptions(), 60.0, self.stage_dir)
        self.assertEqual(len(result), BULLET_REVEAL_MAX_STAGES)
        self.assertEqual(seen_bullet_counts, list(range(1, BULLET_REVEAL_MAX_STAGES + 1)))

    def test_stage_durations_split_the_slide_duration_evenly(self):
        slide = Slide(title="Üç madde", bullets=["A", "B", "C"])
        with patch("app.pipeline.render_slide",
                   side_effect=lambda *a, **kw: Path(a[4]).write_bytes(b"x")):
            result = _build_bullet_reveal_stages(slide, 1, 1, "", VideoOptions(), 9.0, self.stage_dir)
        self.assertEqual([round(d, 4) for _, d in result], [3.0, 3.0, 3.0])

    def test_stage_images_are_written_under_the_stage_dir(self):
        slide = Slide(title="İki madde", bullets=["A", "B"])
        result = _build_bullet_reveal_stages(slide, 3, 5, "", VideoOptions(), 4.0, self.stage_dir)
        self.assertEqual(len(result), 2)
        for path, _duration in result:
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, self.stage_dir)


class BulletRevealCacheKeyTests(unittest.TestCase):
    def test_toggling_bullet_reveal_changes_the_render_cache_key(self):
        slide = Slide(title="Konu", bullets=["A", "B"])
        on = VideoOptions(bullet_reveal=True)
        off = VideoOptions(bullet_reveal=False)
        self.assertNotEqual(
            _slide_hash(slide, "edge", "voice", "+0%", on),
            _slide_hash(slide, "edge", "voice", "+0%", off),
        )


class BulletRevealLayoutGatingTests(unittest.TestCase):
    """Aşamalı gösterim "bullets", "process" ve "definition" için tanımlı (madde bir aşamadan
    diğerine sadece BİRİKİR, bkz. app/pipeline.py _REVEALABLE_LAYOUTS) — "comparison" ise
    KASITLI OLARAK dışarıda bırakıldı: "---" ayracı bullets[:k]'ya girmeden önceki aşamalar düz
    madde kartına düşer, ayraç girer girmez görünüm aniden iki sütuna sıçrar (bkz. yorum,
    app/pipeline.py render_one). "callout"/"formula"/"emphasis" zaten hep tek maddeden oluştuğu
    için `len(bullets) >= 2` koşuluyla doğal olarak elenir, ayrı bir test gerekmez."""

    def _reveal_stages_for(self, slide: Slide) -> list | None:
        seen_reveal_stages = []

        def fake_build_segment(img_path, audio_path, duration, seg_path, opts, ass_path=None, reveal_stages=None):
            seen_reveal_stages.append(reveal_stages)
            seg_path.write_bytes(b"fake-segment")

        with tempfile.TemporaryDirectory() as tmp, \
             patch("app.pipeline.PROJECTS_DIR", Path(tmp)), \
             patch("app.pipeline.get_provider", return_value=_FakeSilentProvider()), \
             patch("app.pipeline.build_segment", side_effect=fake_build_segment), \
             patch("app.pipeline.concat_videos"), patch("app.pipeline.concat_audio"):
            pdir = project_dir_for("bullet-reveal-layout-gate")
            save_script(pdir, [slide])
            render_video(pdir, [slide], "edge", "tr-TR-AhmetNeural", "+0%",
                         VideoOptions(subtitles=False, bullet_reveal=True))
        self.assertEqual(len(seen_reveal_stages), 1)
        return seen_reveal_stages[0]

    def test_comparison_layout_never_gets_reveal_stages_even_with_enough_bullets(self):
        slide = Slide(
            title="Karşılaştırma", layout="comparison", narration="Test anlatımı",
            bullets=["Stack", "Hızlı erişim", "---", "Heap", "Esnek boyut"],
        )
        self.assertIsNone(self._reveal_stages_for(slide))

    def test_definition_layout_gets_reveal_stages(self):
        slide = Slide(
            title="Tanımlar", layout="definition", narration="Test anlatımı yeterince uzun burada",
            bullets=["Pointer: Bir adres tutar", "NULL: Geçersiz adres"],
        )
        stages = self._reveal_stages_for(slide)
        self.assertIsNotNone(stages)
        self.assertEqual(len(stages), 2)

    def test_process_layout_gets_reveal_stages(self):
        slide = Slide(
            title="Adımlar", layout="process", narration="Test anlatımı yeterince uzun burada",
            bullets=["Değişkeni tanımla", "Değeri ata", "Sonucu yazdır"],
        )
        stages = self._reveal_stages_for(slide)
        self.assertIsNotNone(stages)
        self.assertEqual(len(stages), 3)

    def test_each_definition_reveal_stage_still_parses_as_valid_definition_pairs(self):
        """Her aşama (bullets[:k]) kendi başına geçerli bir "Terim: Tanım" alt kümesi olmalı —
        aksi halde ara aşamalar beklenmedik şekilde düz madde kartına düşer."""
        from app.video.slide_renderer import _parse_definition_pairs

        slide = Slide(
            title="Tanımlar", layout="definition", narration="Test anlatımı yeterince uzun burada",
            bullets=["A: birinci tanım", "B: ikinci tanım", "C: üçüncü tanım"],
        )
        stages = self._reveal_stages_for(slide)
        for k in range(1, len(slide.bullets) + 1):
            self.assertIsNotNone(_parse_definition_pairs(slide.bullets[:k]))
        self.assertEqual(len(stages), 3)


class _FakeSilentProvider:
    """Gerçek ffmpeg ile 3sn'lik sessiz bir mp3 üretir; gerçek concat/mux yolunu sınamak için."""

    def synthesize(self, text, voice, out_path, rate="+0%"):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "3",
             "-c:a", "libmp3lame", str(out_path)],
            check=True, capture_output=True,
        )
        return SynthResult(duration=3.0, words=None)


@unittest.skipUnless(
    __import__("shutil").which("ffmpeg") and __import__("shutil").which("ffprobe"), "ffmpeg gerekli"
)
class BulletRevealEndToEndTests(unittest.TestCase):
    def test_render_video_with_bullet_reveal_produces_correct_duration(self):
        slide = Slide(
            title="Slayt", narration="Kısa bir anlatım metni",
            bullets=["Birinci madde", "İkinci madde", "Üçüncü madde"],
        )
        with tempfile.TemporaryDirectory() as tmp, \
             patch("app.pipeline.PROJECTS_DIR", Path(tmp)), \
             patch("app.pipeline.get_provider", return_value=_FakeSilentProvider()):
            pdir = project_dir_for("bullet-reveal-e2e")
            save_script(pdir, [slide])
            render_video(pdir, [slide], "edge", "tr-TR-AhmetNeural", "+0%",
                         VideoOptions(subtitles=False, bullet_reveal=True))
            video_path = pdir / "ders.mp4"
            self.assertTrue(video_path.is_file())
            duration = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0",
                 str(video_path)],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        self.assertAlmostEqual(float(duration), 3.0, delta=0.3)


if __name__ == "__main__":
    unittest.main()
