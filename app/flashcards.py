"""Kaynak slaytlardan flashcard destesi üretimi ve kalıcı depolama.

İki kart türü üretilir (v1, LLM çağrısı yok — deterministik, anında, ücretsiz):
- "basic": klasik ön yüz=başlık, arka yüz=anlatım+maddeler (app/export.py'nin
  build_anki_tsv'siyle aynı ruhta, ama .tsv yerine yapısal/izlenebilir kartlar).
- "cloze": bir maddeden, kabaca en "belirleyici" kelime boşluk bırakılarak
  üretilen boşluk-doldurma kartı (RemNote tarzı) — Anki'nin flat "başlık->
  anlatım" kartından daha aktif hatırlamayı zorluyor.

Kartların kimliği (id) içeriğe göre SABİT (hash tabanlı) — bu yüzden deste
yeniden üretildiğinde (ör. slaytlar düzenlendikten sonra) DEĞİŞMEYEN kartlar
aynı id'yi koruyup aralıklı tekrar ilerlemesini kaybetmez; değişen/silinen
slaytların kartları doğal olarak listeden düşer (yeni bir id ile yeniden
üretilirler, taze bir kart olarak).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.models import Slide
from app.spaced_repetition import new_card_state

FLASHCARDS_FILE = "flashcards.json"
CLOZE_BLANK = "_____"

# Türkçede sık geçen, "anahtar terim" olma ihtimali düşük kelimeler — cloze
# boşluğu için aday seçilirken elenir. Kapsamlı bir NLP değil, kaba bir filtre.
_STOPWORDS = {
    "bir", "bu", "şu", "ve", "veya", "ile", "için", "gibi", "kadar", "daha",
    "çok", "az", "her", "hiç", "ama", "fakat", "ancak", "de", "da", "ki",
    "mi", "mı", "mu", "mü", "ne", "nasıl", "neden", "niçin", "olan", "olarak",
    "sonra", "önce", "üzerinde", "içinde", "arasında", "birlikte", "değil",
    "yani", "böyle", "şöyle", "tüm", "bütün", "bazı", "diğer", "aynı",
    "burada", "orada", "artık", "zaten", "yalnızca", "sadece", "genellikle",
}
_MIN_CLOZE_TERM_LEN = 4


def _clean(text: str) -> str:
    return (text or "").strip()


def _card_id(kind: str, slide_index: int, front: str) -> str:
    payload = f"{kind}|{slide_index}|{front}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _pick_cloze_term(bullet: str) -> str | None:
    """Bir maddedeki en "belirleyici" kelimeyi (boşluğa alınacak terimi)
    seçer: stopword olmayan, yeterince uzun kelimeler arasından en uzunu.
    Uygun aday yoksa None döner (bu madde cloze için atlanır)."""
    candidates = re.findall(r"[\wığüşöçİĞÜŞÖÇ]+", bullet, flags=re.UNICODE)
    best = None
    for word in candidates:
        if len(word) < _MIN_CLOZE_TERM_LEN or word.isdigit():
            continue
        if word.casefold() in _STOPWORDS:
            continue
        if best is None or len(word) > len(best):
            best = word
    return best


def _make_cloze_card(bullet: str) -> dict[str, str] | None:
    term = _pick_cloze_term(bullet)
    if term is None:
        return None
    front = bullet.replace(term, CLOZE_BLANK, 1)
    if front == bullet:
        return None
    return {"front": front, "back": term}


def _basic_card(slide: Slide) -> dict[str, str] | None:
    title = _clean(slide.title)
    narration = _clean(slide.narration)
    if not title or not narration:
        return None
    back = narration
    bullets = [b for b in (slide.bullets or []) if _clean(b)]
    if bullets:
        bullet_html = "\n".join(f"• {_clean(b)}" for b in bullets)
        back = f"{bullet_html}\n\n{narration}"
    return {"front": title, "back": back}


def _candidate_cards(slides: list[Slide]) -> list[dict[str, Any]]:
    """LLM'siz, deterministik aday kart listesi — henüz zamanlama alanları yok."""
    candidates: list[dict[str, Any]] = []
    for index, slide in enumerate(slides):
        if slide.level == "chapter":
            continue
        basic = _basic_card(slide)
        if basic:
            candidates.append({
                "kind": "basic", "sourceSlideIndex": index,
                "front": basic["front"], "back": basic["back"],
            })
        for bullet in slide.bullets or []:
            bullet = _clean(bullet)
            if not bullet:
                continue
            cloze = _make_cloze_card(bullet)
            if cloze:
                candidates.append({
                    "kind": "cloze", "sourceSlideIndex": index,
                    "front": cloze["front"], "back": cloze["back"],
                })
                break  # slayt başına en fazla 1 cloze — deste şişmesin
    return candidates


def generate_deck(slides: list[Slide], existing_cards: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Slaytlardan taze bir aday kart listesi üretir; içerik değişmemiş kartlar
    için var olan aralıklı-tekrar ilerlemesini (existing_cards) korur.
    """
    existing_by_id = {c["id"]: c for c in (existing_cards or [])}
    deck: list[dict[str, Any]] = []
    for candidate in _candidate_cards(slides):
        card_id = _card_id(candidate["kind"], candidate["sourceSlideIndex"], candidate["front"])
        previous = existing_by_id.get(card_id)
        if previous:
            schedule = {
                "easeFactor": previous.get("easeFactor"),
                "intervalDays": previous.get("intervalDays"),
                "repetitions": previous.get("repetitions"),
                "dueAt": previous.get("dueAt"),
                "lastReviewedAt": previous.get("lastReviewedAt"),
                "suspended": previous.get("suspended", False),
            }
        else:
            schedule = {**new_card_state(), "suspended": False}
        deck.append({"id": card_id, **candidate, **schedule})
    return deck


def _flashcards_path(pdir: Path) -> Path:
    return pdir / FLASHCARDS_FILE


def load_deck(pdir: Path) -> list[dict[str, Any]]:
    path = _flashcards_path(pdir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def save_deck(pdir: Path, deck: list[dict[str, Any]]) -> None:
    path = _flashcards_path(pdir)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def deck_summary(deck: list[dict[str, Any]], now: float | None = None) -> dict[str, Any]:
    import time

    now = time.time() if now is None else now
    active = [c for c in deck if not c.get("suspended")]
    due = [c for c in active if c.get("dueAt", 0) <= now]
    new = [c for c in active if c.get("repetitions", 0) == 0]
    return {
        "totalCards": len(deck),
        "activeCards": len(active),
        "suspendedCards": len(deck) - len(active),
        "dueCount": len(due),
        "newCount": len(new),
    }
