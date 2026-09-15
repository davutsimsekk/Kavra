import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import flashcard_study as study
from app.flashcards import create_deck, get_deck, _save_deck_file, regenerate_deck_cards
from app.models import Slide
from app.spaced_repetition import new_card_state, schedule_review, validate_options, card_state
from studio_web.api import app


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdir = Path(self.tmp.name)
        self.deck = create_deck(self.pdir, "Test", "static", [])
        self.deck["cards"] = [
            {"id": f"card{i}", "front": f"Soru {i}", "back": "Cevap", "kind": "basic",
             "sourceSlideIndex": None, **new_card_state()} for i in range(4)]
        self.deck["options"] = validate_options({"newPerDay": 2, "reviewsPerDay": 1})
        _save_deck_file(self.pdir, self.deck)
        self.id = self.deck["id"]
        self.now = datetime(2026, 9, 15, 12).timestamp()

    def tearDown(self):
        self.tmp.cleanup()

    def review(self, card_id="card0", rating="again", now=None, version=None):
        return study.review(self.pdir, self.id, card_id, rating, version, 3,
                            self.now if now is None else now)

    def test_again_returns_in_same_session_and_does_not_consume_another_new_slot(self):
        result = self.review()
        self.assertEqual(result["cards"][0]["dueAt"], self.now + 60)
        self.assertEqual(result["stats"]["todayNew"], 1)
        self.assertNotIn("card0", result["study"]["queueIds"])
        later = study.study_payload(get_deck(self.pdir, self.id), self.now + 61)
        self.assertEqual(later["study"]["nextCard"]["id"], "card0")
        self.review(now=self.now + 61)
        self.assertEqual(study.study_payload(get_deck(self.pdir, self.id), self.now + 61)["stats"]["todayNew"], 1)

    def test_daily_limit_survives_reopening_and_study_day_rollover(self):
        self.review(rating="easy")
        result = self.review("card1", "easy")
        self.assertEqual(result["study"]["newCount"], 0)
        reopened = study.study_payload(get_deck(self.pdir, self.id), self.now)
        self.assertEqual(reopened["stats"]["todayNew"], 2)
        tomorrow = study.study_payload(get_deck(self.pdir, self.id), self.now + 86400)
        self.assertEqual(tomorrow["study"]["newCount"], 2)
        with self.assertRaises(study.StudyConflict):
            self.review("card2", "easy")

    def test_relearning_not_blocked_by_review_limit(self):
        deck = get_deck(self.pdir, self.id)
        for c in deck["cards"][:2]:
            c.update(state="review", repetitions=4, intervalDays=10, dueAt=self.now - 10, lastReviewedAt=self.now - 86400)
        _save_deck_file(self.pdir, deck)
        self.review(rating="again")
        payload = study.study_payload(get_deck(self.pdir, self.id), self.now + 601)
        self.assertEqual(payload["study"]["nextCard"]["id"], "card0")
        self.assertNotIn("card1", payload["study"]["queueIds"])
        self.assertEqual(payload["study"]["reviewRemaining"], 0)

    def test_undo_restores_history_budget_and_previous_card_state(self):
        before = copy.deepcopy(self.deck["cards"][0])
        result = self.review(rating="easy")
        reopened = get_deck(self.pdir, self.id)
        result = study.undo_review(self.pdir, self.id, result["study"]["undoId"], self.now)
        self.assertEqual(result["stats"]["todayCount"], 0)
        self.assertEqual(result["study"]["newCount"], 2)
        self.assertEqual(result["cards"][0]["dueAt"], before["dueAt"])
        self.assertEqual(result["cards"][0]["state"], "new")
        self.assertEqual(len(reopened["reviewLog"]), 1)

    def test_concurrent_duplicate_review_is_rejected(self):
        def attempt():
            try:
                self.review(version=0)
                return True
            except study.StudyConflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(get_deck(self.pdir, self.id)["reviewLog"]), 1)

    def test_siblings_buried_until_rollover_and_undo_restores(self):
        deck = get_deck(self.pdir, self.id)
        for c in deck["cards"][:2]:
            c["sourceSlideIndex"] = 0
        _save_deck_file(self.pdir, deck)
        result = self.review()
        self.assertNotIn("card1", result["study"]["queueIds"])
        self.assertEqual(result["summary"]["buriedCards"], 1)
        result = study.undo_review(self.pdir, self.id, now=self.now)
        self.assertIn("card1", result["study"]["queueIds"])
        self.assertEqual(result["summary"]["buriedCards"], 0)

    def test_suspend_and_bury_do_not_reset_schedule(self):
        before = copy.deepcopy(get_deck(self.pdir, self.id)["cards"][0])
        result = study.bulk_action(self.pdir, self.id, ["card0"], "suspend", now=self.now)
        self.assertNotIn("card0", result["study"]["queueIds"])
        result = study.bulk_action(self.pdir, self.id, ["card0"], "resume", now=self.now)
        self.assertEqual(result["cards"][0]["dueAt"], before["dueAt"])
        result = study.bulk_action(self.pdir, self.id, ["card0"], "bury", now=self.now)
        self.assertNotIn("card0", result["study"]["queueIds"])
        self.assertIn("card0", study.study_payload(get_deck(self.pdir, self.id), self.now + 86400)["study"]["queueIds"])

    def test_old_cards_infer_state_without_changing_due_dates(self):
        old = {"id": "old", "front": "S", "back": "C", "repetitions": 0,
               "lastReviewedAt": self.now - 86400, "dueAt": self.now + 86400, "easeFactor": 1.7}
        deck = {**self.deck, "cards": [old]}
        result = study.study_payload(deck, self.now)
        self.assertEqual(result["summary"]["newCount"], 0)
        self.assertEqual(result["cards"][0], old)
        self.assertEqual(card_state(old), "review")

    def test_options_validate_and_preserve_due_dates(self):
        before = copy.deepcopy(get_deck(self.pdir, self.id)["cards"])
        result = study.save_options(self.pdir, self.id, {"newPerDay": 0}, "Yeni isim")
        self.assertEqual(result["name"], "Yeni isim")
        self.assertEqual(result["cards"], before)
        self.assertEqual(result["study"]["newCount"], 0)
        for bad in ({"learningSteps": []}, {"learningSteps": [10, 1]}, {"newPerDay": -1},
                    {"learningSteps": [float("nan")]}, {"dayStartsAt": 24}, {"easyDays": 0}):
            with self.assertRaises(ValueError):
                study.save_options(self.pdir, self.id, bad)

    def test_multi_cloze_hints_and_reverse_cards(self):
        result = study.add_note(self.pdir, self.id, "A {{c1::bir::ipucu}} ve {{c2::iki}}; {{c1::üç}}.",
                                "Açıklama", "cloze", ["test"])
        first, second = result["cards"][-2:]
        self.assertEqual(first["front"], "A [ipucu] ve iki; […].")
        self.assertEqual(second["front"], "A bir ve […]; üç.")
        self.assertEqual(first["noteId"], second["noteId"])
        self.assertIn("A bir ve iki; üç.", first["back"])
        result = study.add_note(self.pdir, self.id, "Ön", "Arka", "reverse")
        self.assertEqual(result["cards"][-2]["front"], result["cards"][-1]["back"])

    def test_import_deduplicates_and_is_atomic_on_bad_row(self):
        result = study.import_text(self.pdir, self.id, "Front\tBack\nYeni\tCevap\ttag\nYeni\tCevap")
        self.assertEqual(result["importResult"], {"added": 1, "skipped": 1})
        before = copy.deepcopy(get_deck(self.pdir, self.id))
        with self.assertRaises(ValueError):
            study.import_text(self.pdir, self.id, "Başka\tCevap\nbozuk")
        self.assertEqual(get_deck(self.pdir, self.id), before)

    def test_export_import_roundtrip_preserves_code_tags_and_newlines(self):
        from app.flashcards import build_deck_anki_txt
        original = {"front": "#define X", "back": '#include <stdio.h>\n"a" & b\tvalue', "tags": ["code"]}
        exported = build_deck_anki_txt({"cards": [original]})
        result = study.import_text(self.pdir, self.id, exported)
        card = result["cards"][-1]
        for key in ("front", "back", "tags"):
            self.assertEqual(card[key], original[key])

    def test_regeneration_keeps_new_scheduler_fields_and_metadata(self):
        slides = [Slide(title="Başlık", narration="Açıklama")]
        deck = create_deck(self.pdir, "Kaynak", "static", slides)
        deck["cards"][0].update(state="learning", stepIndex=1, tags=["kalıcı"], flagged=True,
                                buriedUntil=self.now + 30, reviewVersion=5)
        _save_deck_file(self.pdir, deck)
        result = regenerate_deck_cards(self.pdir, deck["id"], slides)
        self.assertEqual(result["cards"], deck["cards"])

    def test_day_starts_at_four(self):
        before = datetime(2026, 9, 15, 3, 59).timestamp()
        after = datetime(2026, 9, 15, 4, 0).timestamp()
        self.assertEqual(study.day_key(before), "2026-09-14")
        self.assertEqual(study.day_key(after), "2026-09-15")
        self.assertEqual(study.next_day(before), after)


class StudyApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdir = self.root / "demo"
        self.pdir.mkdir()
        self.patch = patch("studio_web.api.PROJECTS_DIR", self.root)
        self.patch.start()
        self.client = TestClient(app, base_url="http://localhost")
        self.base = "/api/projects/demo/flashcards"
        self.deck = self.client.post(self.base + "/blank", json={"name": "Boş"}).json()
        self.url = self.base + "/decks/" + self.deck["id"]

    def tearDown(self):
        self.client.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_complete_local_note_review_undo_flow_and_validation(self):
        response = self.client.post(self.url + "/notes", json={"front": "Soru", "back": "Cevap", "cardType": "basic"})
        self.assertEqual(response.status_code, 200)
        card = response.json()["study"]["nextCard"]
        response = self.client.post(self.url + "/study/review", json={"cardId": card["id"], "rating": "again", "expectedVersion": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stats"]["todayCount"], 1)
        response = self.client.post(self.url + "/study/undo", json={"eventId": response.json()["study"]["undoId"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stats"]["todayCount"], 0)
        self.assertEqual(self.client.patch(self.url + "/options", json={"options": {"newPerDay": -2}}).status_code, 400)
        self.assertEqual(self.client.post(self.url + "/bulk", json={"ids": ["missing"], "action": "bury"}).status_code, 404)
        self.assertEqual(self.client.post(self.url + "/study/review", json={"cardId": card["id"], "rating": "good", "expectedVersion": 0}).status_code, 409)


class LearningStepsTests(unittest.TestCase):
    def test_four_buttons_have_distinct_initial_delays(self):
        card = new_card_state()
        delays = [schedule_review(card, r, 0)["dueAt"] for r in ("again", "hard", "good", "easy")]
        self.assertEqual(delays, [60, 330, 600, 345600])

    def test_good_graduates_only_after_last_learning_step(self):
        card = schedule_review(new_card_state(), "good", 0)
        self.assertEqual(card["state"], "learning")
        card = schedule_review(card, "good", 600)
        self.assertEqual(card["state"], "review")
        self.assertEqual(card["dueAt"], 600 + 86400)

    def test_relearning_graduates_and_retains_lapse(self):
        card = {"state": "review", "intervalDays": 20, "easeFactor": 2.5, "repetitions": 4}
        card = schedule_review(card, "again", 0)
        self.assertEqual(card["state"], "relearning")
        self.assertEqual(card["lapses"], 1)
        card = schedule_review(card, "good", 600)
        self.assertEqual(card["state"], "review")
        self.assertEqual(card["lapses"], 1)
        self.assertEqual(card["intervalDays"], 4)
