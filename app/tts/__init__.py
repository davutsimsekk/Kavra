from app.tts.base import TTSProvider


def get_provider(name: str, **kwargs) -> TTSProvider:
    if name == "edge":
        from app.tts.edge_provider import EdgeTTSProvider
        return EdgeTTSProvider(**kwargs)
    if name == "piper":
        from app.tts.piper_provider import PiperTTSProvider
        return PiperTTSProvider(**kwargs)
    if name == "elevenlabs":
        from app.tts.elevenlabs_provider import ElevenLabsTTSProvider
        return ElevenLabsTTSProvider(**kwargs)
    if name == "coqui":
        from app.tts.coqui_provider import CoquiTTSProvider
        return CoquiTTSProvider(**kwargs)
    raise ValueError(f"Bilinmeyen TTS sağlayıcı: {name}")


def list_voices(name: str) -> list[dict]:
    """List voices without loading heavyweight synthesis models when possible."""
    if name == "coqui":
        from app.tts.coqui_provider import SPEAKERS_DIR

        voices = [{"id": "builtin:default", "label": "Varsayılan (XTTS dahili konuşmacı)"}]
        if SPEAKERS_DIR.exists():
            voices.extend(
                {"id": str(wav), "label": f"Klonlanmış: {wav.stem}"}
                for wav in sorted(SPEAKERS_DIR.glob("*.wav"))
            )
        return voices
    return get_provider(name).list_voices()


PROVIDER_LABELS = {
    "edge": "Edge-TTS (önerilen, ücretsiz, yüksek kalite, kısa internet gerekir)",
    "piper": "Piper (tam offline, orta kalite)",
    "elevenlabs": "ElevenLabs (bulut, en doğal, ücretli/sınırlı ücretsiz kota)",
    "coqui": "Coqui XTTS v2 (offline, ses klonlama, ağır kurulum)",
}
