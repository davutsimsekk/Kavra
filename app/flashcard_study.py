"""Persistent, local study queues, preferences, review history and card tools."""
from __future__ import annotations

import copy
import csv
import io
import re
import math
import time
import uuid
from datetime import datetime, timedelta

from app.flashcards import get_deck, _save_deck_file, synchronized
from app.spaced_repetition import (
    RATINGS, SCHEDULE_FIELDS, card_state, new_card_state, schedule_review, validate_options,
)


class StudyConflict(ValueError):
    pass


def day_key(timestamp, hour=4):
    return (datetime.fromtimestamp(timestamp) - timedelta(hours=hour)).date().isoformat()


def next_day(timestamp, hour=4):
    local = datetime.fromtimestamp(timestamp)
    boundary = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if boundary <= local:
        boundary += timedelta(days=1)
    return boundary.timestamp()


def _load(pdir, deck_id):
    deck = get_deck(pdir, deck_id)
    if deck is None:
        raise KeyError(deck_id)
    return deck


def normalize_tags(value):
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("Etiketleri en fazla 50 öğelik liste olarak gönder.")
    if any(not isinstance(tag, str) or len(tag) > 80 or any(c.isspace() for c in tag) for tag in value):
        raise ValueError("Etiketlerde boşluk kullanma; her etiket en fazla 80 karakter olabilir.")
    return list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))


def _active_events(deck):
    return [e for e in deck.get("reviewLog", []) if not e.get("undone")]


def study_payload(deck, now=None):
    now = time.time() if now is None else now
    options = validate_options(deck.get("options"))
    hour = options["dayStartsAt"]
    today = day_key(now, hour)
    events = _active_events(deck)
    daily = [e for e in events if day_key(e["at"], hour) == today]
    new_seen = {e["cardId"] for e in daily if e["beforeState"] == "new"}
    review_seen = {e["cardId"] for e in daily if e["beforeState"] == "review"}
    cards = deck.get("cards", [])
    active = [c for c in cards if not c.get("suspended") and c.get("buriedUntil", 0) <= now]
    learning = sorted([c for c in active if card_state(c) in {"learning", "relearning"}],
                      key=lambda c: c["dueAt"])
    reviews = sorted([c for c in active if card_state(c) == "review" and c.get("dueAt", 0) <= now],
                     key=lambda c: c.get("dueAt", 0))
    new = [c for c in active if card_state(c) == "new"]
    new_remaining = max(0, options["newPerDay"] - len(new_seen))
    review_remaining = max(0, options["reviewsPerDay"] - len(review_seen))
    ready_learning = [c for c in learning if c["dueAt"] <= now]
    # A card already counted against today's review cap may finish its steps.
    ready_reviews = [c for c in reviews if c["id"] in review_seen]
    ready_reviews += [c for c in reviews if c["id"] not in review_seen][:review_remaining]
    ready_new = new[:new_remaining]
    queue = ready_learning + ready_reviews + ready_new
    next_card = None
    if queue:
        card = queue[0]
        next_card = {**card, "state": card_state(card),
                     "intervals": {r: schedule_review(card, r, now, options)["dueAt"] - now for r in RATINGS}}
    future = [c["dueAt"] for c in learning if c["dueAt"] > now]
    ratings = {r: sum(e["rating"] == r for e in daily) for r in RATINGS}
    # Seven local study days; only history collected by this version is counted.
    history = []
    anchor = datetime.fromisoformat(today)
    for offset in range(6, -1, -1):
        key = (anchor - timedelta(days=offset)).date().isoformat()
        matches = [e for e in events if day_key(e["at"], hour) == key]
        history.append({"day": key, "count": len(matches),
                        "again": sum(e["rating"] == "again" for e in matches)})
    forecast = []
    for offset in range(7):
        key = (anchor + timedelta(days=offset)).date().isoformat()
        count = sum(card_state(c) != "new" and day_key(c.get("dueAt", now), hour) == key for c in active)
        forecast.append({"day": key, "count": count})
    last = events[-1] if events else None
    summary = {
        "totalCards": len(cards), "activeCards": len(active),
        "suspendedCards": sum(bool(c.get("suspended")) for c in cards),
        "buriedCards": sum(c.get("buriedUntil", 0) > now for c in cards),
        "newCount": len(new), "dueCount": len(queue),
        "learningCount": len(learning), "reviewCount": len(reviews),
    }
    return {
        **{k: v for k, v in deck.items() if k != "reviewLog"},
        "options": options, "summary": summary,
        "study": {"readyCount": len(queue), "queueIds": [c["id"] for c in queue],
                  "newCount": len(ready_new), "learningCount": len(ready_learning),
                  "reviewCount": len(ready_reviews), "nextCard": next_card,
                  "nextLearningAt": min(future) if future else None,
                  "newRemaining": new_remaining, "reviewRemaining": review_remaining,
                  "undoId": last["id"] if last else None},
        "stats": {"todayCount": len(daily), "todayNew": len(new_seen),
                  "todayReviews": len(review_seen), "ratings": ratings,
                  "seconds": round(sum(e.get("seconds", 0) for e in daily)),
                  "history": history, "forecast": forecast, "totalReviews": len(events),
                  "matureCards": sum(card_state(c) == "review" and c.get("intervalDays", 0) >= 21 for c in cards)},
    }


