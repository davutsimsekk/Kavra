"""Chatterbox uzun-anlatım parçalama testleri; gerçek model yüklenmez."""

from app.tts.chatterbox_provider import _split_text


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
