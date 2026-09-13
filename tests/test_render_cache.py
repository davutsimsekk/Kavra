import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import VideoOptions
from app.models import Slide, SynthResult, WordTiming
from app.pipeline import (
    _load_cached_words,
    _narration_hash,
    _save_cached_words,
    _slide_hash,
    project_dir_for,
    render_video,
    save_script,
)


def _write_fake_mp3(path: Path, seconds: float = 1.0):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", str(seconds),
         "-c:a", "libmp3lame", str(path)],
        check=True, capture_output=True,
    )


class FakeProvider:
    """Counts real synth calls so tests can assert audio reuse actually happened."""

    def __init__(self):
        self.calls = 0

    def synthesize(self, text, voice, out_path, rate="+0%"):
        self.calls += 1
        _write_fake_mp3(Path(out_path), seconds=1.0)
        words = [WordTiming(text=w, start=i * 0.3, end=i * 0.3 + 0.25) for i, w in enumerate(text.split())]
        return SynthResult(duration=1.0, words=words)


class NarrationHashTests(unittest.TestCase):
    def test_same_normalized_text_and_voice_settings_give_the_same_hash(self):
        self.assertEqual(
            _narration_hash("Merhaba dünya", "edge", "tr-TR-AhmetNeural", "+0%"),
            _narration_hash("Merhaba dünya", "edge", "tr-TR-AhmetNeural", "+0%"),
        )

    def test_different_voice_gives_a_different_hash(self):
        a = _narration_hash("Merhaba dünya", "edge", "tr-TR-AhmetNeural", "+0%")
        b = _narration_hash("Merhaba dünya", "edge", "tr-TR-EmelNeural", "+0%")
        self.assertNotEqual(a, b)

    def test_narration_hash_is_independent_of_theme_or_subtitle_settings(self):
        # _narration_hash'in imzasında opts hiç yok - bu, testin kendisi kadar
        # tasarımın da garantisi: tema/altyazı değişikliği bu hash'i etkileyemez.
        self.assertEqual(
            _narration_hash("Aynı metin", "piper", "v", "+0%"),
            _narration_hash("Aynı metin", "piper", "v", "+0%"),
        )

    def test_pronunciation_dictionary_change_only_invalidates_affected_narrations(self):
        # normalize_pronunciation SONRASI metin hash'lendiği için, sözlük değişince
        # yalnızca o terimi içeren anlatımların hash'i değişir — diğerleri aynı kalır.
        from app.tts.pronunciation import normalize_pronunciation

        old_map = {"switch": "sviç"}
        new_map = {"switch": "sivic"}  # kullanıcı telaffuzu değiştirdi
        affected = Slide(title="x", narration="switch yapısını anlatalım")
        unaffected = Slide(title="y", narration="if yapısını anlatalım")

        def hash_with(slide, mapping):
            text = normalize_pronunciation(slide.narration, mapping=mapping)
            return _narration_hash(text, "edge", "v", "+0%")

        self.assertNotEqual(hash_with(affected, old_map), hash_with(affected, new_map))
        self.assertEqual(hash_with(unaffected, old_map), hash_with(unaffected, new_map))


class SlideHashIgnoresMetadataFieldsTests(unittest.TestCase):
    """Regression test for 2026-09-13: adding sourceSectionIds/manuallyEdited to
    Slide silently invalidated every existing render cache (full re-render of a
    331-slide, hours-long project) because the hash used to cover slide.to_dict()
    wholesale. Metadata-only differences must never change the hash."""

    def test_hash_is_identical_regardless_of_source_or_manual_edit_metadata(self):
        opts = VideoOptions(theme_preset="auto")
        base = Slide(title="Aynı içerik", bullets=["a", "b"], narration="Aynı anlatım")
        with_metadata = Slide(
            title="Aynı içerik", bullets=["a", "b"], narration="Aynı anlatım",
            source_section_ids=["fp1", "fp2"], source_titles=["1.1", "1.2"], manually_edited=True,
        )
        self.assertEqual(
            _slide_hash(base, "edge", "v", "+0%", opts),
            _slide_hash(with_metadata, "edge", "v", "+0%", opts),
        )

    def test_hash_still_changes_when_actual_rendered_content_changes(self):
        opts = VideoOptions(theme_preset="auto")
        a = Slide(title="Başlık A", narration="x")
        b = Slide(title="Başlık B", narration="x")
        self.assertNotEqual(
            _slide_hash(a, "edge", "v", "+0%", opts),
            _slide_hash(b, "edge", "v", "+0%", opts),
        )


class WordCacheRoundTripTests(unittest.TestCase):
    def test_round_trips_word_timings_through_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "words.json"
            words = [WordTiming(text="a", start=0.0, end=0.2), WordTiming(text="b", start=0.2, end=0.5)]
            _save_cached_words(path, words)
            loaded = _load_cached_words(path)
            self.assertEqual([(w.text, w.start, w.end) for w in loaded], [("a", 0.0, 0.2), ("b", 0.2, 0.5)])

    def test_missing_file_returns_none(self):
        self.assertIsNone(_load_cached_words(Path("does-not-exist.json")))

    def test_saving_no_words_removes_any_existing_cache_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "words.json"
            path.write_text("[]", encoding="utf-8")
            _save_cached_words(path, None)
            self.assertFalse(path.exists())


class ThemeChangeReusesAudioTests(unittest.TestCase):
    @patch("app.pipeline.get_provider")
    def test_changing_theme_does_not_resynthesize_audio(self, get_provider):
        fake = FakeProvider()
        get_provider.return_value = fake
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
                pdir = project_dir_for("hash-test-proje")
                slides = [Slide(title="Slayt 1", narration="Bu slaytın anlatım metni burada duruyor")]
                save_script(pdir, slides)

                opts_a = VideoOptions(theme_preset="notebook")
                render_video(pdir, slides, "fake", "v", "+0%", opts_a)
                self.assertEqual(fake.calls, 1)
                first_audio_bytes = (pdir / "assets" / "slide_001.mp3").read_bytes()

                opts_b = VideoOptions(theme_preset="midnight")  # yalnızca tema değişti
                render_video(pdir, slides, "fake", "v", "+0%", opts_b)

                self.assertEqual(fake.calls, 1, "tema değişince ses yeniden sentezlenmemeli")
                self.assertEqual((pdir / "assets" / "slide_001.mp3").read_bytes(), first_audio_bytes)

    @patch("app.pipeline.get_provider")
    def test_changing_narration_does_resynthesize_audio(self, get_provider):
        fake = FakeProvider()
        get_provider.return_value = fake
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
                pdir = project_dir_for("hash-test-proje-2")
                slides = [Slide(title="Slayt 1", narration="İlk anlatım metni burada")]
                save_script(pdir, slides)
                opts = VideoOptions(theme_preset="notebook")
                render_video(pdir, slides, "fake", "v", "+0%", opts)
                self.assertEqual(fake.calls, 1)

                slides[0].narration = "Tamamen farklı bir anlatım metni şimdi burada"
                render_video(pdir, slides, "fake", "v", "+0%", opts)

                self.assertEqual(fake.calls, 2, "anlatım değişince ses yeniden sentezlenmeli")


if __name__ == "__main__":
    unittest.main()
