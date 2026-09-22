import subprocess
import unittest
from unittest.mock import MagicMock, patch

from app.config import CACHE_DIR, VideoOptions
from app.models import Slide
from app.tts import list_voices
from studio_web.api import (
    _effective_agent_session,
    _render_in_isolated_process,
    _single_request_is_safe,
)


class LightweightVoiceTests(unittest.TestCase):
    @patch("app.tts.get_provider")
    def test_listing_coqui_voices_does_not_construct_model(self, get_provider):
        voices = list_voices("coqui")
        get_provider.assert_not_called()
        self.assertEqual(voices[0]["id"], "builtin:default")


class SessionSemanticsTests(unittest.TestCase):
    def test_single_request_does_not_allocate_resume_session(self):
        self.assertFalse(_effective_agent_session(True, True))
        self.assertTrue(_effective_agent_session(True, False))
        self.assertFalse(_effective_agent_session(False, False))

    def test_large_source_is_not_safe_for_one_shot_generation(self):
        self.assertTrue(_single_request_is_safe(12, 24_000))
        self.assertFalse(_single_request_is_safe(13, 10_000))
        self.assertFalse(_single_request_is_safe(4, 24_001))


class RenderIsolationTests(unittest.TestCase):
    @patch("studio_web.api.subprocess.Popen")
    def test_native_coqui_crash_is_reported_without_crashing_api(self, popen):
        process = MagicMock()
        process.stdout = iter([])
        process.wait.return_value = -1073741819  # Windows 0xC0000005
        process.poll.return_value = -1073741819
        popen.return_value = process

        with self.assertRaisesRegex(RuntimeError, "web arayüzü çalışmaya devam ediyor"):
            _render_in_isolated_process(
                "native-crash-test",
                CACHE_DIR,
                [Slide(title="Test", narration="Test")],
                "coqui",
                "builtin:default",
                "+0%",
                VideoOptions(),
            )

        popen.assert_called_once()
        command = popen.call_args.args[0]
        self.assertEqual(command[1:3], ["-m", "studio_web.render_worker"])
        self.assertTrue(popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
        self.assertFalse((CACHE_DIR / "web_jobs" / "native-crash-test.json").exists())


    @patch("studio_web.api.jobs.update")
    @patch("studio_web.api.subprocess.Popen")
    def test_progress_marker_is_parsed_after_third_party_log_prefix(self, popen, update):
        process = MagicMock()
        process.stdout = iter([
            'XTTS warning __DERS_JOB__{"type":"status","message":"XTTS 2× hazır","total":1}\n',
            '__DERS_JOB__{"type":"complete","result":{"ok":true}}\n',
        ])
        process.wait.return_value = 0
        process.poll.return_value = 0
        popen.return_value = process

        result = _render_in_isolated_process(
            "embedded-marker-test",
            CACHE_DIR,
            [Slide(title="Test", narration="Test")],
            "coqui",
            "builtin:default",
            "+0%",
            VideoOptions(),
        )

        self.assertEqual(result, {"ok": True})
        update.assert_any_call(
            "embedded-marker-test",
            message="XTTS 2× hazır",
        )


if __name__ == "__main__":
    unittest.main()
