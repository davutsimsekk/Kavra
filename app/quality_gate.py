"""Fast, deterministic quality checks for generated lecture slides.

The quality gate intentionally avoids another LLM call. It catches the most
expensive failure modes before TTS/rendering starts: empty narration, encoding
damage, accidental duplicates and slides that are too dense to read.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import Slide

QUALITY_FILE = "quality_report.json"
# Tespit mantığı değiştiğinde (ör. yeni bir kontrol eklendiğinde) bunu artır —
# aksi halde diskteki eski quality_report.json, slaytlar değişmediği için
# hâlâ "geçerli" sayılıp yeni kontrol hiç çalışmadan önbellekten döner.
QUALITY_VERSION = 3
_WORD_RE = re.compile(r"\b[\wığüşöçİĞÜŞÖÇ]+\b", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_MOJIBAKE_MARKERS = ("�", "Ã", "Â", "â€", "ðŸ", "ï¿½")
_UNEXPECTED_SCRIPT_RE = re.compile(
    r"[\u0400-\u052f\u0600-\u06ff\u0900-\u097f\u3040-\u30ff\u4e00-\u9fff]"
)


def _normalized(text: str) -> str:
    text = text.casefold().strip()
    text = re.sub(r"[^\wığüşöçİĞÜŞÖÇ]+", " ", text, flags=re.UNICODE)
    return _SPACE_RE.sub(" ", text).strip()


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# "Şimdi buna bakalım." gibi kısa geçiş cümleleri doğal olarak tekrar eder;
# bunları gürültü olarak saymamak için sadece bu uzunluğu aşan cümleler
# tam-eşleşme (birebir aynı cümle) kontrolüne dahil edilir.
_MIN_DUPLICATE_SENTENCE_CHARS = 25


def _sentences(narration: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(narration) if s.strip()]


# Yakın-anlam (paraphrase) tekrar tespiti: birebir aynı olmayan ama modelin
# aynı içeriği farklı kelimelerle tekrar ettiği cümleleri yakalar — özellikle
# stateless sağlayıcılarda (OpenAI/Gemini), hafıza notu sadece son birkaç
# slaydı kapsadığı için uzak slaytlar arasındaki örtüşmeler duplicate_sentence
# kontrolünden kaçar.
_MIN_NEAR_DUP_SENTENCE_CHARS = 40
_NEAR_DUP_MIN_WORD_LEN = 4  # "bir", "ve" gibi ayırt edici olmayan kısa kelimeleri ele
_NEAR_DUP_MIN_SHARED_WORDS = 3  # aday filtresi: en az bu kadar ortak kelime paylaşmalı
_NEAR_DUP_JACCARD_THRESHOLD = 0.6
_MAX_NEAR_DUP_ISSUES = 40  # patolojik durumlarda (çok tekrarlı kaynak) uyarı listesini şişirmemek için


def _significant_words(normalized_sentence: str) -> frozenset[str]:
    return frozenset(w for w in normalized_sentence.split() if len(w) >= _NEAR_DUP_MIN_WORD_LEN)


def _find_near_duplicate_sentences(
    records: list[tuple[int, str, frozenset[str]]],
) -> list[tuple[int, int, float]]:
    """records: (slide_index, normalized_sentence, önemli_kelime_seti) — uzunluk
    eşiğini geçmiş her cümle için, slayt sırasına göre. Dönüş: (sonraki_slayt,
    önceki_slayt, jaccard_oranı) — sadece eşiği aşan, birebir AYNI olmayan ve
    farklı slaytlara ait çiftler için.

    Tüm çiftleri O(n^2) karşılaştırmak büyük (300+ slaytlık) projelerde çok
    yavaş olurdu; bunun yerine ortak "önemli kelime" paylaşan adayları bulmak
    için bir ters indeks kullanılır — gerçekten alakasız cümle çiftleri hiç
    karşılaştırılmaz.
    """
    inverted: dict[str, list[int]] = defaultdict(list)
    for i, (_slide_index, _sentence, words) in enumerate(records):
        for word in words:
            inverted[word].append(i)

    found: list[tuple[int, int, float]] = []
    for i, (slide_i, sentence_i, words_i) in enumerate(records):
        if not words_i:
            continue
        candidate_counts: dict[int, int] = defaultdict(int)
        for word in words_i:
            for j in inverted[word]:
                if j > i:
                    candidate_counts[j] += 1
        for j, shared in candidate_counts.items():
            if shared < _NEAR_DUP_MIN_SHARED_WORDS:
                continue
            slide_j, sentence_j, words_j = records[j]
            if slide_i == slide_j or sentence_i == sentence_j:
                continue
            union = words_i | words_j
            ratio = len(words_i & words_j) / len(union) if union else 0.0
            if ratio >= _NEAR_DUP_JACCARD_THRESHOLD:
                found.append((slide_j, slide_i, ratio))
                if len(found) >= _MAX_NEAR_DUP_ISSUES:
                    return found
    return found


def _fingerprint(slides: list[Slide]) -> str:
    payload = json.dumps(
        [slide.to_dict() for slide in slides], ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def analyze_slides(slides: list[Slide]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    def add(index: int | None, severity: str, code: str, message: str, field: str = ""):
        issues.append({
            "slideIndex": index,
            "severity": severity,
            "code": code,
            "field": field,
            "message": message,
        })

    if not slides:
        add(None, "error", "no_slides", "Henüz denetlenecek bir slayt yok.")

    title_owners: dict[str, list[int]] = defaultdict(list)
    narration_owners: dict[str, list[int]] = defaultdict(list)
    sentence_owners: dict[str, list[int]] = defaultdict(list)
    near_dup_records: list[tuple[int, str, frozenset[str]]] = []
    total_words = 0

    for index, slide in enumerate(slides):
        title = (slide.title or "").strip()
        narration = (slide.narration or "").strip()
        bullets = [str(item).strip() for item in (slide.bullets or []) if str(item).strip()]
        word_count = len(_WORD_RE.findall(narration))
        total_words += word_count

        if not title:
            add(index, "error", "missing_title", "Slayt başlığı boş.", "title")
        elif len(title) > 100:
            add(index, "warning", "long_title", "Başlık ekranda taşabilecek kadar uzun.", "title")

        if not narration:
            add(index, "error", "missing_narration", "Anlatım metni boş; ses üretilemez.", "narration")
        elif slide.level == "chapter" and word_count < 4:
            add(index, "warning", "short_narration", "Bölüm açılışının anlatımı çok kısa.", "narration")
        elif slide.level != "chapter" and word_count < 18:
            add(index, "warning", "short_narration", "Konu anlatımı çok kısa; açıklama eksik olabilir.", "narration")
        elif word_count > 260:
            add(index, "warning", "long_narration", "Bu slaytın anlatımı çok uzun; iki slayda bölünebilir.", "narration")

        if len(bullets) > 7:
            add(index, "warning", "too_many_bullets", "Yedi maddeden fazla içerik bilişsel ve görsel yük oluşturabilir.", "bullets")
        if any(len(bullet) > 180 for bullet in bullets):
            add(index, "warning", "long_bullet", "En az bir madde ekranda taşabilecek kadar uzun.", "bullets")

        combined = " ".join([title, narration, *bullets])
        if any(marker in combined for marker in _MOJIBAKE_MARKERS):
            add(index, "error", "encoding_damage", "Bozuk karakter kodlaması algılandı.")
        unexpected_count = len(_UNEXPECTED_SCRIPT_RE.findall(combined))
        letter_count = sum(char.isalpha() for char in combined)
        if unexpected_count >= 8 and unexpected_count / max(letter_count, 1) > 0.03:
            add(index, "error", "unexpected_language", "Türkçe anlatım içinde yoğun bir farklı alfabe algılandı.")

        normalized_title = _normalized(title)
        normalized_narration = _normalized(narration)
        if normalized_title:
            title_owners[normalized_title].append(index)
        if len(normalized_narration) >= 80:
            narration_owners[normalized_narration].append(index)

        # Tam slayt bire bir eşleşmesi (yukarıdaki narration_owners) nadir bir
        # durum; asıl karşılaşılan sorun tek bir cümlenin başka bir slaytta
        # (ya da aynı slaytta ikinci kez) birebir tekrar edilmesi — özellikle
        # stateless sağlayıcılarda (OpenAI/Gemini) hafıza özet olduğu için.
        for sentence in _sentences(narration):
            normalized_sentence = _normalized(sentence)
            if len(normalized_sentence) >= _MIN_DUPLICATE_SENTENCE_CHARS:
                sentence_owners[normalized_sentence].append(index)
            if len(normalized_sentence) >= _MIN_NEAR_DUP_SENTENCE_CHARS:
                near_dup_records.append((index, normalized_sentence, _significant_words(normalized_sentence)))

    for owners in title_owners.values():
        if len(owners) > 1:
            for index in owners[1:]:
                add(index, "warning", "duplicate_title", f"Başlık, slayt {owners[0] + 1} ile aynı.", "title")
    for owners in narration_owners.values():
        if len(owners) > 1:
            for index in owners[1:]:
                add(index, "warning", "duplicate_narration", f"Anlatım, slayt {owners[0] + 1} ile tamamen aynı.", "narration")
    for owners in sentence_owners.values():
        if len(owners) <= 1:
            continue
        first_index = owners[0]
        for index in owners[1:]:
            if index == first_index:
                add(
                    index, "warning", "duplicate_sentence",
                    "Bu cümle, aynı slaydın anlatımında birden fazla kez geçiyor.", "narration",
                )
            else:
                add(
                    index, "warning", "duplicate_sentence",
                    f"Bu cümle, slayt {first_index + 1} ile birebir aynı — LLM muhtemelen tekrar üretti.",
                    "narration",
                )
    for later_index, earlier_index, ratio in _find_near_duplicate_sentences(near_dup_records):
        add(
            later_index, "info", "near_duplicate_sentence",
            f"Bu cümle, slayt {earlier_index + 1} ile anlamca çok benziyor (~%{round(ratio * 100)} "
            "ortak kelime) — LLM muhtemelen aynı şeyi farklı kelimelerle tekrar etti.",
            "narration",
        )

    counts = {
        severity: sum(issue["severity"] == severity for issue in issues)
        for severity in ("error", "warning", "info")
    }
    slide_issue_counts: dict[str, dict[str, int]] = {}
    for issue in issues:
        if issue["slideIndex"] is None:
            continue
        key = str(issue["slideIndex"])
        bucket = slide_issue_counts.setdefault(key, {"error": 0, "warning": 0, "info": 0})
        bucket[issue["severity"]] += 1

    error_slides = {issue["slideIndex"] for issue in issues if issue["severity"] == "error" and issue["slideIndex"] is not None}
    warning_slides = {
        issue["slideIndex"] for issue in issues
        if issue["severity"] == "warning" and issue["slideIndex"] is not None
    } - error_slides
    affected_weight = len(error_slides) + len(warning_slides) * 0.25
    score = max(0, round(100 * (1 - affected_weight / len(slides)))) if slides else 0
    status = "blocked" if counts["error"] else "review" if counts["warning"] else "passed"
    return {
        "version": QUALITY_VERSION,
        "slideFingerprint": _fingerprint(slides),
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "score": score,
        "renderAllowed": counts["error"] == 0 and bool(slides),
        "errorCount": counts["error"],
        "warningCount": counts["warning"],
        "infoCount": counts["info"],
        "issues": issues,
        "slideIssueCounts": slide_issue_counts,
        "stats": {
            "slideCount": len(slides),
            "wordCount": total_words,
            "estimatedMinutes": round(total_words / 132, 1) if total_words else 0,
        },
    }


def save_quality_report(pdir: Path, slides: list[Slide]) -> dict[str, Any]:
    report = analyze_slides(slides)
    path = pdir / QUALITY_FILE
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return report


def load_or_analyze_quality(pdir: Path, slides: list[Slide]) -> dict[str, Any]:
    path = pdir / QUALITY_FILE
    fingerprint = _fingerprint(slides)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("version") == QUALITY_VERSION and report.get("slideFingerprint") == fingerprint:
            return report
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return save_quality_report(pdir, slides)
