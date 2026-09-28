"""app.pipeline.render_video'nun 3. geçişi (slayt görseli + segment) artık PASS3_MAX_WORKERS
kadar iş parçacığında paralel çalışıyor. Bu testler: (1) gerçekten eşzamanlı çalıştığını,
(2) tamamlanma sırası ne olursa olsun nihai segment/ses sıralamasının slayt indeksini
koruduğunu, (3) bir slaytın hatasının render'ı durdurup gerçek hatayı yüzeye çıkardığını
doğruluyor. Gerçek ffmpeg/PIL çağrılmaz; render_slide/build_segment/concat_* sahteleniyor
(concat_videos gerçek olsaydı sahte segment baytlarını birleştirmeye çalışıp patlardı)."""
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import VideoOptions
from app.models import Slide, SynthResult
from app.pipeline import project_dir_for, render_video, save_script


def _write_fake_mp3(path: Path, seconds: float = 1.0):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", str(seconds),
         "-c:a", "libmp3lame", str(path)],
        check=True, capture_output=True,
    )


class FakeProvider:
    def synthesize(self, text, voice, out_path, rate="+0%"):
        _write_fake_mp3(Path(out_path), seconds=1.0)
        return SynthResult(duration=1.0, words=None)


def _stems(paths) -> list[str]:
    return [Path(p).stem for p in paths]


