"""Anka TTS sağlayıcısının testleri: referans ses/transkript çözümleme ve
önbelleği, sentezleme akışı.

Gerçek anka/f5-tts/torch modelini YÜKLEMEZ (ağır: model indirme + GPU/CPU'da
dakikalar sürebilir) — anka.AnkaTTS sahte (mock) bir nesneyle değiştirilir.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.tts.anka_provider import AnkaTTSProvider


def _make_provider(fake_tts):
    with patch("anka.AnkaTTS.from_pretrained", return_value=fake_tts):
        return AnkaTTSProvider(device="cpu")


class ListVoicesTests(unittest.TestCase):
    def test_lists_builtin_default_and_matched_clone_pairs(self):
        provider = _make_provider(MagicMock())
        with patch("app.tts.anka_provider.ANKA_SPEAKERS_DIR") as fake_dir:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                (tmp_path / "ahmet.wav").write_bytes(b"")
                (tmp_path / "ahmet.txt").write_text("merhaba", encoding="utf-8")
                (tmp_path / "orphan.wav").write_bytes(b"")  # eşleşen .txt yok -> listelenmemeli
                fake_dir.exists.return_value = True
                fake_dir.glob.return_value = sorted(tmp_path.glob("*.wav"))

                voices = provider.list_voices()

        ids = [v["id"] for v in voices]
        self.assertEqual(ids[0], "builtin:default")
        self.assertEqual(len(voices), 2)
        self.assertTrue(any("ahmet" in v["id"] for v in voices))
        self.assertFalse(any("orphan" in v["id"] for v in voices))


class ResolveReferenceTests(unittest.TestCase):
    def test_builtin_default_downloads_and_caches_reference_pair(self):
        provider = _make_provider(MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = str(Path(tmp) / "male.wav")
            txt_path = Path(tmp) / "male.txt"
            txt_path.write_text("  Merhaba dünya.  ", encoding="utf-8")
            Path(wav_path).write_bytes(b"")

            with patch("huggingface_hub.hf_hub_download", side_effect=[wav_path, str(txt_path)]) as fake_dl:
                first = provider._resolve_reference("builtin:default")
                second = provider._resolve_reference("")  # boş ses de builtin:default'a düşmeli

        self.assertEqual(first, (wav_path, "Merhaba dünya."))
        self.assertEqual(first, second)
        # wav + txt için 2 indirme çağrısı; ikinci _resolve_reference çağrısı
        # tamamen önbellekten geldiği için toplam hâlâ 2 (4 değil).
        self.assertEqual(fake_dl.call_count, 2)

    def test_cloned_voice_reads_matching_transcript_file(self):
        provider = _make_provider(MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "ahmet.wav"
            wav_path.write_bytes(b"")
            (Path(tmp) / "ahmet.txt").write_text("Bu bir test cümlesidir.", encoding="utf-8")

            ref_audio, ref_text = provider._resolve_reference(str(wav_path))

        self.assertEqual(ref_audio, str(wav_path))
        self.assertEqual(ref_text, "Bu bir test cümlesidir.")

    def test_cloned_voice_without_transcript_raises_clear_error(self):
        provider = _make_provider(MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "no_transcript.wav"
            wav_path.write_bytes(b"")

            with self.assertRaisesRegex(ValueError, "transkript"):
                provider._resolve_reference(str(wav_path))


class SynthesizeTests(unittest.TestCase):
    def test_synthesize_converts_wav_to_mp3_and_reads_ffprobe_duration(self):
        fake_tts = MagicMock()
        fake_tts.synthesize.return_value = [0.0] * 24000
        provider = _make_provider(fake_tts)
        provider._ref_cache["builtin:default"] = ("ref.wav", "referans metin")

        with patch("app.tts.anka_provider.subprocess.run") as fake_run, \
             patch("pathlib.Path.unlink"):
            fake_run.return_value = MagicMock(stdout="3.20\n")
            result = provider.synthesize("merhaba dünya", "builtin:default", Path("out.mp3"))

        fake_tts.synthesize.assert_called_once_with("merhaba dünya", ref_audio="ref.wav", ref_text="referans metin")
        fake_tts.save_wav.assert_called_once()
        self.assertEqual(result.duration, 3.2)
        self.assertIsNone(result.words)
        # ffmpeg (wav->mp3) + ffprobe (süre) olmak üzere iki subprocess çağrısı beklenir.
        self.assertEqual(fake_run.call_count, 2)
        ffmpeg_call = fake_run.call_args_list[0].args[0]
        self.assertIn("ffmpeg", ffmpeg_call)

    def test_synthesize_skips_ffmpeg_conversion_for_wav_output(self):
        fake_tts = MagicMock()
        fake_tts.synthesize.return_value = [0.0] * 24000
        provider = _make_provider(fake_tts)
        provider._ref_cache["builtin:default"] = ("ref.wav", "referans metin")

        with patch("app.tts.anka_provider.subprocess.run") as fake_run:
            fake_run.return_value = MagicMock(stdout="1.00\n")
            provider.synthesize("merhaba", "builtin:default", Path("out.wav"))

        fake_tts.save_wav.assert_called_once_with(fake_tts.synthesize.return_value, "out.wav")
        self.assertEqual(fake_run.call_count, 1)  # yalnızca ffprobe, ffmpeg yok


if __name__ == "__main__":
    unittest.main()
