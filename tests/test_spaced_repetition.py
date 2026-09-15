import unittest

from app.spaced_repetition import (
    RATING_AGAIN,
    RATING_EASY,
    RATING_GOOD,
    RATING_HARD,
    new_card_state,
    schedule_review,
)


class NewCardStateTests(unittest.TestCase):
    def test_fresh_card_has_sane_defaults_and_is_immediately_due(self):
        import time

        before = time.time()
        state = new_card_state()
        self.assertEqual(state["repetitions"], 0)
        self.assertEqual(state["easeFactor"], 2.5)
        self.assertIsNone(state["lastReviewedAt"])
        self.assertGreaterEqual(state["dueAt"], before)


class ScheduleReviewTests(unittest.TestCase):
    def test_again_starts_ten_minute_relearning(self):
        card = {"easeFactor": 2.5, "intervalDays": 20.0, "repetitions": 4}
        now = 1_000_000.0

        result = schedule_review(card, RATING_AGAIN, now=now)

        self.assertEqual(result["repetitions"], 5)
        self.assertEqual(result["state"], "relearning")
        self.assertEqual(result["lapses"], 1)
        self.assertEqual(result["dueAt"], now + 600)

    def test_first_good_advances_to_ten_minute_learning_step(self):
        card = new_card_state()
        now = 1_000_000.0

        result = schedule_review(card, RATING_GOOD, now=now)

        self.assertEqual(result["repetitions"], 1)
        self.assertEqual(result["state"], "learning")
        self.assertEqual(result["stepIndex"], 1)
        self.assertEqual(result["dueAt"], now + 600)

    def test_legacy_review_uses_existing_interval_and_ease(self):
        card = {"easeFactor": 2.5, "intervalDays": 1.0, "repetitions": 1}
        now = 1_000_000.0

        result = schedule_review(card, RATING_GOOD, now=now)

        self.assertEqual(result["repetitions"], 2)
        self.assertEqual(result["intervalDays"], 2.0)

    def test_third_and_later_good_reviews_multiply_interval_by_ease_factor(self):
        card = {"easeFactor": 2.0, "intervalDays": 6.0, "repetitions": 2}

        result = schedule_review(card, RATING_GOOD, now=0.0)

        self.assertEqual(result["repetitions"], 3)
        self.assertEqual(result["intervalDays"], 12.0)  # 6 * 2.0

    def test_easy_increases_ease_factor_more_than_good(self):
        card_good = {"easeFactor": 2.5, "intervalDays": 6.0, "repetitions": 2}
        card_easy = dict(card_good)

        good_result = schedule_review(card_good, RATING_GOOD, now=0.0)
        easy_result = schedule_review(card_easy, RATING_EASY, now=0.0)

        self.assertGreater(easy_result["easeFactor"], good_result["easeFactor"])

    def test_repeated_again_ratings_never_drop_ease_factor_below_floor(self):
        card = {"easeFactor": 1.35, "intervalDays": 1.0, "repetitions": 0}

        result = schedule_review(card, RATING_AGAIN, now=0.0)

        self.assertGreaterEqual(result["easeFactor"], 1.3)

    def test_hard_rating_still_advances_repetitions_but_less_than_good(self):
        card = {"easeFactor": 2.5, "intervalDays": 6.0, "repetitions": 2}
        card_copy = dict(card)

        hard_result = schedule_review(card, RATING_HARD, now=0.0)
        good_result = schedule_review(card_copy, RATING_GOOD, now=0.0)

        self.assertEqual(hard_result["repetitions"], 3)
        self.assertLess(hard_result["easeFactor"], good_result["easeFactor"])

    def test_unknown_rating_raises_value_error(self):
        with self.assertRaises(ValueError):
            schedule_review(new_card_state(), "excellent")

    def test_input_card_dict_is_not_mutated(self):
        card = {"easeFactor": 2.5, "intervalDays": 6.0, "repetitions": 2}
        snapshot = dict(card)

        schedule_review(card, RATING_GOOD, now=0.0)

        self.assertEqual(card, snapshot)


if __name__ == "__main__":
    unittest.main()
