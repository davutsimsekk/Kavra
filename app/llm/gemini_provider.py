import re
import time

from app.config import get_api_key
from app.llm.base import NarrationGenerator, extract_json_array
from app.llm.prompt_template import build_prompt
from app.models import RawSection, Slide

DEFAULT_MODEL = "gemini-3.5-flash-lite"

# 2026-09 itibarıyla kullanıcı tarafından doğrulanmış / API'nin geçerli kabul ettiği modeller.
# (google-genai'nin models.list() çağrısı bu hesapta 429 verdiği için elle listeleniyor;
# GUI'de bu liste bir başlangıç noktasıdır, kutu serbest metin de kabul eder.)
KNOWN_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3.1-pro-preview",
]

MAX_RETRIES = 6
BASE_DELAY = 5.0
MAX_DELAY = 90.0


class GeminiRateLimitError(RuntimeError):
    pass


class GeminiNarrationGenerator(NarrationGenerator):
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        from google import genai

        key = api_key or get_api_key("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY bulunamadı. https://aistudio.google.com/apikey adresinden "
                "ücretsiz bir anahtar alıp ayarlardan veya .env dosyasına ekle."
            )
        self.client = genai.Client(api_key=key)
        self.model = model

    def _call(self, prompt: str) -> str:
        from google.genai import errors as genai_errors

        delay = BASE_DELAY
        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.client.models.generate_content(model=self.model, contents=prompt)
                return resp.text
            except genai_errors.ClientError as e:
                msg = str(e)
                is_rate_limit = "429" in msg or "RESOURCE_EXHAUSTED" in msg
                if not is_rate_limit:
                    raise
                last_err = e
                quota_zero = "quota_limit_value': '0'" in msg or 'quota_limit_value": "0"' in msg
                if quota_zero:
                    raise GeminiRateLimitError(
                        "Gemini API bu proje/anahtar için dakikalık istek kotasını 0 olarak "
                        "raporluyor. Bu, çok hızlı istek atmakla düzelmez — hesap/proje "
                        "tarafında bir kısıtlama var demektir. Kontrol et:\n"
                        "1) aistudio.google.com/apikey içindeki key doğru projeye mi bağlı?\n"
                        "2) O Google Cloud projesinde faturalandırma (billing) etkin mi? "
                        "(Tier 1 için billing şart; bazı AB/EEA/İngiltere/İsviçre bölgelerinde "
                        "ücretsiz kademe tamamen kapalı olabilir.)\n"
                        "3) console.cloud.google.com üzerinde 'Generative Language API' "
                        "kotalarına bakıp bir artırım talep edebilirsin.\n"
                        f"(Ham hata: {msg[:300]})"
                    ) from e
                wait = min(delay, MAX_DELAY)
                m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+)", msg)
                if m:
                    wait = max(wait, float(m.group(1)))
                time.sleep(wait)
                delay *= 2
        raise GeminiRateLimitError(
            f"{MAX_RETRIES} denemeden sonra hâlâ 429 (rate limit) alınıyor. "
            f"Son hata: {last_err}"
        )

    def generate(self, sections: list[RawSection], style_note: str = "") -> list[Slide]:
        prompt = build_prompt(sections, style_note)
        raw_text = self._call(prompt)
        try:
            data = extract_json_array(raw_text)
        except ValueError:
            fix_prompt = (
                "Aşağıdaki metni SADECE geçerli bir JSON dizisine çevir, "
                "başka hiçbir şey yazma:\n\n" + raw_text
            )
            raw_text = self._call(fix_prompt)
            data = extract_json_array(raw_text)
        return [Slide.from_dict(d) for d in data]
