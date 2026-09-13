import tempfile
import unittest
from pathlib import Path

from app.chapters import (
    build_youtube_chapters,
    export_all_chapters,
    format_timestamp,
    identify_chapters,
    missing_render_indexes,
)
from app.models import Slide


class IdentifyChaptersTests(unittest.TestCase):
    def test_groups_slides_at_each_chapter_marker(self):
        slides = [
            Slide(title="Bölüm 1", level="chapter"),
            Slide(title="1.1"), Slide(title="1.2"),
            Slide(title="Bölüm 2", level="chapter"),
            Slide(title="2.1"),
        ]
        chapters = identify_chapters(slides)
        self.assertEqual([(c.title, c.start, c.end) for c in chapters], [
            ("Bölüm 1", 0, 3), ("Bölüm 2", 3, 5),
        ])

    def test_slides_before_first_marker_become_implicit_giris_chapter(self):
        slides = [Slide(title="Kapak"), Slide(title="Bölüm 1", level="chapter"), Slide(title="1.1")]
        chapters = identify_chapters(slides)
        self.assertEqual(chapters[0].title, "Giriş")
        self.assertEqual((chapters[0].start, chapters[0].end), (0, 1))
        self.assertEqual(chapters[1].title, "Bölüm 1")

    def test_deck_with_no_chapter_markers_is_a_single_implicit_chapter(self):
        slides = [Slide(title="a"), Slide(title="b")]
        chapters = identify_chapters(slides)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "Giriş")
        self.assertEqual(chapters[0].slide_count, 2)

    def test_empty_deck_has_no_chapters(self):
        self.assertEqual(identify_chapters([]), [])


class TimestampTests(unittest.TestCase):
    def test_formats_hours_minutes_seconds(self):
        self.assertEqual(format_timestamp(0), "00:00:00")
        self.assertEqual(format_timestamp(65), "00:01:05")
        self.assertEqual(format_timestamp(3725), "01:02:05")

    def test_youtube_chapters_first_line_is_always_zero(self):
        slides = [Slide(title="Bölüm 1", level="chapter"), Slide(title="1.1"), Slide(title="Bölüm 2", level="chapter")]
        chapters = identify_chapters(slides)
        text = build_youtube_chapters(chapters, [30.0, 45.0, 20.0])
        lines = text.strip().split("\n")
        self.assertEqual(lines[0], "00:00:00 Bölüm 1")
        self.assertEqual(lines[1], "00:01:15 Bölüm 2")  # 30 + 45 saniye sonra


class ExportAllChaptersTests(unittest.TestCase):
    def _write_fake_segment(self, path: Path, seconds: float):
        # Gerçek, kısa, geçerli bir mp4/mp3 üretmek için ffmpeg lavfi kaynağı kullanıyoruz.
        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={seconds}",
             "-f", "lavfi", "-i", f"anullsrc=r=8000:cl=mono", "-t", str(seconds),
             "-c:v", "libx264", "-c:a", "aac", str(path)],
            check=True, capture_output=True,
        )

    def _write_fake_audio(self, path: Path, seconds: float):
        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", str(seconds),
             "-c:a", "libmp3lame", str(path)],
            check=True, capture_output=True,
        )

    def test_reports_clear_error_when_slides_are_not_all_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "assets").mkdir()
            slides = [Slide(title="Bölüm 1", level="chapter"), Slide(title="1.1")]
            with self.assertRaises(ValueError) as ctx:
                export_all_chapters(pdir, slides)
            self.assertIn("henüz render edilmemiş", str(ctx.exception))

    def test_missing_render_indexes_lists_unrendered_slides_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            assets = pdir / "assets"
            assets.mkdir()
            (assets / "segment_001.mp4").write_bytes(b"x")
            self.assertEqual(missing_render_indexes(pdir, 3), [2, 3])

    def test_builds_one_mp4_mp3_pair_per_chapter_from_existing_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            assets = pdir / "assets"
            assets.mkdir()
            slides = [
                Slide(title="Bölüm 1", level="chapter"),
                Slide(title="1.1", narration="x"),
                Slide(title="Bölüm 2", level="chapter"),
            ]
            for i in range(1, 4):
                self._write_fake_segment(assets / f"segment_{i:03d}.mp4", 1)
                self._write_fake_audio(assets / f"slide_{i:03d}.mp3", 1)

            result = export_all_chapters(pdir, slides)

            self.assertEqual(len(result["chapters"]), 2)
            chapters_dir = pdir / "chapters"
            for chapter in result["chapters"]:
                self.assertTrue((chapters_dir / chapter["videoFile"]).exists())
                self.assertTrue((chapters_dir / chapter["audioFile"]).exists())
            self.assertTrue((chapters_dir / result["youtubeChaptersFile"]).exists())


if __name__ == "__main__":
    unittest.main()
