import json
import subprocess
import unittest
from unittest.mock import patch

from app.llm.agent_cli_provider import AgentCliNarrationGenerator
from app.llm.base import NarrationGenerator, chunk_sections
from app.models import RawSection, Slide


def envelope(title: str, session_id: str = "session-123") -> str:
    result = json.dumps([
        {
            "title": title,
            "bullets": ["Madde"],
            "code": None,
            "narration": "Anlatım metni.",
            "level": "topic",
        }
    ], ensure_ascii=False)
    return json.dumps({
        "result": result,
        "session_id": session_id,
        "is_error": False,
        "total_cost_usd": 0.01,
    })


class ChunkingTests(unittest.TestCase):
    def test_max_sections_limits_short_pdf_pages(self):
        sections = [RawSection("", f"Sayfa {i}", "kısa") for i in range(10)]
        chunks = chunk_sections(sections, max_chars=6000, max_sections=4)
        self.assertEqual([len(chunk) for chunk in chunks], [4, 4, 2])

    def test_stateless_chunks_receive_compact_lesson_memory(self):
        class RecordingGenerator(NarrationGenerator):
            def __init__(self):
                self.notes = []

            def generate(self, sections, style_note=""):
                self.notes.append(style_note)
                return [Slide(title=f"Çıktı {sections[0].title}", bullets=["özet"])]

        generator = RecordingGenerator()
        sections = [RawSection("", "Bir", "a"), RawSection("", "İki", "b")]
        existing = [Slide(title="Giriş", bullets=["temel kavram"])]

        generator.generate_chunked(
            sections,
            "Akademik anlat",
            max_sections_per_chunk=1,
            request_interval_sec=0,
            initial_context_slides=existing,
        )

        self.assertIn("Giriş", generator.notes[0])
        self.assertIn("Çıktı Bir", generator.notes[1])
        self.assertIn("önceki slaytları tekrar üretme", generator.notes[1])


class PersistentSessionTests(unittest.TestCase):
    @patch("app.llm.agent_cli_provider.subprocess.run")
    def test_second_chunk_resumes_first_claude_session(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, envelope("Birinci"), ""),
            subprocess.CompletedProcess([], 0, envelope("İkinci"), ""),
        ]
        generator = AgentCliNarrationGenerator(reuse_session=True)
        first = RawSection("", "Birinci", "İçerik")
        second = RawSection("", "İkinci", "İçerik")

        generator.generate([first])
        generator.generate([second])

        first_cmd = run.call_args_list[0].args[0]
        second_cmd = run.call_args_list[1].args[0]
        self.assertIn("--session-id", first_cmd)
        self.assertEqual(second_cmd[-2:], ["--resume", "session-123"])
        self.assertIn(
            "önceki slaytları tekrarlama",
            run.call_args_list[1].kwargs["input"].lower(),
        )

    @patch("app.llm.agent_cli_provider.subprocess.run")
    def test_session_reuse_can_be_disabled(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, envelope("Tek"), "")
        generator = AgentCliNarrationGenerator(reuse_session=False)

        generator.generate([RawSection("", "Konu", "İçerik")])

        cmd = run.call_args.args[0]
        self.assertNotIn("--session-id", cmd)
        self.assertNotIn("--resume", cmd)

    @patch("app.llm.agent_cli_provider.subprocess.run")
    def test_non_claude_cli_does_not_receive_claude_session_flags(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, envelope("Tek"), "")
        generator = AgentCliNarrationGenerator(
            command="gemini -p --output-format json",
            reuse_session=True,
        )

        generator.generate([RawSection("", "Konu", "İçerik")])

        cmd = run.call_args.args[0]
        self.assertNotIn("--session-id", cmd)
        self.assertNotIn("--resume", cmd)

    @patch("app.llm.agent_cli_provider.subprocess.run")
    def test_saved_session_is_resumed_on_first_call_after_restart(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, envelope("Devam"), "")
        generator = AgentCliNarrationGenerator(
            reuse_session=True,
            session_id="saved-session",
        )

        generator.generate([RawSection("", "Konu", "İçerik")])

        self.assertEqual(run.call_args.args[0][-2:], ["--resume", "saved-session"])


if __name__ == "__main__":
    unittest.main()
