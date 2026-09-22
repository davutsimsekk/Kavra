"""Chatterbox Multilingual için dar kapsamlı TTS sağlayıcısı.

Bu modül ana uygulamanın varsayılan ortamında import edilmemelidir. Chatterbox,
Coqui'nin desteklediği sürümden farklı bir ``transformers`` sürümü istiyor;
karşılaştırma demosu onu ``chatterbox_venv`` adlı yalıtılmış ortamda çalıştırır.
Beğenilirse video render hattına dahil etmek için aynı sınıf güvenle yeniden
kullanılabilir.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from app.config import MODELS_DIR
from app.models import SynthResult
from app.tts.base import TTSProvider


CHATTERBOX_SPEAKERS_DIR = MODELS_DIR / "chatterbox_speakers"
MAX_CHARS_PER_GENERATION = 220


def _split_text(text: str, maximum: int = MAX_CHARS_PER_GENERATION) -> list[str]:
    """Uzun anlatımı Chatterbox'ın güvenli bağlam parçalarına ayırır."""
    clean = " ".join(text.split())
    if not clean:
        return []
    sentences = re.split(r"(?<=[.!?…])\s+", clean)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > maximum:
            words = sentence.split()
            fragments = []
            fragment = ""
            for word in words:
                candidate = f"{fragment} {word}".strip()
                if fragment and len(candidate) > maximum:
                    fragments.append(fragment)
                    fragment = word
                else:
                    fragment = candidate
            if fragment:
                fragments.append(fragment)
        else:
            fragments = [sentence]
        for fragment in fragments:
            candidate = f"{current} {fragment}".strip()
            if current and len(candidate) > maximum:
                parts.append(current)
                current = fragment
            else:
                current = candidate
    if current:
        parts.append(current)
    return parts

class ChatterboxTTSProvider(TTSProvider):
    """Türkçe destekli Chatterbox Multilingual ile zero-shot ses klonlama."""

    name = "chatterbox"

    def __init__(self, device: str | None = None):
        try:
            import torch
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        except ImportError as exc:
            raise RuntimeError(
                "Chatterbox yalıtılmış ortamda kurulu değil. "
                "venv\\Scripts\\python.exe install_chatterbox.py komutunu çalıştır."
            ) from exc

        self.device = device or "cuda"
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(
                "Chatterbox GPU ortamında CUDA kullanılamıyor. "
                "venv\\Scripts\\python.exe install_chatterbox.py komutuyla CUDA kurulumunu yenileyin."
            )
        self._torch = torch
        self._model = ChatterboxMultilingualTTS.from_pretrained(device=self.device)

    def list_voices(self) -> list[dict]:
        return [
            {"id": str(wav), "label": f"Klon referansı: {wav.stem.replace('_', ' ').title()}"}
            for wav in sorted(CHATTERBOX_SPEAKERS_DIR.glob("*.wav"))
        ]

    def synthesize(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        if not voice or voice == "builtin:default":
            raise ValueError("Chatterbox karşılaştırması için bir referans WAV dosyası gerekli.")

        clips = []
        for part in _split_text(text):
            # Uzun bir slaytı tek üretimde vermek KV cache'in 8 GB VRAM'i
            # doldurmasına neden olur. Her cümle grubu bittikten sonra yalnızca
            # model belleği korunur; geçici üretim belleği GPU'dan bırakılır.
            with self._torch.inference_mode():
                wav = self._model.generate(part, language_id="tr", audio_prompt_path=str(voice))
            clips.append(wav.squeeze(0).detach().cpu().numpy())
            del wav
            if self.device.startswith("cuda"):
                self._torch.cuda.empty_cache()
        if not clips:
            raise ValueError("Seslendirilecek metin boş.")
        pause = np.zeros(int(self._model.sr * 0.12), dtype=clips[0].dtype)
        stitched = []
        for clip in clips:
            stitched.extend((clip, pause))
        samples = np.concatenate(stitched[:-1])
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        import soundfile as sf

        # Video hattı MP3 ister; soundfile ise WAV/FLAC yazar. Önce geçici WAV
        # üretip ffmpeg ile MP3'e dönüştürmek, demo ile video hattının aynı
        # sentez kodunu güvenle paylaşmasını sağlar.
        if out_path.suffix.lower() == ".wav":
            sf.write(str(out_path), samples, self._model.sr)
        else:
            source_wav = out_path.with_name(f"{out_path.stem}.chatterbox-source.wav")
            try:
                sf.write(str(source_wav), samples, self._model.sr)
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(source_wav), "-q:a", "2", str(out_path)],
                    check=True,
                )
            finally:
                source_wav.unlink(missing_ok=True)
        return SynthResult(duration=len(samples) / self._model.sr, words=None)
