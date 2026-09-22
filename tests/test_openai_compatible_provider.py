import json
import unittest
from unittest.mock import Mock, patch

from app.llm.openai_compatible_provider import (
    OpenAICompatibleNarrationGenerator,
    normalize_chat_completions_endpoint,
)
from app.models import RawSection


def make_response(status_code=200, body=None, headers=None):
    response = Mock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.headers = headers or {}
    response.reason = "test"
    response.text = json.dumps(body or {}, ensure_ascii=False)
    response.json.return_value = body or {}
    return response


class EndpointTests(unittest.TestCase):
    def test_base_url_gets_chat_completions_path(self):
        self.assertEqual(
            normalize_chat_completions_endpoint("https://example.com/v1/"),
            "https://example.com/v1/chat/completions",
        )

    def test_full_endpoint_is_kept(self):
        endpoint = "http://localhost:1234/v1/chat/completions"
        self.assertEqual(normalize_chat_completions_endpoint(endpoint), endpoint)

    def test_rejects_non_http_url(self):
        with self.assertRaises(ValueError):
            normalize_chat_completions_endpoint("file:///tmp/api")


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.sections = [RawSection("", "Konu", "İçerik")]
        self.slide_json = json.dumps([
            {
                "title": "Konu",
                "bullets": ["Bir"],
                "code": None,
                "narration": "Anlatım",
                "level": "topic",
            }
        ], ensure_ascii=False)

    @patch("app.llm.openai_compatible_provider.requests.post")
    def test_generates_slides_and_sends_bearer_key(self, post):
        post.return_value = make_response(body={
            "choices": [{"message": {"content": self.slide_json}}]
        })
        generator = OpenAICompatibleNarrationGenerator(
            endpoint="https://example.com/v1",
            model="test-model",
            api_key="secret",
        )

        slides = generator.generate(self.sections)

        self.assertEqual(slides[0].title, "Konu")
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(kwargs["json"]["model"], "test-model")
        self.assertEqual(post.call_args.args[0], "https://example.com/v1/chat/completions")

    @patch("app.llm.openai_compatible_provider.requests.post")
    def test_local_server_can_be_used_without_key(self, post):
        post.return_value = make_response(body={
            "choices": [{"message": {"content": self.slide_json}}]
        })
        generator = OpenAICompatibleNarrationGenerator(
            endpoint="http://localhost:1234/v1",
            model="local-model",
            api_key=None,
        )

        generator.generate(self.sections)

        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])

    @patch("app.llm.openai_compatible_provider.time.sleep")
    @patch("app.llm.openai_compatible_provider.requests.post")
    def test_retries_rate_limit(self, post, sleep):
        post.side_effect = [
            make_response(429, {"error": {"message": "yavaşla"}}, {"Retry-After": "0"}),
            make_response(body={"choices": [{"message": {"content": self.slide_json}}]}),
        ]
        generator = OpenAICompatibleNarrationGenerator(
            endpoint="https://example.com/v1",
            model="test-model",
            api_key="secret",
        )

        slides = generator.generate(self.sections)

        self.assertEqual(len(slides), 1)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(0.0)

    @patch("app.llm.openai_compatible_provider.requests.post")
    def test_reads_list_based_message_content(self, post):
        post.return_value = make_response(body={
            "choices": [{"message": {"content": [{"type": "text", "text": self.slide_json}]}}]
        })
        generator = OpenAICompatibleNarrationGenerator(
            endpoint="https://example.com/v1",
            model="test-model",
            api_key="secret",
        )

        self.assertEqual(generator.generate(self.sections)[0].narration, "Anlatım")

    @patch("app.llm.openai_compatible_provider.requests.post")
    def test_reads_structured_output_object_wrapper(self, post):
        wrapped = json.dumps({"slides": json.loads(self.slide_json)}, ensure_ascii=False)
        post.return_value = make_response(body={
            "choices": [{"message": {"content": wrapped}}]
        })
        generator = OpenAICompatibleNarrationGenerator(
            endpoint="https://openrouter.ai/api/v1",
            model="openai/test-model",
            api_key="secret",
        )

        self.assertEqual(generator.generate(self.sections)[0].title, "Konu")

    @patch("app.llm.openai_compatible_provider.requests.post")
    def test_openrouter_requires_structured_slide_output(self, post):
        post.return_value = make_response(body={
            "choices": [{"message": {"content": self.slide_json}}]
        })
        generator = OpenAICompatibleNarrationGenerator(
            endpoint="https://openrouter.ai/api/v1",
            model="qwen/qwen3-235b-a22b-2507",
            api_key="secret",
        )

        generator.generate(self.sections)

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        schema = payload["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["properties"]["slides"]["type"], "array")
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertEqual(payload["provider"]["sort"], "throughput")
        self.assertEqual(payload["max_tokens"], 16_000)

    @patch("app.llm.openai_compatible_provider.requests.post")
    def test_call_with_json_schema_none_omits_the_slide_schema(self, post):
        """Regresyon testi: exam_routes.py ve app.flashcards, ders slaytı
        DIŞINDA bir şey (sınav sorusu, flashcard) isterken generator._call'ı
        json_schema=None ile çağırıyor — OpenRouter'a zorlanan yapı hâlâ
        slayt şekli olursa model "prompt"/kart alanı olmayan nesneler
        döndürüp çağıranın kendi doğrulaması başarısız oluyordu."""
        post.return_value = make_response(body={
            "choices": [{"message": {"content": '{"anything": "goes"}'}}]
        })
        generator = OpenAICompatibleNarrationGenerator(
            endpoint="https://openrouter.ai/api/v1",
            model="openai/test-model",
            api_key="secret",
        )

        result = generator._call("bir sınav sorusu üret", json_schema=None)

        self.assertEqual(result, '{"anything": "goes"}')
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("response_format", payload)
        # OpenRouter yönlendirme tercihleri (throughput/require_parameters) yine korunmalı.
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertEqual(payload["max_tokens"], 16_000)

    @patch("app.llm.openai_compatible_provider.requests.post")
    def test_call_without_json_schema_kwarg_defaults_to_the_slide_schema(self, post):
        post.return_value = make_response(body={
            "choices": [{"message": {"content": self.slide_json}}]
        })
        generator = OpenAICompatibleNarrationGenerator(
            endpoint="https://openrouter.ai/api/v1",
            model="openai/test-model",
            api_key="secret",
        )

        generator._call("bir slayt üret")

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"]["type"], "json_schema")


if __name__ == "__main__":
    unittest.main()
