import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import pymupdf

from app.export import (
    build_anki_tsv,
    build_markdown_notes,
    build_plain_transcript,
    build_quiz_markdown,
    build_srt,
    build_vtt,
    write_export,
)
from app.models import Slide


def sample_slides() -> list[Slide]:
    return [
        Slide(title="Bölüm 1", level="chapter", narration="Giriş konuşması"),
        Slide(
            title="if / else",
            bullets=["Java ile aynı sözdizimi", "Koşul parantez içinde"],
            code="if (x > 0) { }",
            narration="if else yapısını burada anlatıyoruz.",
        ),
        Slide(title="Boş slayt", bullets=[], narration=""),
    ]


class MarkdownNotesTests(unittest.TestCase):
    def test_chapter_becomes_h1_topic_becomes_h2(self):
        md = build_markdown_notes(sample_slides())
        self.assertIn("# Bölüm 1", md)
        self.assertIn("## if / else", md)

    def test_bullets_code_and_narration_are_all_included(self):
        md = build_markdown_notes(sample_slides())
        self.assertIn("- Java ile aynı sözdizimi", md)
        self.assertIn("if (x > 0) { }", md)
        self.assertIn("if else yapısını burada anlatıyoruz.", md)


class PlainTranscriptTests(unittest.TestCase):
    def test_skips_slides_with_no_narration(self):
        text = build_plain_transcript(sample_slides())
        self.assertIn("if else yapısını burada anlatıyoruz.", text)
        self.assertNotIn("Boş slayt", text)  # narration boş -> hiç görünmemeli


class AnkiTsvTests(unittest.TestCase):
    def test_header_row_and_one_card_per_narrated_topic_slide(self):
        tsv = build_anki_tsv(sample_slides())
        lines = tsv.strip().split("\n")
        self.assertEqual(lines[0], "Front\tBack")
        self.assertEqual(len(lines), 2)  # sadece "if / else" nitelikli
        self.assertTrue(lines[1].startswith("if / else\t"))

    def test_chapter_slides_and_empty_slides_produce_no_card(self):
        tsv = build_anki_tsv(sample_slides())
        self.assertNotIn("Bölüm 1", tsv)
        self.assertNotIn("Boş slayt", tsv)

    def test_embedded_newlines_and_tabs_do_not_break_row_structure(self):
        slide = Slide(title="X", narration="Satır 1\nSatır 2\tsekmeli")
        tsv = build_anki_tsv([slide])
        self.assertEqual(len(tsv.strip().split("\n")), 2)
        self.assertIn("<br>", tsv)
        self.assertNotIn("\t\t", tsv)


class QuizMarkdownTests(unittest.TestCase):
    def test_produces_matching_question_and_answer_numbers(self):
        quiz = build_quiz_markdown(sample_slides())
        self.assertIn("1. **if / else** hakkında", quiz)
        self.assertIn("## Cevap Anahtarı", quiz)
        self.assertIn("if else yapısını burada anlatıyoruz.", quiz)

    def test_handles_no_eligible_slides_gracefully(self):
        quiz = build_quiz_markdown([Slide(title="Bölüm", level="chapter")])
        self.assertIn("Henüz test edilecek", quiz)


class WriteExportTests(unittest.TestCase):
    def test_writes_file_atomically_under_exports_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            path = write_export(pdir, "anki", sample_slides())
            self.assertTrue(path.exists())
            self.assertEqual(path.parent.name, "exports")
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_writes_one_pdf_page_per_slide(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_export(Path(tmp), "pdf", sample_slides(), theme_preset="white")
            self.assertEqual(path.name, "ders-slaytlari.pdf")
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))
            with pymupdf.open(path) as document:
                self.assertEqual(document.page_count, len(sample_slides()))
                self.assertAlmostEqual(document[0].rect.width / document[0].rect.height, 16 / 9)

    def test_rejects_unknown_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_export(Path(tmp), "epub", sample_slides())


def _write_fake_audio(path: Path, seconds: float):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", str(seconds),
         "-c:a", "libmp3lame", str(path)],
        check=True, capture_output=True,
    )


class SubtitleExportTests(unittest.TestCase):
    def test_srt_has_sequential_numbering_and_arrow_timestamps_without_a_render(self):
        # Hiç render edilmemiş bir proje — tahmini zamanlama yoluna düşmeli, hata vermemeli.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            slides = [Slide(title="Bir", narration="Bu kısa bir test cümlesidir gerçekten.")]
            srt = build_srt(pdir, slides)
            self.assertTrue(srt.startswith("1\n"))
            self.assertIn(" --> ", srt)
            self.assertIn(",", srt.splitlines()[1])  # SRT virgüllü milisaniye

    def test_vtt_starts_with_webvtt_header_and_dot_separated_ms(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            slides = [Slide(title="Bir", narration="Bu kısa bir test cümlesidir gerçekten.")]
            vtt = build_vtt(pdir, slides)
            self.assertTrue(vtt.startswith("WEBVTT\n"))
            timestamp_line = vtt.splitlines()[2]
            self.assertIn(" --> ", timestamp_line)
            self.assertIn(".", timestamp_line.split(" --> ")[0])

    def test_second_slide_timestamps_are_offset_by_first_slides_real_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            assets = pdir / "assets"
            assets.mkdir()
            _write_fake_audio(assets / "slide_001.mp3", 3.0)
            (assets / "slide_001.words.json").write_text(json.dumps([
                {"text": "Merhaba", "start": 0.0, "end": 1.0},
            ]), encoding="utf-8")
            _write_fake_audio(assets / "slide_002.mp3", 2.0)
            (assets / "slide_002.words.json").write_text(json.dumps([
                {"text": "Devam", "start": 0.0, "end": 0.8},
            ]), encoding="utf-8")

            slides = [Slide(title="Bir", narration="Merhaba"), Slide(title="İki", narration="Devam")]
            srt = build_srt(pdir, slides)

            # İkinci slaydın kelimesi, ~3 saniyelik ilk slayttan SONRA başlamalı.
            second_cue_start = srt.split("\n\n")[1].splitlines()[1].split(" --> ")[0]
            self.assertEqual(second_cue_start, "00:00:03,000")

    def test_srt_export_is_reachable_through_write_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            path = write_export(pdir, "srt", sample_slides())
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "altyazi.srt")


if __name__ == "__main__":
    unittest.main()
