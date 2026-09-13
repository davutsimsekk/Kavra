import unittest
from unittest.mock import MagicMock, patch

from app.vision_caption import caption_page_image


class VisionCaptionTests(unittest.TestCase):
    def test_missing_api_key_raises_clear_error(self):
        with self.assertRaises(ValueError):
            caption_page_image(b"fake-png-bytes", api_key="")

    @patch("google.genai.Client")
    def test_returns_stripped_model_text_on_success(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text="  Sayfada bir akış şeması var: giriş -> işleme -> çıkış.  "
        )

        result = caption_page_image(b"fake-png-bytes", api_key="test-key")

        self.assertEqual(result, "Sayfada bir akış şeması var: giriş -> işleme -> çıkış.")
        client_cls.assert_called_once_with(api_key="test-key")

    @patch("time.sleep", return_value=None)
    @patch("google.genai.Client")
    def test_retries_on_rate_limit_then_succeeds(self, client_cls, _sleep):
        from google.genai import errors as genai_errors

        rate_limit_error = genai_errors.ClientError(
            429, {"error": {"message": "RESOURCE_EXHAUSTED"}}
        )
        client_cls.return_value.models.generate_content.side_effect = [
            rate_limit_error,
            MagicMock(text="Açıklama"),
        ]

        result = caption_page_image(b"fake-png-bytes", api_key="test-key")

        self.assertEqual(result, "Açıklama")
        self.assertEqual(client_cls.return_value.models.generate_content.call_count, 2)

    @patch("time.sleep", return_value=None)
    @patch("google.genai.Client")
    def test_zero_quota_fails_fast_with_actionable_message(self, client_cls, _sleep):
        from google.genai import errors as genai_errors

        zero_quota_error = genai_errors.ClientError(
            429, {"error": {"message": "RESOURCE_EXHAUSTED: quota_limit_value': '0'"}}
        )
        client_cls.return_value.models.generate_content.side_effect = zero_quota_error

        with self.assertRaises(RuntimeError) as raised:
            caption_page_image(b"fake-png-bytes", api_key="test-key")

        self.assertIn("faturalandırma", str(raised.exception))
        # Boşuna 3 kez denemek yerine ilk denemede anlaşılır hatayla durmalı.
        self.assertEqual(client_cls.return_value.models.generate_content.call_count, 1)

    @patch("google.genai.Client")
    def test_non_rate_limit_client_error_propagates_immediately(self, client_cls):
        from google.genai import errors as genai_errors

        client_cls.return_value.models.generate_content.side_effect = genai_errors.ClientError(
            400, {"error": {"message": "INVALID_ARGUMENT"}}
        )

        with self.assertRaises(genai_errors.ClientError):
            caption_page_image(b"fake-png-bytes", api_key="test-key")

        self.assertEqual(client_cls.return_value.models.generate_content.call_count, 1)


if __name__ == "__main__":
    unittest.main()
