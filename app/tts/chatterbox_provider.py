"""Chatterbox Multilingual için dar kapsamlı TTS sağlayıcısı.

Bu modül ana uygulamanın varsayılan ortamında import edilmemelidir. Chatterbox,
Coqui'nin desteklediği sürümden farklı bir ``transformers`` sürümü istiyor;
karşılaştırma demosu onu ``chatterbox_venv`` adlı yalıtılmış ortamda çalıştırır.
Beğenilirse video render hattına dahil etmek için aynı sınıf güvenle yeniden
kullanılabilir.
"""

from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path

import numpy as np

from app.config import MODELS_DIR, VOICE_REFERENCES_DIR
from app.models import SynthResult
from app.tts.base import TTSProvider


CHATTERBOX_SPEAKERS_DIR = MODELS_DIR / "chatterbox_speakers"
MAX_CHARS_PER_GENERATION = 220
MAX_COMPLETENESS_ATTEMPTS = 3
MAX_PLAUSIBLE_WORDS_PER_SECOND = 5.2
CHATTERBOX_T3_MODEL = "v3"
CHATTERBOX_TEMPERATURE = 0.85
CHATTERBOX_EXAGGERATION = 0.80
CHATTERBOX_CFG_WEIGHT = 0.30
CHATTERBOX_RECOMMENDED_MALE_REFERENCE_STEM = "Damien_Black"
CHATTERBOX_RECOMMENDED_FEMALE_REFERENCE_STEM = "Claribel_Dervla"
CHATTERBOX_DEFAULT_REFERENCE_STEM = "Doga_Upbeat_Rich"
CHATTERBOX_FEMALE_REFERENCE_STEMS = {
    "Claribel_Dervla",
    "Ana_Florence",
    "Tanja_Adelina",
    "Tammy_Grit",
    "Sofia_Hellen",
}


def list_chatterbox_voices() -> list[dict]:
    """Klon referanslarını önerilen ders sesi önce gelecek şekilde listele."""
    unique: dict[str, Path] = {}
    for directory in (VOICE_REFERENCES_DIR, CHATTERBOX_SPEAKERS_DIR):
        if directory.exists():
            for wav in directory.glob("*.wav"):
                unique[str(wav.resolve()).casefold()] = wav
    wavs = sorted(
        unique.values(),
        key=lambda wav: (
            {
                CHATTERBOX_DEFAULT_REFERENCE_STEM: 0,
                CHATTERBOX_RECOMMENDED_MALE_REFERENCE_STEM: 1,
                CHATTERBOX_RECOMMENDED_FEMALE_REFERENCE_STEM: 2,
            }.get(wav.stem, 3),
            wav.stem.casefold(),
        ),
    )
    voices = []
    for wav in wavs:
        display = wav.stem.replace("_", " ").title()
        if wav.stem == CHATTERBOX_DEFAULT_REFERENCE_STEM:
            label = "Doğa — upbeat & rich (Chatterbox V3 · varsayılan)"
        elif wav.stem == CHATTERBOX_RECOMMENDED_MALE_REFERENCE_STEM:
            label = f"{display} (Chatterbox klon · önerilen erkek ders sesi)"
        elif wav.stem == CHATTERBOX_RECOMMENDED_FEMALE_REFERENCE_STEM:
            label = f"{display} (Chatterbox klon · önerilen kadın ders sesi)"
        elif wav.stem in CHATTERBOX_FEMALE_REFERENCE_STEMS:
            label = f"{display} (Chatterbox klon · kadın ders sesi)"
        else:
            label = f"{display} (Chatterbox klon referansı)"
        voices.append({"id": str(wav), "label": label})
    return voices


def _split_text(
    text: str,
    maximum: int = MAX_CHARS_PER_GENERATION,
    isolate_sentences: bool = False,
) -> list[str]:
    """Anlatımı Chatterbox'ın güvenli bağlam parçalarına ayırır.

    Varsayılan hızlı mod kısa cümleleri ``maximum`` sınırına kadar birleştirir.
    ``isolate_sentences=True`` seçilirse her normal cümle ayrı üretim çağrısına
    dönüşür; bu bazı yutma vakalarını azaltabilir ama uzun derslerde belirgin
    biçimde daha yavaştır. Her iki mod da maximum'u aşan tek cümleyi sözcük
    sınırından böler.
    """
    clean = " ".join(text.split())
    if not clean:
        return []
    sentences = re.split(r"(?<=[.!?…])\s+", clean)
    fragments: list[str] = []
    for sentence in sentences:
        if len(sentence) > maximum:
            words = sentence.split()
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
            fragments.append(sentence)
    fragments = [fragment for fragment in fragments if fragment]
    if isolate_sentences:
        return fragments

    parts: list[str] = []
    current = ""
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


