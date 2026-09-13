"""SM-2 tabanlı aralıklı tekrar (spaced repetition) zamanlayıcısı.

Anki'nin kendisinin de temel aldığı SuperMemo-2 algoritmasının, modern Anki'nin
4 butonlu (Tekrar / Zor / İyi / Kolay) arayüzüne uyarlanmış hali. Dışarıdan bir
kütüphane/servis kullanılmıyor — tamamen yerel, saf Python.
"""

from __future__ import annotations

import time
from typing import Any

RATING_AGAIN = "again"
RATING_HARD = "hard"
RATING_GOOD = "good"
RATING_EASY = "easy"
RATINGS = (RATING_AGAIN, RATING_HARD, RATING_GOOD, RATING_EASY)

# SM-2'nin 0-5 kalite puanına kaba bir eşleme — Anki de dahili olarak buna benzer bir dönüşüm yapar.
_QUALITY_BY_RATING = {RATING_AGAIN: 0, RATING_HARD: 3, RATING_GOOD: 4, RATING_EASY: 5}
_MIN_EASE_FACTOR = 1.3
_DEFAULT_EASE_FACTOR = 2.5
_SECONDS_PER_DAY = 86_400


def new_card_state() -> dict[str, Any]:
    """Hiç incelenmemiş taze bir kartın zamanlama alanları."""
    return {
        "easeFactor": _DEFAULT_EASE_FACTOR,
        "intervalDays": 0.0,
        "repetitions": 0,
        "dueAt": time.time(),  # hemen incelenebilir
        "lastReviewedAt": None,
    }


def schedule_review(card: dict[str, Any], rating: str, now: float | None = None) -> dict[str, Any]:
    """Bir inceleme puanından sonra kartın zamanlama alanlarını günceller.

    card: en azından easeFactor/intervalDays/repetitions alanlarını taşıyan dict
    (new_card_state() veya önceki bir schedule_review() çıktısı).
    rating: RATINGS içinden biri.
    Dönüş: GÜNCELLENMİŞ zamanlama alanlarını içeren yeni bir dict (girdi
    mutasyona uğratılmaz).
    """
    if rating not in _QUALITY_BY_RATING:
        raise ValueError(f"Bilinmeyen değerlendirme: {rating!r} (beklenen: {RATINGS})")
    now = time.time() if now is None else now
    quality = _QUALITY_BY_RATING[rating]

    ease_factor = float(card.get("easeFactor", _DEFAULT_EASE_FACTOR))
    repetitions = int(card.get("repetitions", 0))

    if quality < 3:
        # "Tekrar" — kart unutulmuş sayılır, baştan başlar ama ease_factor'ü de
        # (SM-2'nin orijinal tanımına sadık kalarak) aşağı çeker.
        repetitions = 0
        interval_days = 1.0
    else:
        if repetitions == 0:
            interval_days = 1.0
        elif repetitions == 1:
            interval_days = 6.0
        else:
            interval_days = round(float(card.get("intervalDays", 1.0)) * ease_factor, 2)
        repetitions += 1

    ease_factor = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ease_factor = max(_MIN_EASE_FACTOR, round(ease_factor, 3))

    return {
        "easeFactor": ease_factor,
        "intervalDays": interval_days,
        "repetitions": repetitions,
        "dueAt": now + interval_days * _SECONDS_PER_DAY,
        "lastReviewedAt": now,
    }
