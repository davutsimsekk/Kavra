import json
import tempfile
import unittest
from pathlib import Path

from app.generation_checkpoint import (
    begin_chunk_commit,
    completed_indexes,
    ensure_checkpoint,
    finish_chunk_commit,
    generation_status,
    infer_legacy_completed_prefix,
    saved_agent_session,
)
from app.llm.base import NarrationGenerator
from app.models import RawSection, Slide
from app.pipeline import save_script


def section(title: str, text: str = "İçerik") -> RawSection:
    return RawSection("", title, text)


class RecordingGenerator(NarrationGenerator):
    def generate(self, sections, style_note=""):
        return [Slide(title=sections[0].title, narration="Anlatım")]


class GenerationCheckpointTests(unittest.TestCase):
    def test_legacy_numbered_project_infers_only_proven_prefix(self):
        sections = [section("1. Giriş"), section("1.1 Temel"), section("2. İleri"), section("3. Son")]
        slides = [Slide(title="1. Giriş"), Slide(title="1.1 Temel kavramlar"), Slide(title="2. İleri: örnek")]

        self.assertEqual(infer_legacy_completed_prefix(sections, slides), [0, 1, 2])

    def test_persisted_legacy_inference_stays_visible_in_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            sections = [section("1. Giriş"), section("2. Devam")]
            slides = [Slide(title="1. Giriş")]

            ensure_checkpoint(pdir, sections, slides)

            self.assertTrue(generation_status(pdir, sections, slides)["inferred"])

    def test_successful_chunk_is_skipped_but_changed_source_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            sections = [section("1. Giriş"), section("2. Devam")]
            state = ensure_checkpoint(pdir, sections, [])
            slides = [Slide(title="Giriş", narration="Anlatım")]

            begin_chunk_commit(pdir, state, [(0, sections[0])], 0, slides)
            save_script(pdir, slides)
            finish_chunk_commit(pdir, state)

            self.assertEqual(completed_indexes(state, sections), [0])
            changed = [section("1. Giriş", "Değişmiş içerik"), sections[1]]
            self.assertEqual(completed_indexes(state, changed), [])

    def test_pending_commit_is_reconciled_after_process_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            sections = [section("1. Giriş")]
            state = ensure_checkpoint(pdir, sections, [])
            slides = [Slide(title="Üretilen", narration="Anlatım")]

            begin_chunk_commit(pdir, state, [(0, sections[0])], 0, slides)
            save_script(pdir, slides)  # Simulate a crash before finish_chunk_commit.

            status = generation_status(pdir, sections, slides)
            self.assertEqual(status["completedIndexes"], [0])
            persisted = json.loads((pdir / "generation_checkpoint.json").read_text(encoding="utf-8"))
            self.assertIsNone(persisted["pending"])

    def test_agent_session_is_bound_to_the_same_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            sections = [section("1. Giriş")]
            state = ensure_checkpoint(pdir, sections, [])
            slides = [Slide(title="Üretilen")]
            command = "claude -p --output-format json"

            begin_chunk_commit(
                pdir, state, [(0, sections[0])], 0, slides,
                agent_session_id="session-42", agent_command=command,
            )
            save_script(pdir, slides)
            finish_chunk_commit(pdir, state)

            self.assertEqual(saved_agent_session(state, command), "session-42")
            self.assertIsNone(saved_agent_session(state, command + " --model haiku"))

    def test_chunk_callback_receives_exact_source_chunk(self):
        generator = RecordingGenerator()
        sections = [section("1. Bir"), section("2. İki"), section("3. Üç")]
        received = []

        generator.generate_chunked(
            sections,
            max_sections_per_chunk=2,
            request_interval_sec=0,
            chunk_completed_cb=lambda _i, _total, source_chunk, _slides: received.append(
                [item.title for item in source_chunk]
            ),
        )

        self.assertEqual(received, [["1. Bir", "2. İki"], ["3. Üç"]])


if __name__ == "__main__":
    unittest.main()
