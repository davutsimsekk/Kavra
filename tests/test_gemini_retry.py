"""app/gemini_retry.py — dört modülde (vision_caption, image_enrichment,
vision_narration, pronunciation_scan) kopyalanmış olan 429/kota-sıfır geri çekilme
mantığının TEK ortak implementasyonu."""
import unittest
from unittest.mock import patch

from app.gemini_retry import call_with_retry


class CallWithRetryTests(unittest.TestCase):
    def test_returns_the_function_result_on_success(self):
        self.assertEqual(call_with_retry(lambda: 42, feature_name="test"), 42)

    def test_non_rate_limit_client_error_propagates_immediately(self):
        from google.genai import errors as genai_errors

        def fn():
            raise genai_errors.ClientError(400, {"error": {"message": "INVALID_ARGUMENT"}})

        with self.assertRaises(genai_errors.ClientError):
            call_with_retry(fn, feature_name="test")

    @patch("time.sleep", return_value=None)
    def test_retries_on_rate_limit_then_succeeds(self, _sleep):
        from google.genai import errors as genai_errors

        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise genai_errors.ClientError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}})
            return "ok"

        self.assertEqual(call_with_retry(fn, feature_name="test"), "ok")
        self.assertEqual(calls["n"], 2)

    @patch("time.sleep", return_value=None)
    def test_zero_quota_fails_fast_with_actionable_message(self, _sleep):
        from google.genai import errors as genai_errors

        def fn():
            raise genai_errors.ClientError(429, {"error": {"message": "RESOURCE_EXHAUSTED: quota_limit_value': '0'"}})

        with self.assertRaises(RuntimeError) as raised:
            call_with_retry(fn, feature_name="test özelliği")
        self.assertIn("faturalandırma", str(raised.exception))
        self.assertIn("test özelliği", str(raised.exception))

    @patch("time.sleep", return_value=None)
    def test_exhausting_all_retries_raises_a_clear_error_naming_the_feature(self, _sleep):
        from google.genai import errors as genai_errors

        def fn():
            raise genai_errors.ClientError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}})

        with self.assertRaises(RuntimeError) as raised:
            call_with_retry(fn, feature_name="görsel tabanlı anlatım")
        self.assertIn("görsel tabanlı anlatım", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
