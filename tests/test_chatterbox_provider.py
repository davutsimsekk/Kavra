"""Chatterbox metin parçalama ve hafif ses-listesi testleri; model yüklenmez."""

from pathlib import Path
from unittest.mock import patch

from app.tts.chatterbox_provider import _minimum_plausible_duration, _split_text, list_chatterbox_voices


def test_split_text_keeps_all_words_in_order_and_respects_size_limit():
    text = (
        "İlk cümle kısa kalır. "
        "İkinci cümle ise benchmark sırasında GPU belleğinin neden dikkatle yönetilmesi gerektiğini "
        "açıklamak için yeterince uzun bir anlatım içerir. "
        "Son cümle metni bitirir."
    )
    chunks = _split_text(text, maximum=80)

    assert len(chunks) >= 3
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())


def test_split_text_returns_empty_for_whitespace_only():
    assert _split_text("  \n\t ") == []


def test_split_text_keeps_normal_sentences_in_separate_generation_calls():
    assert _split_text("Birinci cümle. İkinci cümle! Üçüncü cümle?") == [
        "Birinci cümle.", "İkinci cümle!", "Üçüncü cümle?",
    ]


def test_minimum_duration_flags_implausibly_short_sentence_outputs():
    assert _minimum_plausible_duration("tek") == 0.55
    assert _minimum_plausible_duration("bir iki üç dört beş altı") > 1.0


def test_voice_list_puts_recommended_damien_reference_first(tmp_path):
    for name in (
        "Luis_Moray.wav",
        "Damien_Black.wav",
        "Claribel_Dervla.wav",
        "Ana_Florence.wav",
        "turkce_erkek_referans.wav",
    ):
        (tmp_path / name).write_bytes(b"RIFF")

    with patch("app.tts.chatterbox_provider.CHATTERBOX_SPEAKERS_DIR", Path(tmp_path)), \
         patch("app.tts.chatterbox_provider.VOICE_REFERENCES_DIR", Path(tmp_path) / "shared-empty"):
        voices = list_chatterbox_voices()

    assert [Path(voice["id"]).name for voice in voices] == [
        "Damien_Black.wav",
        "Claribel_Dervla.wav",
        "Ana_Florence.wav",
        "Luis_Moray.wav",
        "turkce_erkek_referans.wav",
    ]
    assert "önerilen erkek ders sesi" in voices[0]["label"]
    assert "önerilen kadın ders sesi" in voices[1]["label"]
    assert "kadın ders sesi" in voices[2]["label"]


def test_voice_list_puts_shared_doga_reference_first(tmp_path):
    shared = tmp_path / "shared"
    specific = tmp_path / "specific"
    shared.mkdir()
    specific.mkdir()
    (shared / "Doga_Upbeat_Rich.wav").write_bytes(b"RIFF")
    (specific / "Damien_Black.wav").write_bytes(b"RIFF")

    with patch("app.tts.chatterbox_provider.VOICE_REFERENCES_DIR", shared), \
         patch("app.tts.chatterbox_provider.CHATTERBOX_SPEAKERS_DIR", specific):
        voices = list_chatterbox_voices()

    assert Path(voices[0]["id"]).name == "Doga_Upbeat_Rich.wav"
    assert "varsayılan" in voices[0]["label"]