def _minimum_plausible_duration(text: str) -> float:
    """Tam bir Türkçe cümle için çok ihtiyatlı alt süre sınırı (saniye)."""
    word_count = len(re.findall(r"\S+", text))
    return max(0.55, word_count / MAX_PLAUSIBLE_WORDS_PER_SECOND)

class ChatterboxTTSProvider(TTSProvider):
    """Türkçe destekli Chatterbox Multilingual ile zero-shot ses klonlama."""

    name = "chatterbox"

    def __init__(
        self,
        device: str | None = None,
        sentence_isolation: bool = False,
        retry_incomplete: bool = False,
    ):
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
        loader = ChatterboxMultilingualTTS.from_pretrained
        if "t3_model" not in inspect.signature(loader).parameters:
            raise RuntimeError(
                "Kurulu Chatterbox Multilingual V3 seçimini desteklemiyor. "
                "venv\\Scripts\\python.exe install_chatterbox.py komutuyla güncelleyin."
            )
        self._model = loader(device=self.device, t3_model=CHATTERBOX_T3_MODEL)
        self._prepared_voice: str | None = None
        self.sentence_isolation = bool(sentence_isolation)
        self.retry_incomplete = bool(retry_incomplete)

    def list_voices(self) -> list[dict]:
        return list_chatterbox_voices()

    def synthesize(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        if not voice or voice == "builtin:default":
            raise ValueError("Chatterbox karşılaştırması için bir referans WAV dosyası gerekli.")

        # Aynı worker bir ders boyunca çoğunlukla aynı sesi kullanır. Referans
        # kodlamasını her slaytta tekrarlamak yerine ses değişene kadar bellekte
        # tutuyoruz. V3 ve seçilen canlı anlatım ayarları uygulamanın gerçek
        # render hattında da karşılaştırma demosuyla aynı kalır.
        resolved_voice = str(Path(voice).resolve())
        if self._prepared_voice != resolved_voice:
            self._model.prepare_conditionals(
                resolved_voice,
                exaggeration=CHATTERBOX_EXAGGERATION,
            )
            self._prepared_voice = resolved_voice

        clips = []
        for part in _split_text(text, isolate_sentences=self.sentence_isolation):
            # Uzun bir slaytı tek üretimde vermek KV cache'in 8 GB VRAM'i
            # doldurmasına ve modelin bir cümleyi atlamasına neden olabilir.
            # Şüpheli derecede kısa bir sonuçta farklı örnekleme akışıyla yeniden
            # dene; tüm denemeler kısa kalırsa sesi kaybetmek yerine en uzunu kullan.
            best_clip = None
            minimum_duration = _minimum_plausible_duration(part)
            attempt_count = MAX_COMPLETENESS_ATTEMPTS if self.retry_incomplete else 1
            for _attempt in range(attempt_count):
                with self._torch.inference_mode():
                    wav = self._model.generate(
                        part,
                        language_id="tr",
                        temperature=CHATTERBOX_TEMPERATURE,
                        exaggeration=CHATTERBOX_EXAGGERATION,
                        cfg_weight=CHATTERBOX_CFG_WEIGHT,
                    )
                clip = wav.squeeze(0).detach().cpu().numpy()
                del wav
                if best_clip is None or clip.size > best_clip.size:
                    best_clip = clip
                if clip.size / self._model.sr >= minimum_duration:
                    break
                if self.device.startswith("cuda"):
                    self._torch.cuda.empty_cache()
            clips.append(best_clip)
            if self.device.startswith("cuda"):
                self._torch.cuda.empty_cache()
        if not clips:
            raise ValueError("Seslendirilecek metin boş.")
        pause = np.zeros(int(self._model.sr * 0.18), dtype=clips[0].dtype)
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
