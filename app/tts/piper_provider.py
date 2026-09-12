import subprocess
import wave
from pathlib import Path

from app.config import MODELS_DIR
from app.models import SynthResult
from app.tts.base import TTSProvider

PIPER_DIR = MODELS_DIR / "piper"


class PiperTTSProvider(TTSProvider):
    name = "piper"

    def __init__(self):
        self._voice_cache = {}

    def list_voices(self) -> list[dict]:
        voices = []
        if PIPER_DIR.exists():
            for onnx in sorted(PIPER_DIR.glob("*/*.onnx")):
                voices.append({"id": str(onnx), "label": onnx.stem.replace("tr_TR-", "").replace("-medium", "")})
        return voices

    def _get_voice(self, voice_path: str):
        from piper import PiperVoice
        if voice_path not in self._voice_cache:
            self._voice_cache[voice_path] = PiperVoice.load(voice_path)
        return self._voice_cache[voice_path]

    def synthesize(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        out_path = Path(out_path)
        pv = self._get_voice(voice)

        if out_path.suffix.lower() == ".wav":
            wav_path = out_path
        else:
            wav_path = out_path.with_suffix(".piper_tmp.wav")

        with wave.open(str(wav_path), "wb") as wav_file:
            pv.synthesize_wav(text, wav_file)

        if wav_path != out_path:
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
