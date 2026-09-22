"""Coqui XTTS sağlayıcısının testleri: konuşmacı çözümleme ve klonlanmış-ses
koşullandırma önbelleği.

Gerçek TTS/torch modelini YÜKLEMEZ (ağır: ~2GB, GPU/CPU'da dakikalar
sürebilir) — TTS.api.TTS sahte (mock) bir nesneyle değiştirilir.

Paralel üretim (birden fazla bağımsız process) artık bu sağlayıcının değil
app.tts.coqui_parallel'in sorumluluğu — bkz. tests/test_coqui_parallel.py.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.tts.coqui_provider import CoquiTTSProvider


def _make_fake_tts():
    fake_tts = MagicMock()
    fake_tts.speakers = ["Claribel Dervla"]
    fake_tts.synthesizer.tts_model.speaker_manager.speakers = {}
    fake_tts.synthesizer.tts_model.get_conditioning_latents = MagicMock(
        return_value=("latent", "embedding")
    )
    return fake_tts


def _make_provider(fake_tts):
    with patch("TTS.api.TTS", return_value=fake_tts):
        return CoquiTTSProvider(gpu=False)


class AudioLoadingPatchTests(unittest.TestCase):
    """Klonlanmış ses kullanımı, referans wav'ı okumak için TTS.tts.models.xtts
    içindeki load_audio'yu çağırıyor. Bu ortamda torchaudio.load() (TorchCodec
    üzerinden) her zaman OSError ile patlıyor (bkz. coqui_provider.py'deki
    _patch_xtts_audio_loading docstring'i) — bu testler, provider kurulduğunda
    bu fonksiyonun soundfile tabanlı, gerçekten çalışan bir sürümle
    değiştirildiğini doğrular. Gerçek TorchCodec hatasını TEKRAR ÜRETMEZ
    (torch/torchaudio zaten kurulu olduğu için mümkün değil) — yalnızca
    değiştirilen fonksiyonun kendisinin doğru çalıştığını doğrular.
    """

    def test_provider_construction_replaces_xtts_load_audio(self):
        _make_provider(_make_fake_tts())

        from TTS.tts.models import xtts as xtts_module

        self.assertTrue(getattr(xtts_module.load_audio, "_ders_video_patched", False))

    def test_patched_load_audio_reads_real_wav_via_soundfile(self):
        _make_provider(_make_fake_tts())

        import numpy as np
        import soundfile as sf
        from TTS.tts.models import xtts as xtts_module

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "ref.wav"
            samples = (np.sin(np.linspace(0, 10, 4800)) * 0.5).astype("float32")
            sf.write(str(wav_path), samples, 24000)

            audio = xtts_module.load_audio(str(wav_path), 24000)

        self.assertEqual(audio.shape[0], 1)  # mono
        self.assertLessEqual(audio.abs().max().item(), 1.0)

    def test_patch_is_idempotent_across_repeated_provider_construction(self):
        _make_provider(_make_fake_tts())
        from TTS.tts.models import xtts as xtts_module
        first = xtts_module.load_audio

        _make_provider(_make_fake_tts())
        second = xtts_module.load_audio

        self.assertIs(first, second)


class SpeakerResolutionTests(unittest.TestCase):
    def test_builtin_default_uses_first_available_speaker(self):
        fake_tts = _make_fake_tts()
        provider = _make_provider(fake_tts)

        self.assertEqual(provider._resolve_speaker_id("builtin:default"), "Claribel Dervla")

    def test_empty_voice_falls_back_to_builtin_default(self):
        fake_tts = _make_fake_tts()
        provider = _make_provider(fake_tts)

        self.assertEqual(provider._resolve_speaker_id(""), "Claribel Dervla")

    def test_cloned_voice_computes_conditioning_latents_once(self):
        fake_tts = _make_fake_tts()
        provider = _make_provider(fake_tts)
        model = fake_tts.synthesizer.tts_model

        first = provider._resolve_speaker_id("C:/voices/ahmet.wav")
        second = provider._resolve_speaker_id("C:/voices/ahmet.wav")
        third = provider._resolve_speaker_id("C:/voices/ahmet.wav")

        model.get_conditioning_latents.assert_called_once_with(audio_path="C:/voices/ahmet.wav")
        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_cloned_voice_is_registered_in_speaker_manager(self):
        fake_tts = _make_fake_tts()
        provider = _make_provider(fake_tts)
        model = fake_tts.synthesizer.tts_model

        speaker_id = provider._resolve_speaker_id("C:/voices/ahmet.wav")

        self.assertIn(speaker_id, model.speaker_manager.speakers)
        self.assertEqual(
            model.speaker_manager.speakers[speaker_id],
            {"gpt_conditioning_latents": "latent", "speaker_embedding": "embedding"},
        )

    def test_different_cloned_voices_are_cached_independently(self):
        fake_tts = _make_fake_tts()
        provider = _make_provider(fake_tts)
        model = fake_tts.synthesizer.tts_model

        first = provider._resolve_speaker_id("C:/voices/a.wav")
        second = provider._resolve_speaker_id("C:/voices/b.wav")

        self.assertNotEqual(first, second)
        self.assertEqual(model.get_conditioning_latents.call_count, 2)


class SynthesizeTests(unittest.TestCase):
    def test_synthesize_reuses_cached_speaker_and_reads_ffprobe_duration(self):
        fake_tts = _make_fake_tts()
        provider = _make_provider(fake_tts)

        with patch("app.tts.coqui_provider.subprocess.run") as fake_run:
            fake_run.return_value = MagicMock(stdout="12.50\n")
            result = provider.synthesize("merhaba dünya", "builtin:default", Path("out.mp3"))

        fake_tts.tts_to_file.assert_called_once()
        called_kwargs = fake_tts.tts_to_file.call_args.kwargs
        self.assertEqual(called_kwargs["speaker"], "Claribel Dervla")
        self.assertEqual(called_kwargs["language"], "tr")
        self.assertEqual(called_kwargs["text"], "merhaba dünya")
        self.assertEqual(result.duration, 12.5)
        self.assertIsNone(result.words)

    def test_synthesize_with_cloned_voice_does_not_recompute_latents_across_slides(self):
        fake_tts = _make_fake_tts()
        provider = _make_provider(fake_tts)
        model = fake_tts.synthesizer.tts_model

        with patch("app.tts.coqui_provider.subprocess.run") as fake_run:
            fake_run.return_value = MagicMock(stdout="5.0\n")
            provider.synthesize("slayt bir", "C:/voices/ahmet.wav", Path("s1.mp3"))
            provider.synthesize("slayt iki", "C:/voices/ahmet.wav", Path("s2.mp3"))
            provider.synthesize("slayt üç", "C:/voices/ahmet.wav", Path("s3.mp3"))

        model.get_conditioning_latents.assert_called_once()
        self.assertEqual(fake_tts.tts_to_file.call_count, 3)


if __name__ == "__main__":
    unittest.main()
