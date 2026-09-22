import unittest

from app.tts.base import local_engine_unavailable


class LocalEngineMessageTests(unittest.TestCase):
    def test_windows_keeps_the_installation_hint(self):
        hint = "Coqui TTS kurulu değil. GUI'deki 'Coqui XTTS Kur' butonunu kullan"
        self.assertEqual(local_engine_unavailable("XTTS v2", hint, remote_ok=True, windows=True), hint)

    def test_server_message_points_to_remote_gpu_for_engines_that_support_it(self):
        message = local_engine_unavailable("XTTS v2", "win", remote_ok=True, windows=False)
        self.assertIn("Uzak GPU", message)
        self.assertIn("Edge-TTS", message)
        self.assertNotIn("GUI", message)
        self.assertNotIn("venv", message)

    def test_server_message_for_engines_without_remote_support_does_not_offer_it(self):
        message = local_engine_unavailable("Chatterbox", "win", remote_ok=False, windows=False)
        self.assertIn("Bu motor uzak GPU'da çalışmaz", message)
        self.assertNotIn("«Çalıştırma yeri", message)


if __name__ == "__main__":
    unittest.main()
