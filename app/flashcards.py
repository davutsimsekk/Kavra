"""Kaynak slaytlardan flashcard DESTELERİ üretimi ve kalıcı depolama.

Gerçek Anki'deki gibi bir projede BİRDEN FAZLA deste olabilir — her destenin
kendi adı, "kind"i ("static" ya da "llm") ve kendi kart/ilerleme listesi
vardır.

İki üretim yolu vardır:
- "static": LLM çağrısı yok — deterministik, anında, ücretsiz. İki kart türü:
  "basic" (ön yüz=başlık, arka yüz=anlatım+maddeler — app/export.py'nin
  build_anki_tsv'siyle aynı ruhta) ve "cloze" (bir maddeden en "belirleyici"
  kelime boşluk bırakılarak üretilen boşluk-doldurma kartı, RemNote tarzı).
- "llm": kullanıcının seçtiği bir sağlayıcıya (Agent CLI / Gemini / OpenAI-
  uyumlu) ders içeriği gönderilip serbest biçimli, isteğe göre odaklanmış
  kartlar ürettirilir (bkz. generate_llm_deck).

Statik kartların kimliği (id) içeriğe göre SABİT (hash tabanlı) — bu yüzden
bir deste yeniden üretildiğinde (ör. slaytlar düzenlendikten sonra) DEĞİŞMEYEN
kartlar aynı id'yi koruyup aralıklı tekrar ilerlemesini kaybetmez; değişen/
silinen slaytların kartları doğal olarak listeden düşer (yeni bir id ile
yeniden üretilirler, taze bir kart olarak). LLM desteleri kaynağa 1:1 bağlı
olmadığından (model her seferinde farklı kartlar üretebilir) yeniden üretme
şu an yalnızca "static" desteler için desteklenir.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from app.llm.base import extract_json_array
from app.models import Slide
from app.spaced_repetition import new_card_state

FLASHCARDS_DIR_NAME = "flashcards"
DECK_KINDS = {"static", "llm"}
CLOZE_BLANK = "_____"
_LLM_CARD_KINDS = {"basic", "cloze"}
# Tek bir isteğin ürettiği kart sayısına bir tavan — kullanıcı sayı girmese
# bile (ya da model talimatı görmezden gelse bile) deste kontrolsüzce şişip
# aşırı maliyetli/kullanışsız hale gelmesin diye.
_MAX_LLM_CARDS = 60

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


def _slides_source_text(slides: list[Slide]) -> str:
    """LLM'e gönderilecek, ders anlatısının kompakt bir metin özeti."""
    blocks: list[str] = []
    for slide in slides:
        if slide.level == "chapter":
            continue
        title = _clean(slide.title)
        narration = _clean(slide.narration)
        bullets = [_clean(b) for b in (slide.bullets or []) if _clean(b)]
        if not title and not narration and not bullets:
            continue
        block = [f"### {title}"] if title else []
        if bullets:
            block.append("\n".join(f"- {b}" for b in bullets))
        if narration:
            block.append(narration)
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def _build_llm_deck_prompt(source_text: str, count: int | None, focus_prompt: str) -> str:
    if count:
        count_note = (
            f"TAM OLARAK yaklaşık {count} kart üret (±2 tolerans kabul edilebilir); "
            "içerik bu sayı için yetersizse elindeki en önemli noktalardan mümkün "
            "olduğunca yaklaş, uydurma bilgiyle doldurma."
        )
    else:
        count_note = (
            "İçeriğin kapsamına uygun, makul sayıda kart üret (genelde konu başına "
            "1-3 kart yeterli; önemsiz/tekrarlayan ayrıntılar için kart üretme)."
        )
    focus_note = ""
    if focus_prompt.strip():
        focus_note = (
            "\n\nKullanıcının özel isteği (buna öncelik ver, kartların çoğunu bu "
            f"odağa göre şekillendir): {focus_prompt.strip()}"
        )
    return (
        "Aşağıdaki ders anlatımından, Anki tarzı aralıklı-tekrar flashcard'ları üret. "
        "SADECE geçerli bir JSON dizisi döndür, başka açıklama/markdown yazma. "
        "Dizideki her öğe şu alanlara sahip olmalı: "
        '{"kind": "basic" veya "cloze", "front": "...", "back": "..."}. '
        '"basic" kartlarda front kısa bir soru/terim, back net ve öz bir cevap/açıklama '
        'olmalı. "cloze" kartlarda front içinde tam olarak bir "_____" boşluğu olmalı '
        "(cümlenin geri kalanı olduğu gibi kalmalı), back sadece o boşluğa gelen "
        "kelime/kısa ifade olmalı. Kartlar Türkçe olmalı ve SADECE aşağıdaki kaynağa "
        f"dayanmalı, kaynağın dışına çıkıp bilgi uydurma. {count_note}{focus_note}\n\n"
        "KAYNAK DERS İÇERİĞİ:\n" + source_text
    )


def _coerce_llm_cards(raw_items: list) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        front = _clean(str(item.get("front", "")))
        back = _clean(str(item.get("back", "")))
        if not front or not back:
            continue
        kind = item.get("kind") if item.get("kind") in _LLM_CARD_KINDS else "basic"
        if kind == "cloze" and CLOZE_BLANK not in front:
            kind = "basic"
        cards.append({"kind": kind, "front": front, "back": back})
        if len(cards) >= _MAX_LLM_CARDS:
            break
    return cards