class Pass3OrderingAndConcurrencyTests(unittest.TestCase):
    @patch("app.pipeline.concat_audio")
    @patch("app.pipeline.concat_videos")
    @patch("app.pipeline.get_provider")
    def test_final_order_matches_slide_index_even_when_later_slides_finish_first(
        self, get_provider, concat_videos, concat_audio,
    ):
        get_provider.return_value = FakeProvider()
        n = 6
        slides = [Slide(title=f"Slayt {i}", narration=f"Anlatım metni {i}") for i in range(n)]

        # Slayt 0 en yavaş, slayt n-1 en hızlı biter -> tamamlanma sırası ters.
        def fake_render_slide(slide, i, total, breadcrumb, img_path, **kw):
            time.sleep((total - i) * 0.05)
            img_path.write_bytes(b"\x89PNG\r\n")

        def fake_build_segment(img_path, audio_path, duration, seg_path, opts, ass_path=None, reveal_stages=None):
            seg_path.write_bytes(b"fake-segment")

        with tempfile.TemporaryDirectory() as tmp, \
             patch("app.pipeline.PROJECTS_DIR", Path(tmp)), \
             patch("app.pipeline.render_slide", side_effect=fake_render_slide), \
             patch("app.pipeline.build_segment", side_effect=fake_build_segment):
            pdir = project_dir_for("pass3-siralama")
            save_script(pdir, slides)
            render_video(pdir, slides, "edge", "tr-TR-AhmetNeural", "+0%", VideoOptions())

        concat_videos.assert_called_once()
        segment_paths = concat_videos.call_args[0][0]
        self.assertEqual(_stems(segment_paths), [f"segment_{i + 1:03d}" for i in range(n)])
        audio_paths = concat_audio.call_args[0][0]
        self.assertEqual(len(audio_paths), n)

    @patch("app.pipeline.concat_audio")
    @patch("app.pipeline.concat_videos")
    @patch("app.pipeline.get_provider")
    def test_slides_actually_overlap_in_time_not_run_one_after_another(
        self, get_provider, concat_videos, concat_audio,
    ):
        get_provider.return_value = FakeProvider()
        n = 4
        slides = [Slide(title=f"Slayt {i}", narration=f"Metin {i}") for i in range(n)]
        windows: list[tuple[float, float]] = []
        lock = threading.Lock()

        def fake_render_slide(slide, i, total, breadcrumb, img_path, **kw):
            start = time.monotonic()
            time.sleep(0.15)
            with lock:
                windows.append((start, time.monotonic()))
            img_path.write_bytes(b"\x89PNG\r\n")

        def fake_build_segment(img_path, audio_path, duration, seg_path, opts, ass_path=None, reveal_stages=None):
            seg_path.write_bytes(b"fake-segment")

        with tempfile.TemporaryDirectory() as tmp, \
             patch("app.pipeline.PROJECTS_DIR", Path(tmp)), \
             patch("app.pipeline.render_slide", side_effect=fake_render_slide), \
             patch("app.pipeline.build_segment", side_effect=fake_build_segment):
            pdir = project_dir_for("pass3-eszamanli")
            save_script(pdir, slides)
            t0 = time.monotonic()
            render_video(pdir, slides, "edge", "tr-TR-AhmetNeural", "+0%", VideoOptions())
            total_wall = time.monotonic() - t0

        self.assertEqual(len(windows), n)
        # Sıralı çalışsaydı toplam süre >= n * 0.15 olurdu; paralellik sayesinde belirgin kısa.
        self.assertLess(total_wall, n * 0.15 * 0.75, f"toplam süre={total_wall:.2f}s, sıralı gibi görünüyor")
        # En az iki pencere gerçekten çakışmalı (biri başlamadan öteki bitmemiş).
        overlap = any(a_start < b_end and b_start < a_end
                      for i, (a_start, a_end) in enumerate(windows)
                      for b_start, b_end in windows[i + 1:])
        self.assertTrue(overlap, f"pencereler: {windows}")

    @patch("app.pipeline.get_provider")
    def test_one_slide_failing_stops_the_render_and_surfaces_the_real_error(self, get_provider):
        get_provider.return_value = FakeProvider()
        slides = [Slide(title=f"Slayt {i}", narration=f"Metin {i}") for i in range(4)]

        def fake_render_slide(slide, i, total, breadcrumb, img_path, **kw):
            if i == 3:
                raise RuntimeError("disk dolu - slayt 3 çizilemedi")
            img_path.write_bytes(b"\x89PNG\r\n")

        with tempfile.TemporaryDirectory() as tmp, \
             patch("app.pipeline.PROJECTS_DIR", Path(tmp)), \
             patch("app.pipeline.render_slide", side_effect=fake_render_slide), \
             patch("app.pipeline.build_segment"):
            pdir = project_dir_for("pass3-hata")
            save_script(pdir, slides)
            with self.assertRaises(RuntimeError) as raised:
                render_video(pdir, slides, "edge", "tr-TR-AhmetNeural", "+0%", VideoOptions())
        self.assertIn("disk dolu - slayt 3", str(raised.exception))
        # Yarım kalan render bir "ders.mp4" üretmemeli.
        self.assertFalse((pdir / "ders.mp4").exists())

    @patch("app.pipeline.get_provider")
    def test_re_render_after_editing_one_slide_keeps_cached_segments_in_order(self, get_provider):
        """Karışık senaryo: çoğu slayt önbellekten geliyor, yalnızca biri yeniden üretiliyor —
        paralel dal yalnız o tek slaytı işlese de nihai sıralama bozulmamalı. Bu test gerçek
        ffmpeg ile gerçek segment üretip gerçek concat yapar (fake_render_slide/build_segment yok)."""
        get_provider.return_value = FakeProvider()
        slides = [Slide(title=f"Slayt {i}", narration=f"Değişmeyen metin {i}") for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp, patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
            pdir = project_dir_for("pass3-kismi-yeniden")
            save_script(pdir, slides)
            render_video(pdir, slides, "edge", "tr-TR-AhmetNeural", "+0%", VideoOptions(subtitles=False))
            self.assertTrue((pdir / "ders.mp4").is_file())

            slides[2].narration = "Bu slaytın anlatımı artık farklı"
            save_script(pdir, slides)
            render_video(pdir, slides, "edge", "tr-TR-AhmetNeural", "+0%", VideoOptions(subtitles=False))
            self.assertTrue((pdir / "ders.mp4").is_file())
            duration = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(pdir / "ders.mp4")],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertAlmostEqual(float(duration), 5.0, delta=0.5)  # 5 slayt x ~1sn


