"""PDF page batches share context but retain exact per-page provenance."""
import unittest
from unittest.mock import Mock
from app.llm.base import NarrationGenerator
from app.generation_checkpoint import source_fingerprint
from app.models import RawSection, Slide
from app.regenerate import resolve_sibling_range

class PageBatchTests(unittest.TestCase):
    def setUp(self):
        self.sections = [RawSection("", "Same heading", f"Content {i}", page_image=f"/page_{i}.png") for i in range(7)]

    def generator(self, transform=None):
        class Recording(NarrationGenerator):
            def __init__(self):
                self.calls = []
            def generate(self, sections, style_note=""):
                self.calls.append((sections, style_note))
                slides = [Slide(title=s.title, narration=s.text, source_section_ids=[source_fingerprint(s)]) for s in sections]
                return transform(slides) if transform else slides
        return Recording()

    def test_batches_4_and_3_keep_order_images_and_individual_source_ids(self):
        gen = self.generator(lambda slides: list(reversed(slides)))
        results = gen.generate_chunked(self.sections, max_sections_per_chunk=4,
            single_slide_per_section=True, request_interval_sec=0)
        self.assertEqual([len(call[0]) for call in gen.calls], [4,3])
        self.assertEqual([s.narration for s in results], [s.text for s in self.sections])
        self.assertEqual([s.background_image for s in results], [s.page_image for s in self.sections])
        for i, (slide, source) in enumerate(zip(results, self.sections)):
            self.assertEqual(slide.source_section_ids, [source_fingerprint(source)])
            self.assertEqual(resolve_sibling_range(results, i), (i, i+1))
        self.assertIn("DERS BÜTÜNLÜĞÜ HAFIZASI", gen.calls[1][1])
        self.assertIn("Content 3", gen.calls[1][1])
        for source in self.sections[:4]:
            self.assertIn(source_fingerprint(source), gen.calls[0][1])

    def test_single_request_preserves_each_page(self):
        gen = self.generator()
        result = gen.generate_chunked(self.sections, single_request=True,
            max_sections_per_chunk=1, single_slide_per_section=True, request_interval_sec=0)
        self.assertEqual(len(gen.calls), 1)
        self.assertEqual(len(result), 7)

    def test_invalid_or_missing_page_ids_never_commit_a_batch(self):
        for mutation in [
            lambda slides: slides[:-1],
            lambda slides: [Slide(title="Missing ID")]*len(slides),
            lambda slides: [Slide(title="Unknown", source_section_ids=["unknown"])]+slides[1:],
            lambda slides: [Slide(title="Ambiguous", source_section_ids=slides[0].source_section_ids+slides[1].source_section_ids)]+slides[1:],
        ]:
            with self.subTest(mutation=mutation):
                gen = self.generator(mutation)
                commit = Mock()
                with self.assertRaisesRegex(ValueError, "PDF yanıtında"):
                    gen.generate_chunked(self.sections[:4], max_sections_per_chunk=4,
                        single_slide_per_section=True, chunk_completed_cb=commit, request_interval_sec=0)
                commit.assert_not_called()
                self.assertEqual(len(gen.calls), 1)  # No surprise paid retry.

    def test_character_limit_keeps_pages_whole(self):
        gen = self.generator()
        gen.generate_chunked(self.sections, max_sections_per_chunk=4, max_chars=1,
            single_slide_per_section=True, request_interval_sec=0)
        self.assertEqual([call[0] for call in gen.calls], [[s] for s in self.sections])

    def test_fragments_merge_only_with_their_own_page(self):
        gen = self.generator(lambda slides: list(reversed(slides + slides)))
        result = gen.generate_chunked(self.sections, single_request=True,
            single_slide_per_section=True, request_interval_sec=0)
        self.assertEqual([s.narration for s in result], [s.text+" "+s.text for s in self.sections])
        self.assertEqual(len(result), 7)
