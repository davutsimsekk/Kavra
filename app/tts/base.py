import os
from abc import ABC, abstractmethod
from pathlib import Path

from app.models import SynthResult


def local_engine_unavailable(engine: str, windows_hint: str, remote_ok: bool, *, windows: bool | None = None) -> str:
    """Yerel TTS motoru kurulu değilse mesaj: Windows'ta kurulum yönergesi, sunucu/Docker'da yapılacak şey.

    Hafif Docker imajında (KAVRA_LOCAL_TTS=0) ağır motorlar yoktur; Windows'a özgü GUI/venv yönergeleri
    orada yanıltıcı olur."""
    if (os.name == "nt") if windows is None else windows:
        return windows_hint
    alternative = ("Ses ayarlarında «Çalıştırma yeri: Uzak GPU» seç (bkz. REMOTE_TTS.md) veya Edge-TTS kullan."
                   if remote_ok else "Bu motor uzak GPU'da çalışmaz; Edge-TTS ya da uzak XTTS v2 kullan.")
    return f"Bu sunucuda yerel {engine} kurulu değil (hafif Docker imajı veya GPU'suz sunucu). {alternative}"


class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    def list_voices(self) -> list[dict]:
        """[{"id": ..., "label": ...}]"""

    @abstractmethod
    def synthesize(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        ...
