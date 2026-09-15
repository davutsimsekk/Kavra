"""Local Anki-style learning steps and SM-2-inspired intervals (not FSRS).
Legacy due dates/ease are preserved until a card is reviewed.
"""
from __future__ import annotations
import math
import time

RATING_AGAIN, RATING_HARD, RATING_GOOD, RATING_EASY = "again", "hard", "good", "easy"
RATINGS = (RATING_AGAIN, RATING_HARD, RATING_GOOD, RATING_EASY)
DEFAULT_OPTIONS = {
    "newPerDay": 20, "reviewsPerDay": 200, "learningSteps": [1, 10],
    "relearningSteps": [10], "graduatingDays": 1, "easyDays": 4,
    "maxIntervalDays": 36500, "dayStartsAt": 4, "burySiblings": True,
}
SCHEDULE_FIELDS = ("easeFactor", "intervalDays", "repetitions", "dueAt",
                   "lastReviewedAt", "state", "stepIndex", "lapses", "reviewVersion")


def validate_options(values=None):
    values = {} if values is None else values
    if not isinstance(values, dict) or set(values) - set(DEFAULT_OPTIONS):
        raise ValueError("Geçersiz deste ayarı.")
    result = {**DEFAULT_OPTIONS, **values}
    for key, maximum in (("newPerDay", 9999), ("reviewsPerDay", 9999),
                         ("graduatingDays", 36500), ("easyDays", 36500),
                         ("maxIntervalDays", 36500), ("dayStartsAt", 23)):
        value = result[key]
        minimum = 0 if key in {"newPerDay", "reviewsPerDay", "dayStartsAt"} else 1
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{key}: {minimum}–{maximum} arasında tam sayı gir.")
    for key in ("learningSteps", "relearningSteps"):
        steps = result[key]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
            raise ValueError("Öğrenme adımları 1–8 süre içermeli.")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
               not math.isfinite(v) or not 0.1 <= v < 1440 for v in steps):
            raise ValueError("Öğrenme adımları dakika olarak 0,1 ile 1440 arasında olmalı.")
        if steps != sorted(set(steps)):
            raise ValueError("Öğrenme adımlarını küçükten büyüğe, tekrarsız gir.")
        result[key] = list(steps)
    if not isinstance(result["burySiblings"], bool):
        raise ValueError("Kardeş kartları ertele seçeneği geçersiz.")
    if not result["graduatingDays"] <= result["easyDays"] <= result["maxIntervalDays"]:
        raise ValueError("Kolay aralığı mezuniyet aralığından kısa, üst sınırdan uzun olamaz.")
    return result


def card_state(card):
    state = card.get("state")
    if state in {"learning", "relearning", "review"}:
        return state
    return "review" if card.get("lastReviewedAt") is not None or card.get("repetitions", 0) > 0 else "new"


def new_card_state():
    return {"easeFactor": 2.5, "intervalDays": 0.0, "repetitions": 0,
            "dueAt": time.time(), "lastReviewedAt": None, "state": "new",
            "stepIndex": 0, "lapses": 0, "reviewVersion": 0}


def schedule_review(card, rating, now=None, options=None):
    if rating not in RATINGS:
        raise ValueError(f"Bilinmeyen değerlendirme: {rating!r}")
    opts = validate_options(options)
    now = time.time() if now is None else now
    state = card_state(card)
    ease = max(1.3, float(card.get("easeFactor") or 2.5))
    interval = float(card.get("intervalDays") or 0)
    step = int(card.get("stepIndex") or 0)
    lapses = int(card.get("lapses") or 0)
    reps = int(card.get("repetitions") or 0)
    if state == "review":
        days = max(1, interval)
        good = max(days + 1, round(days * ease))
        if rating == "again":
            state, step = "relearning", 0
            interval = max(1, round(days * 0.2))
            delay = opts["relearningSteps"][0] * 60
            ease = max(1.3, ease - 0.2)
            lapses += 1
        else:
            if rating == "hard":
                interval, ease = max(days + 1, round(days * 1.2)), max(1.3, ease - 0.15)
            elif rating == "easy":
                interval, ease = max(good + 1, round(days * ease * 1.3)), ease + 0.15
            else:
                interval = good
            interval = min(interval, opts["maxIntervalDays"])
            delay = interval * 86400
    else:
        relearning = state == "relearning"
        steps = opts["relearningSteps"] if relearning else opts["learningSteps"]
        step = min(max(step, 0), len(steps) - 1)
        state = "relearning" if relearning else "learning"
        if rating == "easy":
            state = "review"
            interval = max(interval, opts["easyDays"])
        elif rating == "again":
            step, delay = 0, steps[0] * 60
        elif rating == "hard":
            delay = ((steps[0] + steps[1]) / 2 if step == 0 and len(steps) > 1
                     else steps[step] * 1.5 if len(steps) == 1 else steps[step]) * 60
        elif step + 1 < len(steps):
            step += 1
            delay = steps[step] * 60
        else:
            state = "review"
            interval = max(1, interval) if relearning else opts["graduatingDays"]
        if state == "review":
            step = 0
            interval = min(interval, opts["maxIntervalDays"])
            delay = interval * 86400
    return {"easeFactor": round(ease, 3), "intervalDays": interval,
            "repetitions": reps + 1, "dueAt": now + delay, "lastReviewedAt": now,
            "state": state, "stepIndex": step, "lapses": lapses,
            "reviewVersion": int(card.get("reviewVersion") or 0) + 1}
