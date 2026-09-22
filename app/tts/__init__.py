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
    if name == "anka":
        from app.tts.anka_provider import AnkaTTSProvider
        return AnkaTTSProvider(**kwargs)
    if name == "chatterbox":
        raise RuntimeError("Chatterbox, yalıtılmış GPU worker'larıyla video render hattında çalışır.")
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
    if name == "anka":
        from app.tts.anka_provider import ANKA_SPEAKERS_DIR

        voices = [{"id": "builtin:default", "label": "Varsayılan (Anka referans ses)"}]
        if ANKA_SPEAKERS_DIR.exists():
            voices.extend(
                {"id": str(wav), "label": f"Klonlanmış: {wav.stem}"}
                for wav in sorted(ANKA_SPEAKERS_DIR.glob("*.wav"))
                if wav.with_suffix(".txt").exists()
            )
        return voices
    if name == "chatterbox":
        from app.tts.chatterbox_provider import CHATTERBOX_SPEAKERS_DIR

        return [
            {"id": str(wav), "label": f"Klon referansı: {wav.stem.replace('_', ' ').title()}"}
            for wav in sorted(CHATTERBOX_SPEAKERS_DIR.glob("*.wav"))
        ]
    return get_provider(name).list_voices()


PROVIDER_LABELS = {
    "edge": "Edge-TTS (önerilen, ücretsiz, yüksek kalite, kısa internet gerekir)",
    "piper": "Piper (tam offline, orta kalite)",
    "elevenlabs": "ElevenLabs (bulut, en doğal, ücretli/sınırlı ücretsiz kota)",
    "coqui": "Coqui XTTS v2 (offline, ses klonlama, ağır kurulum)",
    "anka": "Anka TTS (offline, Türkçe'ye özel eğitildi, XTTS'ten ~2.3x hızlı, kişisel kullanım lisansı)",
    "chatterbox": "Chatterbox Multilingual (offline, Türkçe ses klonlama, GPU)",
}