class ChapterMarkersTests(unittest.TestCase):
    """bkz. app/video/video_builder.py write_chapters_file/concat_videos — "chapter"
    seviyeli slaytlar final videoya atlanabilir bölüm işaretleri olarak gömülür."""

    @patch("app.pipeline.concat_audio")
    @patch("app.pipeline.concat_videos")
    @patch("app.pipeline.get_provider")
    def test_chapter_slides_produce_a_chapters_file_with_correct_titles_and_offsets(
        self, get_provider, concat_videos, concat_audio,
    ):
        get_provider.return_value = FakeProvider()
        slides = [
            Slide(title="Giriş", level="chapter", narration="Giriş anlatımı"),
            Slide(title="Konu 1", narration="Birinci konu anlatımı"),
            Slide(title="Pointer'lar", level="chapter", narration="Bölüm geçişi anlatımı"),
            Slide(title="Konu 2", narration="İkinci konu anlatımı"),
        ]

        def fake_render_slide(slide, i, total, breadcrumb, img_path, **kw):
            img_path.write_bytes(b"\x89PNG\r\n")

        def fake_build_segment(img_path, audio_path, duration, seg_path, opts, ass_path=None, reveal_stages=None):
            seg_path.write_bytes(b"fake-segment")

        with tempfile.TemporaryDirectory() as tmp, \
             patch("app.pipeline.PROJECTS_DIR", Path(tmp)), \
             patch("app.pipeline.render_slide", side_effect=fake_render_slide), \
             patch("app.pipeline.build_segment", side_effect=fake_build_segment):
            pdir = project_dir_for("bolum-isaretleri")
            save_script(pdir, slides)
            render_video(pdir, slides, "edge", "tr-TR-AhmetNeural", "+0%", VideoOptions())

            concat_videos.assert_called_once()
            chapters_file = concat_videos.call_args.kwargs["chapters_file"]
            self.assertIsNotNone(chapters_file)
            text = chapters_file.read_text(encoding="utf-8")
        self.assertEqual(text.count("[CHAPTER]"), 2)
        self.assertIn("title=Giriş", text)
        self.assertIn("title=Pointer'lar", text)
        self.assertIn("START=0", text)  # ilk bölüm videonun başında
        self.assertIn("START=2000", text)  # 2 slaytlık ses (1sn+1sn) sonra ikinci bölüm başlıyor

    @patch("app.pipeline.concat_audio")
    @patch("app.pipeline.concat_videos")
    @patch("app.pipeline.get_provider")
    def test_no_chapter_slides_means_no_chapters_file_at_all(
        self, get_provider, concat_videos, concat_audio,
    ):
        get_provider.return_value = FakeProvider()
        slides = [Slide(title=f"Slayt {i}", narration=f"Metin {i}") for i in range(3)]

        def fake_render_slide(slide, i, total, breadcrumb, img_path, **kw):
            img_path.write_bytes(b"\x89PNG\r\n")

        def fake_build_segment(img_path, audio_path, duration, seg_path, opts, ass_path=None, reveal_stages=None):
            seg_path.write_bytes(b"fake-segment")

        with tempfile.TemporaryDirectory() as tmp, \
             patch("app.pipeline.PROJECTS_DIR", Path(tmp)), \
             patch("app.pipeline.render_slide", side_effect=fake_render_slide), \
             patch("app.pipeline.build_segment", side_effect=fake_build_segment):
            pdir = project_dir_for("bolum-yok")
            save_script(pdir, slides)
            render_video(pdir, slides, "edge", "tr-TR-AhmetNeural", "+0%", VideoOptions())

        self.assertIsNone(concat_videos.call_args.kwargs["chapters_file"])

    @patch("app.pipeline.get_provider")
    def test_real_render_actually_embeds_readable_chapters_in_the_final_mp4(self, get_provider):
        """Gerçek ffmpeg ile render edip ffprobe'un bölümleri gerçekten okuyabildiğini
        doğrular — mock'lanmış üstteki testlerin aksine, konteynerin gerçekten doğru
        yazıldığından emin olmak için."""
        get_provider.return_value = FakeProvider()
        slides = [
            Slide(title="Giriş", level="chapter", narration="Giriş"),
            Slide(title="Ayrıntılar", narration="Ayrıntılar hakkında konuşalım"),
        ]
        with tempfile.TemporaryDirectory() as tmp, patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
            pdir = project_dir_for("gercek-bolum-gomme")
            save_script(pdir, slides)
            render_video(pdir, slides, "edge", "tr-TR-AhmetNeural", "+0%", VideoOptions(subtitles=False))

            import json
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_chapters", str(pdir / "ders.mp4")],
                capture_output=True, encoding="utf-8", check=True,
            )
            chapters = json.loads(result.stdout)["chapters"]
            self.assertEqual(len(chapters), 1)
            self.assertEqual(chapters[0]["tags"]["title"], "Giriş")


if __name__ == "__main__":
    unittest.main()
