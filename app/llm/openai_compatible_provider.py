import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

import requests

from app.config import get_api_key
from app.llm.base import NarrationGenerator, extract_json_array
from app.llm.prompt_template import build_prompt
from app.models import RawSection, Slide

DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-5.2"

MAX_RETRIES = 6
BASE_DELAY = 2.0
MAX_DELAY = 60.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

SLIDE_ARRAY_SCHEMA = {
    "name": "lecture_slides",
    "strict": True,
    "schema": {
        # OpenAI Structured Outputs requires the root schema to be an object.
        # ``extract_json_array`` accepts this wrapper as well as the legacy bare
        # array used by Gemini and CLI agents.
        "type": "object",
        "properties": {
            "slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "bullets": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "code": {"type": ["string", "null"]},
                        "narration": {"type": "string"},
                        "level": {"type": "string", "enum": ["chapter", "topic"]},
                    },
                    "required": ["title", "bullets", "code", "narration", "level"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["slides"],
        "additionalProperties": False,
    },
}


def normalize_chat_completions_endpoint(endpoint: str) -> str:
    """Tam URL veya /v1 benzeri bir base URL kabul eder."""
    endpoint = endpoint.strip()
    if not endpoint:
        raise ValueError("OpenAI uyumlu API endpoint'i boş olamaz.")

    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "Endpoint geçerli bir http(s) URL olmalı. Örnek: "
            "https://api.openai.com/v1/chat/completions"
        )
    if parsed.username or parsed.password:
        raise ValueError("Güvenlik nedeniyle endpoint URL'sinde kullanıcı adı/parola kullanılamaz.")

    path = parsed.path.rstrip("/")
    if not path.lower().endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            return max(retry_at.timestamp() - time.time(), 0.0)
        except (TypeError, ValueError, OverflowError):
            return None


def _response_error(response: requests.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            if isinstance(error, str):
                return error
    except ValueError:
        pass
    return (response.text or response.reason or "Bilinmeyen API hatası").strip()[:1000]


def _content_from_response(data: dict) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("API yanıtında 'choices' dizisi bulunamadı.")

    choice = choices[0]
    if not isinstance(choice, dict):
        raise RuntimeError("API yanıtındaki ilk 'choice' geçersiz.")

    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str) and content.strip():
        return content

    # Bazı OpenAI-uyumlu sunucular metni parçalardan oluşan bir liste olarak döndürür.
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
        joined = "".join(parts).strip()
        if joined:
            return joined

    # Eski/gevşek uyumluluk sağlayan bazı sunucular completion biçimini kullanır.
    if isinstance(choice.get("text"), str) and choice["text"].strip():
        return choice["text"]

    raise RuntimeError("API yanıtında kullanılabilir bir metin içeriği bulunamadı.")


class OpenAICompatibleNarrationGenerator(NarrationGenerator):
    """OpenAI Chat Completions protokolünü kullanan resmi veya uyumlu API istemcisi."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        timeout: float = 300.0,
    ):
        self.endpoint = normalize_chat_completions_endpoint(endpoint)
        self.model = model.strip()
        self.timeout = timeout

        parsed = urlsplit(self.endpoint)
        is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        self.is_openrouter = parsed.hostname == "openrouter.ai" or parsed.hostname.endswith(".openrouter.ai")
        # A remotely saved OpenRouter/OpenAI secret must never be attached to a
        # localhost server by accident.  A local server can still receive a key
        # when the caller explicitly supplies one.
        self.api_key = api_key if api_key is not None else (
            None if is_local else get_api_key("OPENAI_API_KEY")
        )

        if not self.model:
            raise ValueError("OpenAI uyumlu API için model adı boş olamaz.")

        if parsed.hostname == "api.openai.com" and not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY bulunamadı. OpenAI API anahtarını arayüzden girip kaydet."
            )
        if self.api_key and parsed.scheme == "http" and parsed.hostname not in {
            "localhost", "127.0.0.1", "::1"
        }:
            raise ValueError(
                "API anahtarını korumak için uzak endpoint HTTPS kullanmalı. "
                "HTTP yalnızca localhost için kabul edilir."
            )

    def _call(self, prompt: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.is_openrouter:
            # OpenRouter can route only to providers that honor the schema.  This
            # prevents a long, paid generation from ending in unparsable JSON.
            payload.update({
                "max_tokens": 16_000,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": SLIDE_ARRAY_SCHEMA,
                },
                # OpenRouter otherwise prefers the cheapest route.  For long
                # lecture generations that can select a provider several times
                # slower than the alternatives and leave the UI looking stuck.
                # Throughput routing still keeps provider fallback while making
                # checkpoint commits arrive at a useful cadence.
                "provider": {
                    "require_parameters": True,
                    "sort": "throughput",
                },
            })

        delay = BASE_DELAY
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt == MAX_RETRIES:
                    break
                time.sleep(min(delay, MAX_DELAY))
                delay *= 2
                continue
            except requests.RequestException as exc:
                raise RuntimeError(f"OpenAI uyumlu API isteği gönderilemedi: {exc}") from exc

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = RuntimeError(
                    f"HTTP {response.status_code}: {_response_error(response)}"
                )
                if attempt == MAX_RETRIES:
                    break
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                time.sleep(min(retry_after if retry_after is not None else delay, MAX_DELAY))
                delay *= 2
                continue

            if not response.ok:
                raise RuntimeError(
                    f"OpenAI uyumlu API hata verdi (HTTP {response.status_code}): "
                    f"{_response_error(response)}"
                )

            try:
                return _content_from_response(response.json())
            except ValueError as exc:
                raise RuntimeError("OpenAI uyumlu API geçerli JSON döndürmedi.") from exc

        raise RuntimeError(
            f"OpenAI uyumlu API {MAX_RETRIES} denemeden sonra yanıt vermedi. "
            f"Son hata: {last_error}"
        )

    def generate(self, sections: list[RawSection], style_note: str = "") -> list[Slide]:
        raw_text = self._call(build_prompt(sections, style_note))
        try:
            data = extract_json_array(raw_text)
        except (ValueError, TypeError):
            fix_prompt = (
                "Aşağıdaki metni SADECE geçerli bir JSON dizisine çevir, "
                "başka hiçbir şey yazma:\n\n" + raw_text
            )
            data = extract_json_array(self._call(fix_prompt))
        return [Slide.from_dict(item) for item in data]
