import tempfile
import unittest
from pathlib import Path

from app.flashcards import (
    _basic_card,
    _candidate_cards,
    _coerce_llm_cards,
    _make_cloze_card,
    _pick_cloze_term,
    create_deck,
    deck_summary,
    delete_deck,
    generate_deck,
    generate_llm_deck,
    get_deck,
    list_decks,
    regenerate_deck_cards,
    update_card,
)
from app.models import Slide


class FakeGenerator:
    """app.llm.* sağlayıcılarının ortak arayüzünü taklit eder (bkz. GeminiNarrationGenerator,
    OpenAICompatibleNarrationGenerator, AgentCliNarrationGenerator._call)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def _call(self, prompt):
        self.calls.append(prompt)
        return self._responses.pop(0)


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


class CoerceLlmCardsTests(unittest.TestCase):
    def test_drops_items_missing_front_or_back(self):
        cards = _coerce_llm_cards([
            {"kind": "basic", "front": "Soru", "back": "Cevap"},
            {"kind": "basic", "front": "", "back": "Cevap"},
            {"kind": "basic", "front": "Soru", "back": ""},
            "not a dict",
        ])
        self.assertEqual(len(cards), 1)

    def test_unknown_kind_defaults_to_basic(self):
        cards = _coerce_llm_cards([{"kind": "mystery", "front": "Soru", "back": "Cevap"}])
        self.assertEqual(cards[0]["kind"], "basic")

    def test_cloze_without_blank_marker_falls_back_to_basic(self):
        cards = _coerce_llm_cards([{"kind": "cloze", "front": "Boşluksuz cümle", "back": "terim"}])
        self.assertEqual(cards[0]["kind"], "basic")

    def test_caps_card_count_at_the_hard_limit(self):
        items = [{"kind": "basic", "front": f"S{i}", "back": f"C{i}"} for i in range(100)]
        cards = _coerce_llm_cards(items)
        self.assertEqual(len(cards), 60)


class GenerateLlmDeckTests(unittest.TestCase):
    def _slides(self):
        return [Slide(title="Pointer", narration="Pointer bir bellek adresi tutar.", bullets=["Adres tutar"])]

    def test_parses_a_clean_json_response_into_scheduled_cards(self):
        generator = FakeGenerator([
            '[{"kind": "basic", "front": "Pointer nedir?", "back": "Bir bellek adresi tutar."}]'
        ])
        deck = generate_llm_deck(self._slides(), generator)
        self.assertEqual(len(deck), 1)
        card = deck[0]
        self.assertEqual(card["front"], "Pointer nedir?")
        self.assertEqual(card["repetitions"], 0)
        self.assertFalse(card["suspended"])
        self.assertIn("id", card)

    def test_retries_with_a_fix_prompt_when_output_is_not_valid_json(self):
        generator = FakeGenerator([
            "üzgünüm, şu an kart üretemiyorum.",
            '[{"kind": "basic", "front": "a", "back": "b"}]',
        ])
        deck = generate_llm_deck(self._slides(), generator)
        self.assertEqual(len(deck), 1)
        self.assertEqual(len(generator.calls), 2)

    def test_raises_when_model_returns_no_usable_cards(self):
        generator = FakeGenerator(['[{"kind": "basic", "front": "", "back": ""}]'])
        with self.assertRaises(ValueError):
            generate_llm_deck(self._slides(), generator)

    def test_raises_when_slides_have_no_narration_content(self):
        generator = FakeGenerator([])
        empty_slides = [Slide(title="Bölüm", level="chapter", narration="")]
        with self.assertRaises(ValueError):
            generate_llm_deck(empty_slides, generator)

    def test_count_and_focus_prompt_are_included_in_the_prompt_sent_to_the_model(self):
        generator = FakeGenerator(['[{"kind": "basic", "front": "a", "back": "b"}]'])
        generate_llm_deck(self._slides(), generator, count=12, focus_prompt="pointer'lara odaklan")
        sent_prompt = generator.calls[0]
        self.assertIn("12", sent_prompt)
        self.assertIn("pointer'lara odaklan", sent_prompt)


class MultiDeckManagementTests(unittest.TestCase):
    def _slides(self):
        return [Slide(title="X", narration="Kesme isteği önceliklendirme anlatımı.", bullets=["İşlemci kesme isteği alır"])]

    def test_list_decks_is_empty_for_a_project_with_no_decks(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list_decks(Path(tmp)), [])

    def test_create_deck_persists_and_is_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            deck = create_deck(pdir, "Sınav Öncesi", "static", self._slides())
            self.assertEqual(deck["name"], "Sınav Öncesi")
            self.assertEqual(deck["kind"], "static")
            self.assertGreater(len(deck["cards"]), 0)

            decks = list_decks(pdir)
            self.assertEqual(len(decks), 1)
            self.assertEqual(decks[0]["id"], deck["id"])

    def test_multiple_decks_coexist_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            first = create_deck(pdir, "Deste A", "static", self._slides())
            second = create_deck(pdir, "Deste B", "static", self._slides())

            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(len(list_decks(pdir)), 2)

    def test_llm_kind_requires_a_generator(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                create_deck(Path(tmp), "YZ Destesi", "llm", self._slides())

    def test_llm_kind_creates_deck_using_the_given_generator(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            responses = ['[{"kind": "basic", "front": "Pointer nedir?", "back": "Bir adres tutar."}]']
            generator = FakeGenerator(responses)
            deck = create_deck(pdir, "YZ Destesi", "llm", self._slides(),
                                generator=generator, focus_prompt="pointer'lara odaklan")
            self.assertEqual(deck["kind"], "llm")
            self.assertEqual(len(deck["cards"]), 1)
            self.assertEqual(deck["cards"][0]["front"], "Pointer nedir?")
            self.assertEqual(deck["focusPrompt"], "pointer'lara odaklan")
            self.assertEqual(len(generator.calls), 1)

    def test_llm_deck_is_persisted_and_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            responses = ['[{"kind": "basic", "front": "Soru", "back": "Cevap"}]']
            create_deck(pdir, "YZ Destesi", "llm", self._slides(), generator=FakeGenerator(responses))
            decks = list_decks(pdir)
            self.assertEqual(len(decks), 1)
            self.assertEqual(decks[0]["kind"], "llm")

    def test_regenerate_is_rejected_for_llm_decks(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            responses = ['[{"kind": "basic", "front": "Soru", "back": "Cevap"}]']
            deck = create_deck(pdir, "YZ Destesi", "llm", self._slides(), generator=FakeGenerator(responses))
            with self.assertRaises(ValueError):
                regenerate_deck_cards(pdir, deck["id"], self._slides())

    def test_unknown_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                create_deck(Path(tmp), "Deste", "sihirli", self._slides())

    def test_get_deck_returns_none_for_unknown_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(get_deck(Path(tmp), "yok"))

    def test_regenerate_deck_cards_preserves_progress_for_that_deck_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            slides = self._slides()
            deck = create_deck(pdir, "Deste A", "static", slides)
            card_id = deck["cards"][0]["id"]
            update_card(pdir, deck["id"], card_id, {"repetitions": 4})

            regenerated = regenerate_deck_cards(pdir, deck["id"], slides)

            card = next(c for c in regenerated["cards"] if c["id"] == card_id)
            self.assertEqual(card["repetitions"], 4)

    def test_regenerate_unknown_deck_raises_key_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                regenerate_deck_cards(Path(tmp), "yok", self._slides())

    def test_update_card_persists_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            deck = create_deck(pdir, "Deste A", "static", self._slides())
            card_id = deck["cards"][0]["id"]

            update_card(pdir, deck["id"], card_id, {"suspended": True})

            reloaded = get_deck(pdir, deck["id"])
            card = next(c for c in reloaded["cards"] if c["id"] == card_id)
            self.assertTrue(card["suspended"])

    def test_update_unknown_card_raises_key_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            deck = create_deck(pdir, "Deste A", "static", self._slides())
            with self.assertRaises(KeyError):
                update_card(pdir, deck["id"], "yok", {"suspended": True})

    def test_delete_deck_removes_it_from_the_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            deck = create_deck(pdir, "Deste A", "static", self._slides())
            delete_deck(pdir, deck["id"])
            self.assertEqual(list_decks(pdir), [])

    def test_delete_unknown_deck_raises_key_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                delete_deck(Path(tmp), "yok")


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
