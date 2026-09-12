from pathlib import Path

import requests

from app.config import get_api_key
from app.models import SynthResult
from app.tts.base import TTSProvider

API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

DEFAULT_VOICES = [
    {"id": "21m00Tcm4TlvDq8ikWAM", "label": "Rachel (multilingual, TR destekli)"},
    {"id": "pNInz6obpgDQGcFmaJgB", "label": "Adam (multilingual, TR destekli)"},
]


class ElevenLabsTTSProvider(TTSProvider):
    name = "elevenlabs"

    def __init__(self, api_key: str | None = None, model: str = "eleven_multilingual_v2"):
        self.api_key = api_key or get_api_key("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY bulunamadı. https://elevenlabs.io adresinden ücretsiz "
                "bir hesap/anahtar alıp ayarlardan ekle."
            )
        self.model = model

    def list_voices(self) -> list[dict]:
        return DEFAULT_VOICES

    def synthesize(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        resp = requests.post(
            API_URL.format(voice_id=voice),
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            json={"text": text, "model_id": self.model,
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=120,
        )
        resp.raise_for_status()
        Path(out_path).write_bytes(resp.content)

        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_path)],
            capture_output=True, text=True, check=True,
        )
        duration = float(result.stdout.strip())
        return SynthResult(duration=duration, words=None)
