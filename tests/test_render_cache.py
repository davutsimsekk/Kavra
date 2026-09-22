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


class CoquiParallelDispatchTests(unittest.TestCase):
    """render_video'nun Coqui + coqui_parallel_workers>1 durumunda
    app.tts.coqui_parallel.synthesize_parallel'a doğru yönlendirdiğini,
    diğer durumlarda eski sıralı yolu kullandığını doğrular. Gerçek
    multiprocessing/GPU yok — synthesize_parallel tamamen sahte."""

    @patch("app.pipeline.get_provider")
    @patch("app.pipeline.synthesize_parallel")
    def test_coqui_with_multiple_workers_uses_the_parallel_path(self, fake_parallel, get_provider):
        def fake_parallel_impl(items, n_workers, progress_cb=None, status_cb=None):
            for _text, _voice, out_path in items:
                _write_fake_mp3(Path(out_path), seconds=1.0)
            return [SynthResult(duration=1.0, words=None) for _ in items]

        fake_parallel.side_effect = fake_parallel_impl
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
                pdir = project_dir_for("coqui-parallel-proje")
                slides = [Slide(title=f"Slayt {i}", narration=f"Anlatım metni {i}") for i in range(1, 4)]
                save_script(pdir, slides)
                opts = VideoOptions(theme_preset="notebook", coqui_parallel_workers=3)
                render_video(pdir, slides, "coqui", "builtin:default", "+0%", opts)

        fake_parallel.assert_called_once()
        called_items, called_n_workers = fake_parallel.call_args[0]
        self.assertEqual(len(called_items), 3)
        self.assertEqual(called_n_workers, 3)
        get_provider.assert_not_called()  # paralel yolda tek bir israf model yüklemesi olmamalı

    @patch("app.pipeline.get_provider")
    @patch("app.pipeline.synthesize_parallel")
    def test_coqui_with_a_single_worker_uses_the_sequential_path(self, fake_parallel, get_provider):
        fake = FakeProvider()
        get_provider.return_value = fake
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
                pdir = project_dir_for("coqui-sequential-proje")
                slides = [Slide(title="Slayt 1", narration="Anlatım metni")]
                save_script(pdir, slides)
                opts = VideoOptions(theme_preset="notebook", coqui_parallel_workers=1)
                render_video(pdir, slides, "coqui", "builtin:default", "+0%", opts)

        fake_parallel.assert_not_called()
        self.assertEqual(fake.calls, 1)

    @patch("app.pipeline.get_provider")
    @patch("app.pipeline.synthesize_parallel")
    def test_non_coqui_provider_ignores_the_parallel_workers_setting(self, fake_parallel, get_provider):
        fake = FakeProvider()
        get_provider.return_value = fake
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
                pdir = project_dir_for("edge-ignores-parallel-proje")
                slides = [Slide(title="Slayt 1", narration="Anlatım metni")]
                save_script(pdir, slides)
                # coqui_parallel_workers=3 olsa bile sağlayıcı coqui değilse etkisiz olmalı.
                opts = VideoOptions(theme_preset="notebook", coqui_parallel_workers=3)
                render_video(pdir, slides, "edge", "tr-TR-AhmetNeural", "+0%", opts)

        fake_parallel.assert_not_called()
        self.assertEqual(fake.calls, 1)

    @patch("app.pipeline.synthesize_parallel")
    def test_each_slide_receives_its_own_audio_via_the_parallel_path(self, fake_parallel):
        def tagging_parallel(items, n_workers, progress_cb=None, status_cb=None):
            results = []
            for text, _voice, out_path in items:
                # Slayt numarasını (metnin son karakteri) ses süresine kodlayıp
                # her slaytın gerçekten KENDİ çıktısını aldığını doğruluyoruz.
                tag_seconds = float(text.strip()[-1])
                _write_fake_mp3(Path(out_path), seconds=tag_seconds)
                results.append(SynthResult(duration=tag_seconds, words=None))
            return results

        fake_parallel.side_effect = tagging_parallel
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
                pdir = project_dir_for("coqui-parallel-tagging-proje")
                slides = [Slide(title=f"Slayt {i}", narration=f"metin {i}") for i in range(1, 4)]
                save_script(pdir, slides)
                opts = VideoOptions(theme_preset="notebook", coqui_parallel_workers=2)
                render_video(pdir, slides, "coqui", "builtin:default", "+0%", opts)

                from app.video.video_builder import ffprobe_duration
                for i in range(1, 4):
                    audio_path = pdir / "assets" / f"slide_{i:03d}.mp3"
                    self.assertAlmostEqual(ffprobe_duration(audio_path), float(i), delta=0.15)

    @patch("app.pipeline.synthesize_parallel")
    def test_cached_slides_are_not_resent_to_the_parallel_path(self, fake_parallel):
        call_count = {"n": 0}

        def fake_parallel_impl(items, n_workers, progress_cb=None, status_cb=None):
            call_count["n"] += 1
            for _text, _voice, out_path in items:
                _write_fake_mp3(Path(out_path), seconds=1.0)
            return [SynthResult(duration=1.0, words=None) for _ in items]

        fake_parallel.side_effect = fake_parallel_impl
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
                pdir = project_dir_for("coqui-parallel-cache-proje")
                slides = [Slide(title=f"Slayt {i}", narration=f"Anlatım metni {i}") for i in range(1, 3)]
                save_script(pdir, slides)
                opts = VideoOptions(theme_preset="notebook", coqui_parallel_workers=2)
                render_video(pdir, slides, "coqui", "builtin:default", "+0%", opts)
                self.assertEqual(call_count["n"], 1)

                # Aynı slaytlarla ikinci render: hepsi önbellekten gelmeli, paralel
                # yol hiç tekrar çağrılmamalı.
                render_video(pdir, slides, "coqui", "builtin:default", "+0%", opts)
                self.assertEqual(call_count["n"], 1, "önbellekteki slaytlar tekrar üretilmemeli")

    @patch("app.pipeline.synthesize_parallel")
    def test_completed_audio_survives_interrupted_parallel_batch(self, fake_parallel):
        item_counts = []

        def flaky_parallel(items, n_workers, progress_cb=None, status_cb=None):
            item_counts.append(len(items))
            if len(item_counts) == 1:
                _write_fake_mp3(Path(items[0][2]), seconds=1.0)
                raise RuntimeError("simulated native interruption")
            for _text, _voice, out_path in items:
                _write_fake_mp3(Path(out_path), seconds=1.0)
            return [SynthResult(duration=1.0, words=None) for _ in items]

        fake_parallel.side_effect = flaky_parallel
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
                pdir = project_dir_for("coqui-partial-resume-proje")
                slides = [Slide(title=f"Slayt {i}", narration=f"Anlatım {i}") for i in range(1, 4)]
                save_script(pdir, slides)
                opts = VideoOptions(theme_preset="notebook", coqui_parallel_workers=2)

                with self.assertRaisesRegex(RuntimeError, "simulated native interruption"):
                    render_video(pdir, slides, "coqui", "builtin:default", "+0%", opts)
                render_video(pdir, slides, "coqui", "builtin:default", "+0%", opts)

        self.assertEqual(item_counts, [3, 2])

    @patch("app.pipeline.synthesize_parallel")
    def test_progress_cb_fires_during_tts_synthesis_not_only_after(self, fake_parallel):
        """Regresyon testi: eskiden progress_cb yalnızca 3. geçişte (görsel/
        segment üretimi) çağrılıyordu — büyük projelerde ses üretimi süren
        uzun 2. geçiş boyunca arayüz "0/0 Başlatılıyor"da donmuş görünüyordu.
        Artık 2. geçiş de kendi (done, total_pending, "Seslendiriliyor")
        ilerlemesini raporlamalı."""
        def fake_parallel_impl(items, n_workers, progress_cb=None, status_cb=None):
            for i, (_text, _voice, out_path) in enumerate(items, start=1):
                _write_fake_mp3(Path(out_path), seconds=1.0)
                if progress_cb:
                    progress_cb(i, len(items))
            return [SynthResult(duration=1.0, words=None) for _ in items]

        fake_parallel.side_effect = fake_parallel_impl
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
                pdir = project_dir_for("coqui-parallel-progress-proje")
                slides = [Slide(title=f"Slayt {i}", narration=f"Anlatım metni {i}") for i in range(1, 4)]
                save_script(pdir, slides)
                opts = VideoOptions(theme_preset="notebook", coqui_parallel_workers=2)
                render_video(pdir, slides, "coqui", "builtin:default", "+0%", opts,
                             progress_cb=lambda i, total, title: calls.append((i, total, title)))

        synth_calls = [c for c in calls if c[2] == "Seslendiriliyor"]
        self.assertEqual(synth_calls, [(1, 3, "Seslendiriliyor"), (2, 3, "Seslendiriliyor"), (3, 3, "Seslendiriliyor")])
        # 3. geçiş de hâlâ kendi (slayt bazlı) ilerlemesini raporlamaya devam etmeli.
        render_calls = [c for c in calls if c[2] != "Seslendiriliyor"]
        self.assertEqual(len(render_calls), 3)


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
    def test_force_audio_resynthesizes_even_when_cache_matches(self, get_provider):
        fake = FakeProvider()
        get_provider.return_value = fake
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.pipeline.PROJECTS_DIR", Path(tmp)):
                pdir = project_dir_for("force-audio-test")
                slides = [Slide(title="Slayt 1", narration="Aynı anlatım yeniden üretilecek")]
                save_script(pdir, slides)
                opts = VideoOptions(theme_preset="notebook")

                with (
                    patch("app.pipeline.render_slide"),
                    patch("app.pipeline.build_segment"),
                    patch("app.pipeline.concat_videos"),
                    patch("app.pipeline.concat_audio"),
                ):
                    render_video(pdir, slides, "fake", "v", "+0%", opts)
                    render_video(pdir, slides, "fake", "v", "+0%", opts, force_audio=True)

                self.assertEqual(fake.calls, 2, "zorunlu yenileme ses cache'ini kullanmamalı")

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
