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

    def test_duration_budget_is_proportional_to_section_share(self):
        class RecordingGenerator(NarrationGenerator):
            def __init__(self):
                self.notes = []

            def generate(self, sections, style_note=""):
                self.notes.append(style_note)
                return [Slide(title=sections[0].title)]

        generator = RecordingGenerator()
        # Başlıklar boş bırakılıyor ki ağırlık hesabı (_section_weight) sadece
        # gövde metninden gelsin ve pay tam olarak %75/%25 çıksın.
        long_section = RawSection("", "", "x" * 300)
        short_section = RawSection("", "", "y" * 100)

        generator.generate_chunked(
            [long_section, short_section],
            max_sections_per_chunk=1,
            request_interval_sec=0,
            continuity_context=False,
            target_duration_minutes=60,
        )

        self.assertIn("SÜRE HEDEFİ MODU AKTİF", generator.notes[0])
        self.assertIn("%75", generator.notes[0])
        self.assertIn("45.0 dakika", generator.notes[0])
        self.assertIn("%25", generator.notes[1])
        self.assertIn("15.0 dakika", generator.notes[1])

    def test_duration_budget_note_is_absent_by_default(self):
        class RecordingGenerator(NarrationGenerator):
            def __init__(self):
                self.notes = []

            def generate(self, sections, style_note=""):
                self.notes.append(style_note)
                return [Slide(title=sections[0].title)]

        generator = RecordingGenerator()
        generator.generate_chunked(
            [RawSection("", "Konu", "içerik")],
            "Üslup notu",
            request_interval_sec=0,
            continuity_context=False,
        )

        self.assertEqual(generator.notes[0], "Üslup notu")
        self.assertNotIn("SÜRE HEDEFİ", generator.notes[0])


class ContinuityFullTextTests(unittest.TestCase):
    def test_last_slides_full_narration_is_included_for_stateless_providers(self):
        from app.llm.base import build_continuity_context

        slides = [
            Slide(title="Eski", narration="Bu çok eski bir slayt, tam metni görünmemeli."),
            Slide(title="Az önceki", narration="Bu az önceki slaydın tam anlatım metni burada."),
            Slide(title="Son", narration="Bu en son üretilen slaydın tam anlatım metni."),
        ]

        note = build_continuity_context(slides, full_text_recent=2)

        self.assertIn("Bu az önceki slaydın tam anlatım metni burada.", note)
        self.assertIn("Bu en son üretilen slaydın tam anlatım metni.", note)
        self.assertNotIn("Bu çok eski bir slayt, tam metni görünmemeli.", note)
        self.assertIn("BİREBİR TEKRAR ÜRETME", note)

    def test_full_narration_is_truncated_to_bound_cost(self):
        from app.llm.base import build_continuity_context

        long_narration = "kelime " * 300  # kasıtlı olarak çok uzun
        slides = [Slide(title="Uzun", narration=long_narration)]

        note = build_continuity_context(slides, full_text_recent=1, full_text_max_chars=50)

        self.assertIn("…", note)
        self.assertLess(len(note), len(long_narration))


class SingleSlidePerSectionTests(unittest.TestCase):
    def test_single_slide_note_is_injected_when_enabled(self):
        class RecordingGenerator(NarrationGenerator):
            def __init__(self):
                self.notes = []

            def generate(self, sections, style_note=""):
                self.notes.append(style_note)
                return [Slide(title=sections[0].title)]

        generator = RecordingGenerator()
        generator.generate_chunked(
            [RawSection("", "Sayfa 1", "içerik")],
            request_interval_sec=0, continuity_context=False,
            single_slide_per_section=True,
        )

        self.assertIn("SAYFA MODU AKTİF", generator.notes[0])
        self.assertIn("TAM OLARAK 1 slayt", generator.notes[0])

    def test_multiple_returned_slides_are_merged_into_one(self):
        class SplittingGenerator(NarrationGenerator):
            def generate(self, sections, style_note=""):
                return [
                    Slide(title="Sayfa 1 - a", bullets=["birinci"], narration="İlk yarı."),
                    Slide(title="Sayfa 1 - b", bullets=["ikinci"], narration="İkinci yarı."),
                ]

        generator = SplittingGenerator()
        slides = generator.generate_chunked(
            [RawSection("", "Sayfa 1", "içerik")],
            request_interval_sec=0, continuity_context=False,
            single_slide_per_section=True,
        )

        self.assertEqual(len(slides), 1)
        self.assertEqual(slides[0].title, "Sayfa 1 - a")
        self.assertEqual(slides[0].bullets, ["birinci", "ikinci"])
        self.assertEqual(slides[0].narration, "İlk yarı. İkinci yarı.")

    def test_disabled_by_default_allows_multiple_slides_per_chunk(self):
        class SplittingGenerator(NarrationGenerator):
            def generate(self, sections, style_note=""):
                return [Slide(title="a"), Slide(title="b")]

        generator = SplittingGenerator()
        slides = generator.generate_chunked(
            [RawSection("", "Sayfa 1", "içerik")],
            request_interval_sec=0, continuity_context=False,
        )

        self.assertEqual(len(slides), 2)


class MalformedJsonRecoveryTests(unittest.TestCase):
    @patch("app.llm.agent_cli_provider.subprocess.run")
    def test_broken_json_triggers_a_fix_prompt_retry(self, run):
        broken = envelope("Kırık")[:-5]  # zarfı da, içindeki diziyi de bozar
        run.side_effect = [
            subprocess.CompletedProcess([], 0, broken, ""),
            subprocess.CompletedProcess([], 0, envelope("Düzeltilmiş"), ""),
        ]
        generator = AgentCliNarrationGenerator(reuse_session=False)

        slides = generator.generate([RawSection("", "Konu", "İçerik")])

        self.assertEqual(run.call_count, 2)
        self.assertIn("SADECE geçerli bir JSON", run.call_args_list[1].kwargs["input"])
        self.assertEqual(slides[0].title, "Düzeltilmiş")

    @patch("app.llm.agent_cli_provider.subprocess.run")
    def test_still_broken_after_retry_raises_clear_error(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "bu hiç JSON değil", "")
        generator = AgentCliNarrationGenerator(reuse_session=False)

        with self.assertRaises(ValueError):
            generator.generate([RawSection("", "Konu", "İçerik")])
        self.assertEqual(run.call_count, 2)


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
