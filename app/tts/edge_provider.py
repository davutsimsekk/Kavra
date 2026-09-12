import asyncio
from pathlib import Path

import edge_tts

from app.models import SynthResult, WordTiming
from app.tts.base import TTSProvider

TURKISH_VOICES = [
    {"id": "tr-TR-AhmetNeural", "label": "Ahmet (Erkek)"},
    {"id": "tr-TR-EmelNeural", "label": "Emel (Kadın)"},
]


class EdgeTTSProvider(TTSProvider):
    name = "edge"

    def list_voices(self) -> list[dict]:
        return TURKISH_VOICES

    def synthesize(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        return asyncio.run(self._synth_async(text, voice, out_path, rate))

    async def _synth_async(self, text, voice, out_path: Path, rate: str) -> SynthResult:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        words: list[WordTiming] = []
        with open(out_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    dur = chunk["duration"] / 1e7
                    words.append(WordTiming(text=chunk["text"], start=start, end=start + dur))
        duration = words[-1].end if words else 0.0
        return SynthResult(duration=duration, words=words or None)
