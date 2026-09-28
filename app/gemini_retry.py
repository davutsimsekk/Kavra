"""Gemini API çağrıları için ortak 429 (hız sınırı) / kota-sıfır geri çekilme mantığı.

app/vision_caption.py, app/image_enrichment.py, app/vision_narration.py ve
app/pronunciation_scan.py'nin her biri bu mantığı neredeyse birebir aynı şekilde
kopyalamıştı (dört ayrı kopyalanmış implementasyon) — burada TEK bir yerde toplanıp
o dört modül buradan import edecek şekilde güncellendi. Davranış hiçbiri için
değişmedi, sadece kopya kod kaldırıldı.
"""

from __future__ import annotations

import re
import time
from typing import Callable, TypeVar

T = TypeVar("T")

MAX_RETRIES = 3
BASE_DELAY = 4.0
MAX_DELAY = 30.0


def call_with_retry(fn: Callable[[], T], *, feature_name: str) -> T:
    """fn: parametresiz bir çağrı (ör. `lambda: client.models.generate_content(...)`).
    feature_name: hata mesajlarında kullanıcıya gösterilecek, o çağrıyı yapan özelliğin
    adı (ör. "görsel anlama", "görsel tabanlı anlatım", "telaffuz önerisi") — küçük
    harfle, bir cümle içine oturacak şekilde yazılmalı."""
    from google.genai import errors as genai_errors

    delay = BASE_DELAY
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except genai_errors.ClientError as e:
            msg = str(e)
            if not ("429" in msg or "RESOURCE_EXHAUSTED" in msg):
                raise
            if "quota_limit_value': '0'" in msg or 'quota_limit_value": "0"' in msg:
                # Dakikalık kota 0 raporlanıyorsa bu geçici bir hız sınırı değil,
                # hesap/proje tarafında bir kısıtlama demektir — boşuna 3 kez denemek
                # yerine hemen anlaşılır bir hata ver.
                raise RuntimeError(
                    f"Gemini API bu anahtar için {feature_name} isteklerinde dakikalık "
                    "kotayı 0 olarak raporluyor. Muhtemelen faturalandırma (billing) "
                    "kapalı ya da bu bölgede ücretsiz kademe kapalı. "
                    "aistudio.google.com/apikey üzerinden anahtarı ve bağlı projeyi "
                    f"kontrol et.\n(Ham hata: {msg[:300]})"
                ) from e
            last_err = e
            wait = min(delay, MAX_DELAY)
            m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+)", msg)
            if m:
                wait = max(wait, float(m.group(1)))
            if attempt < MAX_RETRIES:
                time.sleep(wait)
            delay *= 2
    raise RuntimeError(
        f"{feature_name[:1].upper()}{feature_name[1:]} {MAX_RETRIES} denemeden sonra "
        f"hız sınırına takıldı: {last_err}"
    )
