import subprocess
from pathlib import Path

from app.config import MODELS_DIR
from app.models import SynthResult
from app.tts.base import TTSProvider

SPEAKERS_DIR = MODELS_DIR / "coqui_speakers"


class CoquiTTSProvider(TTSProvider):
    """Coqui XTTS v2: offline, çok dilli, ses klonlama destekli ama ağır (torch + ~2GB model).
    Kurulu değilse açık bir hata verir; kurulum için gui/install_coqui.py kullan."""
    name = "coqui"

    def __init__(self, gpu: bool | None = None):
        try:
            from TTS.api import TTS
        except ImportError as e:
            raise RuntimeError(
                "Coqui TTS kurulu değil. GUI'deki 'Coqui XTTS Kur' butonunu kullan "
                "veya: venv\\Scripts\\pip install TTS torch --index-url https://download.pytorch.org/whl/cpu"
            ) from e

        if gpu is None:
            try:
                import torch
                gpu = torch.cuda.is_available()
            except ImportError:
                gpu = False

        self.gpu = gpu
        self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=gpu)

    def list_voices(self) -> list[dict]:
        voices = [{"id": "builtin:default", "label": "Varsayılan (XTTS dahili konuşmacı)"}]
        if SPEAKERS_DIR.exists():
            for wav in sorted(SPEAKERS_DIR.glob("*.wav")):
                voices.append({"id": str(wav), "label": f"Klonlanmış: {wav.stem}"})
        return voices

    def synthesize(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        kwargs = dict(text=text, language="tr", file_path=str(out_path))
        if voice and voice != "builtin:default":
            kwargs["speaker_wav"] = voice
        else:
            kwargs["speaker"] = self.tts.speakers[0] if getattr(self.tts, "speakers", None) else None
        self.tts.tts_to_file(**kwargs)

        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_path)],
            capture_output=True, text=True, check=True,
        )
        duration = float(result.stdout.strip())
        return SynthResult(duration=duration, words=None)
