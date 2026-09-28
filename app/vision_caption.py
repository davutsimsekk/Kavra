"""Görsel-ağırlıklı (metni az/hiç olmayan) sayfaları metne çeviren, anlatım
üretiminden TAMAMEN AYRI bir zenginleştirme adımı.

Anlatım üretimi (app/llm/*) her zaman metin alır, metin döndürür — bu hiç
değişmez. Kullanıcının seçtiği anlatım sağlayıcısı (Agent CLI, OpenAI-uyumlu
herhangi bir endpoint vb.) multimodal olmayabilir; bu yüzden görsel anlama
kasıtlı olarak HER ZAMAN bilinen-multimodal bir sağlayıcı (Gemini) ile,
anlatım sağlayıcısından bağımsız çalışır. Sonuç, ayrıştırma sırasında
RawSection.text'e düz metin olarak eklenir — anlatım LLM'i resmi hiç görmez.
"""

from __future__ import annotations

from app.gemini_retry import call_with_retry

DEFAULT_VISION_MODEL = "gemini-3.7-flash"

_PROMPT = (
    "Bu bir ders sunumu sayfasının görüntüsü. Önce sayfada görünen TÜM metni "
    "olduğu gibi aktar. Ardından, varsa diyagram/şema/grafik/tabloyu (kutular, "
    "oklar, aralarındaki ilişkiler, eksenler, değerler) kısa ve net bir "
    "paragrafla betimle. Yorum katma, sadece sayfada gerçekten görüneni yaz. "
    "Türkçe yaz. Sayfa tamamen boşsa veya anlamlı bir şey yoksa sadece "
    "\"[boş sayfa]\" yaz."
)

def caption_page_image(image_bytes: bytes, api_key: str, model: str = DEFAULT_VISION_MODEL,
                        timeout: int = 60) -> str:
    """image_bytes: PNG olarak sayfa görüntüsü. api_key zorunlu (çağıran taraf,
    kullanıcının kayıtlı Gemini anahtarını çözüp geçirmekten sorumlu).
    """
    if not api_key:
        raise ValueError(
            "Görsel anlama için bir Gemini API anahtarı gerekli "
            "(anlatım sağlayıcından bağımsız, sadece bu adım için)."
        )
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    contents = [_PROMPT, types.Part.from_bytes(data=image_bytes, mime_type="image/png")]

    def _run():
        return client.models.generate_content(
            model=model, contents=contents,
            config={"http_options": {"timeout": timeout * 1000}},
        )

    resp = call_with_retry(_run, feature_name="görsel anlama")
    return (resp.text or "").strip()