@synchronized
def save_options(pdir, deck_id, values, name=None):
    deck = _load(pdir, deck_id)
    deck["options"] = validate_options({**deck.get("options", {}), **values})
    if name is not None:
        if not isinstance(name, str) or not name.strip() or len(name) > 150:
            raise ValueError("Deste adı 1–150 karakter olmalı.")
        deck["name"] = name.strip()
    deck["schemaVersion"] = 2
    _save_deck_file(pdir, deck)
    return study_payload(deck)


@synchronized
def review(pdir, deck_id, card_id, rating, expected_version=None, seconds=0, now=None):
    now = time.time() if now is None else now
    if rating not in RATINGS:
        raise ValueError("Geçersiz değerlendirme.")
    deck = _load(pdir, deck_id)
    card = next((c for c in deck["cards"] if c["id"] == card_id), None)
    if card is None:
        raise KeyError(card_id)
    if expected_version is not None and expected_version != card.get("reviewVersion", 0):
        raise StudyConflict("Bu kart başka bir işlemde değişti. Desteyi yenile.")
    payload = study_payload(deck, now)
    if card_id not in payload["study"]["queueIds"]:
        raise StudyConflict("Kart henüz sırada değil, ertelenmiş veya günlük sınır dolmuş.")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        raise ValueError("Çalışma süresi geçersiz.")
    before = {k: copy.deepcopy(card[k]) for k in SCHEDULE_FIELDS if k in card}
    state = card_state(card)
    event = {"id": uuid.uuid4().hex, "cardId": card_id, "at": now, "rating": rating,
             "before": before, "beforeState": state,
             "seconds": min(300, max(0, float(seconds or 0))), "siblings": []}
    changes = schedule_review(card, rating, now, payload["options"])
    card.update(changes)
    event["afterVersion"] = card["reviewVersion"]
    if payload["options"]["burySiblings"]:
        source = card.get("sourceSlideIndex")
        for sibling in deck["cards"]:
            related = (source is not None and sibling.get("sourceSlideIndex") == source) or (
                card.get("noteId") and sibling.get("noteId") == card["noteId"])
            if sibling["id"] != card_id and related and card_state(sibling) in {"new", "review"}:
                if not sibling.get("suspended") and sibling.get("buriedUntil", 0) <= now:
                    until = next_day(now, payload["options"]["dayStartsAt"])
                    event["siblings"].append({"id": sibling["id"], "before": sibling.get("buriedUntil", 0), "after": until})
                    sibling["buriedUntil"] = until
    deck.setdefault("reviewLog", []).append(event)
    deck["schemaVersion"] = 2
    _save_deck_file(pdir, deck)
    return study_payload(deck, now)


@synchronized
def undo_review(pdir, deck_id, event_id=None, now=None):
    deck = _load(pdir, deck_id)
    events = _active_events(deck)
    if not events:
        raise StudyConflict("Geri alınabilecek değerlendirme yok.")
    event = events[-1]
    if event_id and event_id != event["id"]:
        raise StudyConflict("Son değerlendirme değişti. Desteyi yenile.")
    card = next((c for c in deck["cards"] if c["id"] == event["cardId"]), None)
    if card is None or card.get("reviewVersion", 0) != event["afterVersion"]:
        raise StudyConflict("Kart sonradan değiştirildi; eski değerlendirme geri alınamaz.")
    version = card.get("reviewVersion", 0) + 1
    for key in SCHEDULE_FIELDS:
        card.pop(key, None)
    card.update(event["before"])
    card["reviewVersion"] = version
    # Earlier events on the same card remain undoable, without reusing stale versions.
    prior = next((e for e in reversed(events[:-1]) if e["cardId"] == card["id"]), None)
    if prior:
        prior["afterVersion"] = version
    for item in event.get("siblings", []):
        sibling = next((c for c in deck["cards"] if c["id"] == item["id"]), None)
        if sibling and sibling.get("buriedUntil") == item["after"]:
            sibling["buriedUntil"] = item["before"]
    event["undone"] = True
    _save_deck_file(pdir, deck)
    return study_payload(deck, now)


