from abc import ABC, abstractmethod
from pathlib import Path

from app.models import SynthResult


class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    def list_voices(self) -> list[dict]:
        """[{"id": ..., "label": ...}]"""

    @abstractmethod
    def synthesize(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        ...
