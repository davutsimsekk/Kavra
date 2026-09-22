import subprocess
from pathlib import Path

from app.config import MODELS_DIR
from app.models import SynthResult
from app.tts.base import TTSProvider, local_engine_unavailable

ANKA_SPEAKERS_DIR = MODELS_DIR / "anka_speakers"

# HF Hub üzerinden gelen, modelin kendi değerlendirme setinde kullanılan referans
# ses+transkript çifti — "builtin:default" sesi bunlardan üretiliyor, projeye
# ayrı bir binary dosya olarak eklemeye gerek kalmadan (huggingface_hub kendi
# önbelleğini D:/.../_cache/hf altında tutuyor, bkz. app/config.py HF_HOME).
_DEFAULT_REF_REPO = "krmkayabasi/Anka-TTS"
_DEFAULT_REF_WAV = "eval/reference/male.wav"
_DEFAULT_REF_TXT = "eval/reference/male.txt"


class AnkaTTSProvider(TTSProvider):
    """Anka TTS: F5-TTS mimarisiyle Türkçe'ye özel eğitilmiş (260K adım, ~212 saat
    Türkçe konuşma), sıfır-atış (zero-shot) ses klonlama modeli.

    Bu depoda RTX 4060 Laptop üzerinde, aynı 8 gerçek ders anlatımıyla XTTS v2'ye
    karşı ölçüldü: ~2.35x daha hızlı (2.80x realtime vs 1.19x realtime), ~1/3 VRAM
    (~1GB vs ~2.8GB), ve XTTS'in bilinen "226 karakter (tr) sınırı aşıldı, ses
    kesilebilir" uyarısını hiç vermedi — uzun anlatımları kendi içinde cümle
    sınırlarına göre parçalıyor (bkz. anka.tts modülündeki chunk uyarıları).

    ÖNEMLİ LİSANS NOTU: model ağırlıkları CC-BY-NC-4.0 (yalnızca kişisel/araştırma
    kullanımı) — ticari kullanım için ayrı lisans gerekir, bkz.
    https://huggingface.co/krmkayabasi/Anka-TTS. Bu depoya yalnızca kişisel
    kullanım amacıyla eklendi.

    XTTS'in aksine referans ses YALNIZCA ses dosyası değil, o sesin BİREBİR
    transkripsiyonunu da gerektiriyor (F5-TTS mimarisinin gereği) — bu yüzden
    klonlanmış sesler burada tek bir .wav değil, eşleşen bir .wav + .txt çifti
    (bkz. ANKA_SPEAKERS_DIR).
    """

    name = "anka"

    def __init__(self, device: str | None = None):
        try:
            from anka import AnkaTTS
        except ImportError as e:
            raise RuntimeError(local_engine_unavailable(
                "Anka TTS",
                "Anka TTS kurulu değil. Kurmak için: venv\\Scripts\\python.exe install_anka.py",
                remote_ok=False,
            )) from e

        if device is None:
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        self.device = device
        self._tts = AnkaTTS.from_pretrained("anka-tts/v0.1", device=device)
        # (ref_audio_path, ref_text) çiftleri ses başına BİR KEZ hesaplanıp bu
        # sağlayıcı örneğinin (bir render işi boyunca yaşar) ömrü süresince
        # bellekte tutuluyor — Coqui'deki klonlanmış-ses koşullandırma önbelleğiyle
        # aynı gerekçe (bkz. coqui_provider.py).
        self._ref_cache: dict[str, tuple[str, str]] = {}

    def list_voices(self) -> list[dict]:
        voices = [{"id": "builtin:default", "label": "Varsayılan (Anka referans ses)"}]
        if ANKA_SPEAKERS_DIR.exists():
            for wav in sorted(ANKA_SPEAKERS_DIR.glob("*.wav")):
                if wav.with_suffix(".txt").exists():
                    voices.append({"id": str(wav), "label": f"Klonlanmış: {wav.stem}"})
        return voices

    def _resolve_reference(self, voice: str) -> tuple[str, str]:
        cache_key = voice or "builtin:default"
        cached = self._ref_cache.get(cache_key)
        if cached is not None:
            return cached

        if cache_key == "builtin:default":
            from huggingface_hub import hf_hub_download

            wav_path = hf_hub_download(_DEFAULT_REF_REPO, _DEFAULT_REF_WAV)
            txt_path = hf_hub_download(_DEFAULT_REF_REPO, _DEFAULT_REF_TXT)
            reference = (wav_path, Path(txt_path).read_text(encoding="utf-8").strip())
        else:
            txt_path = Path(cache_key).with_suffix(".txt")
            if not txt_path.exists():
                raise ValueError(
                    f"Klonlanmış Anka sesi için eşleşen transkript dosyası bulunamadı: {txt_path} "
                    "(Anka, XTTS'ten farklı olarak referans sesin BİREBİR yazılı metnini de gerektirir)."
                )
            reference = (cache_key, txt_path.read_text(encoding="utf-8").strip())

        self._ref_cache[cache_key] = reference
        return reference

    def synthesize(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        ref_audio, ref_text = self._resolve_reference(voice)
        wav = self._tts.synthesize(text, ref_audio=ref_audio, ref_text=ref_text)

        out_path = Path(out_path)
        if out_path.suffix.lower() == ".wav":
            self._tts.save_wav(wav, str(out_path))
        else:
            wav_path = out_path.with_suffix(".anka_tmp.wav")
            self._tts.save_wav(wav, str(wav_path))
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-q:a", "2", str(out_path)],
                check=True, capture_output=True,
            )
            wav_path.unlink(missing_ok=True)

        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_path)],
            capture_output=True, text=True, check=True,
        )
        duration = float(result.stdout.strip())
        return SynthResult(duration=duration, words=None)
