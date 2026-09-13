import tempfile
import unittest
from pathlib import Path

from app.flashcards import (
    _basic_card,
    _candidate_cards,
    _make_cloze_card,
    _pick_cloze_term,
    deck_summary,
    generate_deck,
    load_deck,
    save_deck,
)
from app.models import Slide


class PickClozeTermTests(unittest.TestCase):
    def test_picks_the_longest_non_stopword_candidate(self):
        term = _pick_cloze_term("Bu bir kesme isteği ve önceliklendirme örneğidir")
        self.assertEqual(term, "önceliklendirme")

    def test_returns_none_when_no_candidate_qualifies(self):
        term = _pick_cloze_term("Bu da bir şey için iyi")
        self.assertIsNone(term)

    def test_ignores_pure_numbers(self):
        term = _pick_cloze_term("Değer 123456789 olarak kalır")
        self.assertEqual(term, "Değer")


class MakeClozeCardTests(unittest.TestCase):
    def test_blanks_the_picked_term_and_keeps_it_as_the_answer(self):
        card = _make_cloze_card("İşlemci kesme isteği aldığında bağlamı korur")
        self.assertIsNotNone(card)
        self.assertNotIn(card["back"], card["front"])
        self.assertIn("_____", card["front"])

    def test_returns_none_for_a_bullet_with_no_good_term(self):
        self.assertIsNone(_make_cloze_card("Bu da bir şey"))


class BasicCardTests(unittest.TestCase):
    def test_skips_slide_without_narration(self):
        self.assertIsNone(_basic_card(Slide(title="Boş", narration="")))

    def test_includes_bullets_and_narration_in_back(self):
        slide = Slide(title="if/else", bullets=["Madde bir"], narration="Anlatım metni.")
        card = _basic_card(slide)
        self.assertEqual(card["front"], "if/else")
        self.assertIn("Madde bir", card["back"])
        self.assertIn("Anlatım metni.", card["back"])


class CandidateCardsTests(unittest.TestCase):
    def test_chapter_slides_produce_no_cards(self):
        slides = [Slide(title="Bölüm 1", level="chapter", narration="Giriş", bullets=["kesme isteği önceliklendirme"])]
        self.assertEqual(_candidate_cards(slides), [])

    def test_topic_slide_yields_basic_and_at_most_one_cloze_card(self):
        slides = [Slide(
            title="Kesmeler", narration="Kesmeler hakkında anlatım.",
            bullets=["İşlemci kesme isteği alır", "Bağlamı önceliklendirerek korur"],
        )]
        candidates = _candidate_cards(slides)
        kinds = [c["kind"] for c in candidates]
        self.assertEqual(kinds.count("basic"), 1)
        self.assertLessEqual(kinds.count("cloze"), 1)


class GenerateDeckTests(unittest.TestCase):
    def _slides(self):
        return [Slide(
            title="Kesmeler", narration="Kesmeler hakkında anlatım.",
            bullets=["İşlemci kesme isteği alır"],
        )]

    def test_fresh_deck_gets_new_scheduling_state(self):
        deck = generate_deck(self._slides())
        self.assertTrue(all(c["repetitions"] == 0 and not c["suspended"] for c in deck))
        self.assertTrue(all("id" in c for c in deck))

    def test_regenerating_with_unchanged_content_preserves_progress(self):
        slides = self._slides()
        first = generate_deck(slides)
        first[0]["repetitions"] = 3
        first[0]["easeFactor"] = 2.8
        first[0]["suspended"] = True

        second = generate_deck(slides, existing_cards=first)

        self.assertEqual(second[0]["id"], first[0]["id"])
        self.assertEqual(second[0]["repetitions"], 3)
        self.assertEqual(second[0]["easeFactor"], 2.8)
        self.assertTrue(second[0]["suspended"])

    def test_changed_content_gets_a_fresh_card_not_the_old_progress(self):
        slides = self._slides()
        first = generate_deck(slides)
        first[0]["repetitions"] = 5

        changed_slides = [Slide(title="Farklı başlık", narration="Farklı anlatım.", bullets=[])]
        second = generate_deck(changed_slides, existing_cards=first)

        self.assertNotEqual(second[0]["id"], first[0]["id"])
        self.assertEqual(second[0]["repetitions"], 0)


class DeckPersistenceTests(unittest.TestCase):
    def test_round_trips_through_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            deck = generate_deck([Slide(title="X", narration="anlatım", bullets=[])])
            save_deck(pdir, deck)
            loaded = load_deck(pdir)
            self.assertEqual(loaded, deck)
            self.assertFalse((pdir / "flashcards.json.tmp").exists())

    def test_missing_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_deck(Path(tmp)), [])

    def test_corrupt_file_returns_empty_list_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "flashcards.json").write_text("{ bozuk json", encoding="utf-8")
            self.assertEqual(load_deck(pdir), [])


class DeckSummaryTests(unittest.TestCase):
    def test_counts_due_new_and_suspended_correctly(self):
        deck = [
            {"dueAt": 100, "repetitions": 0, "suspended": False},   # due + new
            {"dueAt": 999999999999, "repetitions": 2, "suspended": False},  # not due
            {"dueAt": 50, "repetitions": 1, "suspended": True},      # suspended, excluded
        ]
        summary = deck_summary(deck, now=200)
        self.assertEqual(summary["totalCards"], 3)
        self.assertEqual(summary["activeCards"], 2)
        self.assertEqual(summary["suspendedCards"], 1)
        self.assertEqual(summary["dueCount"], 1)
        self.assertEqual(summary["newCount"], 1)


if __name__ == "__main__":
    unittest.main()
