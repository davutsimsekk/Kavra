import unittest

from app.generation_checkpoint import source_fingerprint
from app.llm.base import tag_slides_with_source
from app.models import RawSection, Slide
from app.regenerate import apply_regeneration, resolve_sibling_range, resolve_source_sections


def section(title: str, text: str = "içerik") -> RawSection:
    return RawSection(breadcrumb="", title=title, text=text)


class TagSlidesWithSourceTests(unittest.TestCase):
    def test_all_slides_from_one_chunk_share_the_same_source_ids(self):
        sections = [section("2.6 Kontrol Akışı"), section("2.7 Fonksiyonlar")]
        slides = [Slide(title="if/else"), Slide(title="switch"), Slide(title="fonksiyonlar")]

        tag_slides_with_source(slides, sections)

        expected_ids = [source_fingerprint(s) for s in sections]
        for slide in slides:
            self.assertEqual(slide.source_section_ids, expected_ids)
            self.assertEqual(slide.source_titles, ["2.6 Kontrol Akışı", "2.7 Fonksiyonlar"])

    def test_manually_added_slide_keeps_empty_source(self):
        slide = Slide(title="Elle eklenen")
        self.assertEqual(slide.source_section_ids, [])


class SiblingRangeTests(unittest.TestCase):
    def test_finds_full_contiguous_group_sharing_source(self):
        ids_a, ids_b = ["a"], ["b"]
        slides = [
            Slide(title="1", source_section_ids=ids_a),
            Slide(title="2", source_section_ids=ids_a),
            Slide(title="3", source_section_ids=ids_a),
            Slide(title="4", source_section_ids=ids_b),
        ]
        self.assertEqual(resolve_sibling_range(slides, 1), (0, 3))
        self.assertEqual(resolve_sibling_range(slides, 3), (3, 4))

    def test_single_slide_group_is_isolated(self):
        slides = [
            Slide(title="1", source_section_ids=["a"]),
            Slide(title="2", source_section_ids=["b"]),
            Slide(title="3", source_section_ids=["a"]),  # not adjacent -> not merged
        ]
        self.assertEqual(resolve_sibling_range(slides, 1), (1, 2))


class ResolveSourceSectionsTests(unittest.TestCase):
    def test_resolves_fingerprints_back_to_original_document_order(self):
        sections = [section("A"), section("B"), section("C")]
        ids = [source_fingerprint(sections[2]), source_fingerprint(sections[0])]  # shuffled

        resolved = resolve_source_sections(sections, ids)

        self.assertEqual([s.title for s in resolved], ["A", "C"])  # document order preserved


class ApplyRegenerationTests(unittest.TestCase):
    def test_replaces_whole_sibling_group_with_fresh_slides(self):
        shared_ids, shared_titles = ["fp-1"], ["2.6 Kontrol Akışı"]
        slides = [
            Slide(title="eski-1", source_section_ids=shared_ids, source_titles=shared_titles),
            Slide(title="eski-2", source_section_ids=shared_ids, source_titles=shared_titles),
            Slide(title="ilgisiz", source_section_ids=["fp-2"], source_titles=["2.7"]),
        ]
        fresh = [Slide(title="yeni-1"), Slide(title="yeni-2"), Slide(title="yeni-3")]

        result = apply_regeneration(slides, 0, fresh)

        self.assertEqual([s.title for s in result], ["yeni-1", "yeni-2", "yeni-3", "ilgisiz"])
        for slide in result[:3]:
            self.assertEqual(slide.source_section_ids, shared_ids)
            self.assertEqual(slide.source_titles, shared_titles)
            self.assertFalse(slide.manually_edited)

    def test_refuses_slide_with_no_source(self):
        slides = [Slide(title="elle eklendi")]
        with self.assertRaises(ValueError):
            apply_regeneration(slides, 0, [Slide(title="x")])

    def test_refuses_empty_fresh_result(self):
        slides = [Slide(title="a", source_section_ids=["fp"])]
        with self.assertRaises(ValueError):
            apply_regeneration(slides, 0, [])


if __name__ == "__main__":
    unittest.main()
