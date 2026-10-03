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
# Render'ın kendi format-ayrıştırma mantığını AYNEN kullanıyoruz (kopyalamıyoruz) —
# böylece bu kontrol, render'ın gerçekte ne yapacağından ASLA sapmaz. Bu isimler
# app.video.slide_renderer içinde modül-özel (_ önekli) ama kasıtlı olarak burada da
# tek gerçek kaynak (single source of truth) olarak yeniden kullanılıyor.
from app.video.slide_renderer import (
    MAX_VISIBLE_BULLET_CARDS,
    _CONTENT_LAYOUTS,
    _parse_comparison_columns,
    _parse_definition_pairs,
)

QUALITY_FILE = "quality_report.json"
# Tespit mantığı değiştiğinde (ör. yeni bir kontrol eklendiğinde) bunu artır —
# aksi halde diskteki eski quality_report.json, slaytlar değişmediği için
# hâlâ "geçerli" sayılıp yeni kontrol hiç çalışmadan önbellekten döner.
QUALITY_VERSION = 6

_LAYOUT_LABELS = {
    "emphasis": "Vurgu cümlesi",
    "definition": "Tanım kartı",
    "comparison": "Karşılaştırma",
    "process": "Adım akışı",
    "formula": "Formül",
    "callout": "Uyarı/İpucu kutusu",
}
_WORD_RE = re.compile(r"\b[\wığüşöçİĞÜŞÖÇ]+\b", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_MOJIBAKE_MARKERS = ("�", "Ã", "Â", "â€", "ðŸ", "ï¿½")
# T\u00fcrk\u00e7e anlat\u0131mda ME\u015eRU olarak asla g\u00f6r\u00fcnmeyecek yaz\u0131 sistemleri \u2014 Yunan alfabesi
# (form\u00fcllerde \u03b1/\u03b2 gibi) kas\u0131tl\u0131 olarak DI\u015eARIDA b\u0131rak\u0131ld\u0131, gerisi (Kiril, \u0130brani,
# Arap\u00e7a, Hint alt k\u0131tas\u0131 yaz\u0131 sistemleri \u2014 Devanagari'den Sinhala'ya tek blo\u011fa
# s\u0131\u011f\u0131yor, Tay/Lao, Tibet, Myanmar, G\u00fcrc\u00fc, Hiragana/Katakana, CJK, Hangul) ger\u00e7ek
# \u00fcretimde g\u00f6r\u00fclm\u00fc\u015f bir hal\u00fcsinasyon t\u00fcr\u00fc sonras\u0131 geni\u015fletildi (bkz. KAVRA_PROJECT_HANDOFF.md).
_UNEXPECTED_SCRIPT_RE = re.compile(
    r"[\u0400-\u052f\u0590-\u05ff\u0600-\u074f\u0900-\u0dff\u0e00-\u0fff"
    r"\u10a0-\u10ff\u1000-\u109f\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7a3]"
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


def _layout_format_mismatch(slide: Slide) -> str | None:
    """Slaydın seçtiği "layout" (bkz. app/models.py Slide.layout, prompts/
    lecture_script_prompt.md) render sırasında gerçekten kullanılabilecek mi, yoksa
    içerik o formatın beklediği yapıya uymadığı için sessizce "bullets"a mı düşecek?
    Bu KENDİSİ bir render hatası değil (render asla çökmez, her zaman güvenli bir
    varsayılana düşer) — ama kullanıcı bunu ŞU AN SADECE render edilmiş videoyu
    izleyince fark edebiliyor. Bu kontrol aynı bilgiyi render'dan ÖNCE, editörde verir.

    Dönüş: uyumsuzluğu açıklayan bir mesaj, ya da her şey yolundaysa (formatın kendisi
    "bullets"/tanınmayan bir değer/chapter/kod/sayfa-arka-planlı bir slayt olması dahil)
    None. "layout" alanı zaten "bullets" olan ya da tanınmayan bir slayt hiçbir şeye
    "düşmüyor" (zaten bullets'ta), bu yüzden bildirilmez.
    """
    if slide.level == "chapter" or slide.code or slide.background_image:
        # Bu üç durumda "layout" render tarafından zaten hiç okunmuyor/farklı bir
        # yolla ele alınıyor (bkz. app/video/slide_renderer.py _draw_topic) — burada
        # bir uyumsuzluktan söz etmek yanıltıcı olur.
        return None
    layout = slide.layout
    if layout not in _CONTENT_LAYOUTS or layout == "bullets":
        return None
    bullets = [str(item).strip() for item in (slide.bullets or []) if str(item).strip()]
    label = _LAYOUT_LABELS.get(layout, layout)

    if layout == "process" and len(bullets) > MAX_VISIBLE_BULLET_CARDS:
        # "bullets"a düşmez (comparison+embedded_image'daki gibi) — akış görünümünde
        # KALIR ama app/video/slide_renderer.py'nin çağıran tarafı listeyi render'a
        # vermeden önce [:MAX_VISIBLE_BULLET_CARDS] ile kırpıyor; bu yüzden generic
        # "madde listesine düşecek" mesajı burada YANLIŞ olurdu, kendi mesajını üretir.
        dropped = len(bullets) - MAX_VISIBLE_BULLET_CARDS
        return (
            f'"{label}" formatında {len(bullets)} adım var, akış görünümünde en fazla '
            f"{MAX_VISIBLE_BULLET_CARDS} adım gösterilir — son {dropped} adım ekranda "
            "hiç görünmeyecek (madde listesine düşme değil, sessiz kırpma)."
        )

    if layout == "emphasis":
        ok = len(bullets) == 1
    elif layout == "definition":
        ok = _parse_definition_pairs(bullets) is not None
    elif layout == "comparison":
        ok = _parse_comparison_columns(bullets) is not None
    elif layout in ("formula", "callout"):
        pairs = _parse_definition_pairs(bullets)
        ok = pairs is not None and len(pairs) == 1
    elif layout == "process":
        ok = bool(bullets)
    else:
        return None
    if ok:
        return None
    return f'"{label}" formatı seçilmiş ama madde içeriği bu formatın beklediği yapıya uymuyor; render sırasında sessizce madde listesine düşecek.'


# Sadece "callout" formatı için tanımlı etiketler (bkz. prompts/lecture_script_prompt.md).
# Model bazen bu tarz bir cümleyi yazıp "callout" DEĞİL başka bir format (en sık "emphasis")
# seçiyor — gerçek bir üretimde görüldü: "DİKKAT: ..." metni "emphasis" olarak işaretlenmiş,
# bu da render'da özel renkli/dikkat çekici kutu yerine "DİKKAT:" öneki dahil düz, dev bir
# alıntı cümlesi olarak görünüyor (bkz. app/video/slide_renderer.py _draw_emphasis_text).
_CALLOUT_LABEL_RE = re.compile(r"^(UYARI|DİKKAT|İPUCU|TAVSİYE|NOT)\s*:", re.IGNORECASE)


def _callout_label_used_outside_callout(slide: Slide) -> str | None:
    if slide.level == "chapter" or slide.code or slide.background_image:
        return None
    if slide.layout == "callout":
        return None
    for bullet in (slide.bullets or []):
        match = _CALLOUT_LABEL_RE.match(str(bullet).strip())
        if match:
            label = match.group(1)
            layout_label = _LAYOUT_LABELS.get(slide.layout, slide.layout)
            return (
                f'Bir madde "{label}:" etiketiyle başlıyor ama seçilen format "{layout_label}" — '
                f'bu etiket sadece "callout" formatında özel (renkli/dikkat çekici) bir kutu olarak '
                f'gösteriliyor, burada düz metnin bir parçası olarak görünecek. Format "callout" '
                "olarak değiştirilmeli mi?"
            )
    return None


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

        layout_issue = _layout_format_mismatch(slide)
        if layout_issue:
            add(index, "warning", "layout_mismatch", layout_issue, "layout")

        callout_issue = _callout_label_used_outside_callout(slide)
        if callout_issue:
            add(index, "warning", "mislabeled_callout", callout_issue, "layout")

        combined = " ".join([title, narration, *bullets])
        if any(marker in combined for marker in _MOJIBAKE_MARKERS):
            add(index, "error", "encoding_damage", "Bozuk karakter kodlaması algılandı.")
        # Eşiksiz: bu yazı sistemleri Türkçe içerikte MEŞRU olarak asla görünmez, tek bir
        # karakter bile neredeyse kesin bir üretim halüsinasyonudur (bkz. yorum yukarıda) —
        # gerçek bir örnekte tek bir kelimelik sızıntı, eski "yoğunluk" eşiğini hiç geçmeyip
        # fark edilmeden render'a gidiyordu.
        if _UNEXPECTED_SCRIPT_RE.search(combined):
            add(index, "error", "unexpected_language", "Anlatım/madde içinde beklenmeyen bir alfabe (Türkçe olmayan yazı sistemi) algılandı.")

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