@synchronized
def bulk_action(pdir, deck_id, ids, action, tags=None, now=None):
    now = time.time() if now is None else now
    if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(not isinstance(v, str) for v in ids):
        raise ValueError("İşlem için kart seç.")
    actions = {"bury", "unbury", "suspend", "resume", "flag", "unflag", "tag", "untag"}
    if action not in actions:
        raise ValueError("Geçersiz toplu işlem.")
    deck = _load(pdir, deck_id)
    wanted = set(ids)
    cards = [c for c in deck["cards"] if c["id"] in wanted]
    if len(cards) != len(wanted):
        raise KeyError("Kart bulunamadı.")
    tags = normalize_tags(tags or []) if action in {"tag", "untag"} else []
    for card in cards:
        if action in {"bury", "unbury"}:
            card["buriedUntil"] = next_day(now, validate_options(deck.get("options"))["dayStartsAt"]) if action == "bury" else 0
        elif action in {"suspend", "resume"}:
            card["suspended"] = action == "suspend"
        elif action in {"flag", "unflag"}:
            card["flagged"] = action == "flag"
        elif action == "tag":
            card["tags"] = normalize_tags(list(dict.fromkeys(card.get("tags", []) + tags)))
        elif action == "untag":
            card["tags"] = [t for t in card.get("tags", []) if t not in tags]
    _save_deck_file(pdir, deck)
    return study_payload(deck, now)


@synchronized
def import_text(pdir, deck_id, text):
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise ValueError("İçe aktarma en fazla 2 MB metin içerebilir.")
    deck = _load(pdir, deck_id)
    existing = {(c["front"], c["back"]) for c in deck["cards"]}
    added, skipped = 0, 0
    lines = text.lstrip("\ufeff").splitlines(keepends=True)
    reader = csv.reader(lines, delimiter="\t", strict=True)
    rows = []
    previous_line = 0
    try:
        for row in reader:
            # Inspect the raw record start: a quoted "#define" is a real field.
            comment = lines[previous_line].startswith("#")
            previous_line = reader.line_num
            if not comment:
                rows.append(row)
    except csv.Error as exc:
        raise ValueError("Geçersiz TSV: tırnakları ve satırları kontrol et.") from exc
    if len(rows) > 10001:
        raise ValueError("Tek seferde en fazla 10.000 satır aktarılabilir.")
    for index, row in enumerate(rows):
        if not row or not any(row):
            continue
        if len(row) < 2:
            raise ValueError(f"Satır {index + 1}: soru ve cevap sekmeyle ayrılmalı.")
        front, back = row[0].strip(), row[1].strip()
        if front.casefold() in {"front", "ön yüz"} and back.casefold() in {"back", "arka yüz"}:
            continue
        if not front or not back:
            raise ValueError(f"Satır {index + 1}: soru ve cevap boş olamaz.")
        if (front, back) in existing:
            skipped += 1
            continue
        tags = normalize_tags(row[2].split() if len(row) > 2 else [])
        deck["cards"].append({"id": uuid.uuid4().hex[:16], "kind": "basic",
                              "sourceSlideIndex": None, "front": front, "back": back,
                              "manual": True, "tags": tags, "suspended": False, **new_card_state()})
        existing.add((front, back))
        added += 1
    if not added and not skipped:
        raise ValueError("Aktarılacak kart bulunamadı.")
    _save_deck_file(pdir, deck)
    return {**study_payload(deck), "importResult": {"added": added, "skipped": skipped}}


@synchronized
def add_note(pdir, deck_id, front, back, card_type="basic", tags=None):
    deck = _load(pdir, deck_id)
    if not isinstance(front, str) or not isinstance(back, str) or not front.strip():
        raise ValueError("Kart metni boş olamaz.")
    if len(front) > 25000 or len(back) > 25000:
        raise ValueError("Kart yüzü en fazla 25.000 karakter olabilir.")
    tags = normalize_tags(tags or [])
    front, back = front.strip(), back.strip()
    candidates = []
    if card_type == "cloze":
        pattern = re.compile(r"\{\{c([1-9]\d*)::(.*?)(?:::(.*?))?\}\}", re.DOTALL)
        matches = list(pattern.finditer(front))
        if not matches or any(not m[2].strip() for m in matches):
            raise ValueError("En az bir {{c1::cevap}} boşluğu ekle.")
        groups = sorted({m[1] for m in matches}, key=int)
        if len(groups) > 50:
            raise ValueError("Bir not en fazla 50 farklı boşluk içerebilir.")
        answer = pattern.sub(lambda m: m[2], front)
        for group in groups:
            question = pattern.sub(lambda m: ("[" + (m[3] or "…") + "]") if m[1] == group else m[2], front)
            candidates.append((question, answer + ("\n\n" + back if back else ""), "cloze"))
    elif card_type in {"basic", "reverse"}:
        if not back:
            raise ValueError("Cevap boş olamaz.")
        candidates.append((front, back, "basic"))
        if card_type == "reverse":
            candidates.append((back, front, "basic"))
    else:
        raise ValueError("Geçersiz kart türü.")
    note_id = uuid.uuid4().hex
    for question, answer, kind in candidates:
        deck["cards"].append({"id": uuid.uuid4().hex[:16], "noteId": note_id,
                              "kind": kind, "sourceSlideIndex": None, "manual": True,
                              "front": question, "back": answer, "tags": tags,
                              "suspended": False, **new_card_state()})
    _save_deck_file(pdir, deck)
    return study_payload(deck)
