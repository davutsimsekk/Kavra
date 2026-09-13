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

import re
import time

DEFAULT_VISION_MODEL = "gemini-3.7-flash"

_PROMPT = (
    "Bu bir ders sunumu sayfasının görüntüsü. Önce sayfada görünen TÜM metni "
    "olduğu gibi aktar. Ardından, varsa diyagram/şema/grafik/tabloyu (kutular, "
    "oklar, aralarındaki ilişkiler, eksenler, değerler) kısa ve net bir "
    "paragrafla betimle. Yorum katma, sadece sayfada gerçekten görüneni yaz. "
    "Türkçe yaz. Sayfa tamamen boşsa veya anlamlı bir şey yoksa sadece "
    "\"[boş sayfa]\" yaz."
)

MAX_RETRIES = 3
BASE_DELAY = 4.0
MAX_DELAY = 30.0


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
    from google.genai import errors as genai_errors
    from google.genai import types

    client = genai.Client(api_key=api_key)
    contents = [_PROMPT, types.Part.from_bytes(data=image_bytes, mime_type="image/png")]

    delay = BASE_DELAY
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.models.generate_content(
                model=model, contents=contents,
                config={"http_options": {"timeout": timeout * 1000}},
            )
            return (resp.text or "").strip()
        except genai_errors.ClientError as e:
            msg = str(e)
            if not ("429" in msg or "RESOURCE_EXHAUSTED" in msg):
                raise
            if "quota_limit_value': '0'" in msg or 'quota_limit_value": "0"' in msg:
                # Dakikalık kota 0 raporlanıyorsa bu geçici bir hız sınırı değil,
                # hesap/proje tarafında bir kısıtlama demektir — 3 kez boşuna
                # denemek yerine hemen anlaşılır bir hata ver.
                raise RuntimeError(
                    "Gemini API bu anahtar için görsel anlama isteklerinde dakikalık kotayı "
                    "0 olarak raporluyor. Muhtemelen faturalandırma (billing) kapalı ya da "
                    "bu bölgede ücretsiz kademe kapalı. aistudio.google.com/apikey üzerinden "
                    "anahtarı ve bağlı projeyi kontrol et.\n"
                    f"(Ham hata: {msg[:300]})"
                ) from e
            last_err = e
            wait = min(delay, MAX_DELAY)
            m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+)", msg)
            if m:
                wait = max(wait, float(m.group(1)))
            if attempt < MAX_RETRIES:
                time.sleep(wait)
            delay *= 2
    raise RuntimeError(f"Görsel anlama {MAX_RETRIES} denemeden sonra hız sınırına takıldı: {last_err}")