def generate_llm_deck(slides: list[Slide], generator: Any, count: int | None = None,
                       focus_prompt: str = "") -> list[dict[str, Any]]:
    """Bir LLM sağlayıcısı (Agent CLI / Gemini / OpenAI-uyumlu — hepsi
    ``_call(prompt) -> str`` sağlar, bkz. app.llm.*) kullanarak serbest
    biçimli flashcard'lar ürettirir. Statik üretimden farklı olarak kartlar
    doğrudan bir slayda sabitlenmez (id içeriğe göre türetilir) ve deste
    yeniden üretilemez — model her seferinde farklı kartlar üretebileceği
    için "değişmeyen kart aynı id'yi korur" varsayımı burada geçerli değil.
    """
    source_text = _slides_source_text(slides)
    if not source_text.strip():
        raise ValueError("Kart üretmek için yeterli anlatı içeriği yok.")
    prompt = _build_llm_deck_prompt(source_text, count, focus_prompt)
    raw_text = generator._call(prompt)
    try:
        data = extract_json_array(raw_text)
    except (ValueError, TypeError):
        fix_prompt = (
            "Aşağıdaki metni SADECE geçerli bir JSON dizisine çevir, "
            "başka hiçbir şey yazma:\n\n" + raw_text
        )
        data = extract_json_array(generator._call(fix_prompt))
    raw_cards = _coerce_llm_cards(data)
    if not raw_cards:
        raise ValueError("Model geçerli hiçbir flashcard üretmedi.")
    deck: list[dict[str, Any]] = []
    for index, candidate in enumerate(raw_cards):
        card_id = _card_id(candidate["kind"], index, candidate["front"])
        deck.append({
            "id": card_id, "sourceSlideIndex": None,
            **candidate, **new_card_state(), "suspended": False,
        })
    return deck


def deck_summary(cards: list[dict[str, Any]], now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    active = [c for c in cards if not c.get("suspended")]
    due = [c for c in active if c.get("dueAt", 0) <= now]
    new = [c for c in active if c.get("repetitions", 0) == 0]
    return {
        "totalCards": len(cards),
        "activeCards": len(active),
        "suspendedCards": len(cards) - len(active),
        "dueCount": len(due),
        "newCount": len(new),
    }


def _decks_dir(pdir: Path) -> Path:
    return pdir / FLASHCARDS_DIR_NAME


def _deck_path(pdir: Path, deck_id: str) -> Path:
    return _decks_dir(pdir) / f"{deck_id}.json"


def _save_deck_file(pdir: Path, deck: dict[str, Any]) -> None:
    _decks_dir(pdir).mkdir(parents=True, exist_ok=True)
    path = _deck_path(pdir, deck["id"])
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def list_decks(pdir: Path) -> list[dict[str, Any]]:
    """Bir projenin tüm destelerini (kartlarıyla birlikte), oluşturulma sırasına göre döndürür."""
    decks_dir = _decks_dir(pdir)
    if not decks_dir.exists():
        return []
    decks: list[dict[str, Any]] = []
    for path in decks_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(data, dict) and "id" in data:
            decks.append(data)
    decks.sort(key=lambda d: d.get("createdAt", 0))
    return decks


def get_deck(pdir: Path, deck_id: str) -> dict[str, Any] | None:
    path = _deck_path(pdir, deck_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def create_deck(pdir: Path, name: str, kind: str, slides: list[Slide], *,
                 generator: Any = None, count: int | None = None,
                 focus_prompt: str = "") -> dict[str, Any]:
    if kind not in DECK_KINDS:
        raise ValueError(f"Bilinmeyen deste türü: {kind!r} (beklenen: {sorted(DECK_KINDS)})")
    if kind == "llm":
        if generator is None:
            raise ValueError("Yapay zeka ile üretim için bir LLM sağlayıcısı gerekli.")
        cards = generate_llm_deck(slides, generator, count=count, focus_prompt=focus_prompt)
    else:
        cards = generate_deck(slides)
    deck = {
        "id": uuid.uuid4().hex[:12],
        "name": name.strip() or "Adsız Deste",
        "kind": kind,
        "createdAt": time.time(),
        "cards": cards,
    }
    if kind == "llm" and focus_prompt.strip():
        deck["focusPrompt"] = focus_prompt.strip()
    _save_deck_file(pdir, deck)
    return deck


def regenerate_deck_cards(pdir: Path, deck_id: str, slides: list[Slide]) -> dict[str, Any]:
    """Bir destenin kartlarını güncel slaytlardan yeniden üretir; içeriği
    değişmemiş kartların aralıklı-tekrar ilerlemesi korunur (bkz. generate_deck).
    Yalnızca "static" desteler için desteklenir — bkz. modül dokümantasyonu.
    """
    deck = get_deck(pdir, deck_id)
    if deck is None:
        raise KeyError(deck_id)
    if deck.get("kind") != "static":
        raise ValueError("Yalnızca statik desteler kaynaktan yeniden oluşturulabilir.")
    deck["cards"] = generate_deck(slides, existing_cards=deck["cards"])
    _save_deck_file(pdir, deck)
    return deck


def update_card(pdir: Path, deck_id: str, card_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Bir destedeki tek bir kartı günceller (inceleme sonrası zamanlama ya da
    askıya alma) ve tüm desteyi kaydeder. Kart bulunamazsa KeyError fırlatır."""
    deck = get_deck(pdir, deck_id)
    if deck is None:
        raise KeyError(deck_id)
    card = next((c for c in deck["cards"] if c["id"] == card_id), None)
    if card is None:
        raise KeyError(card_id)
    card.update(changes)
    _save_deck_file(pdir, deck)
    return card


def delete_deck(pdir: Path, deck_id: str) -> None:
    path = _deck_path(pdir, deck_id)
    if not path.exists():
        raise KeyError(deck_id)
    path.unlink()
