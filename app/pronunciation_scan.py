"""Bir dersin TÜM anlatım metnini tek seferde tarayıp Türk TTS motorlarının yanlış
okuyabileceği İngilizce/teknik terimleri ve önerilen fonetik Türkçe yazımlarını
bulan, isteğe bağlı bir zenginleştirme adımı — app/tts/pronunciation.py'nin (statik
sözlük + normalize_pronunciation) TAMAMLAYICISI, onun yerine geçmez.

Bu modül HİÇBİR ŞEY KAYDETMEZ — sadece öneri üretir. Kaydetme kararı her zaman
kullanıcıya bırakılır (bkz. studio_web/api.py'deki suggest endpoint'i ve mevcut
PUT /api/pronunciation): AI'ın "İngilizce kelime" tahmini bazen yanlış olabilir
(bir özel isim, zaten Türkçeleşmiş bir kelime), bu yüzden körü körüne otomatik
kaydetmek yerine kullanıcı önerileri gözden geçirip onaylıyor.

Ham anlatım metninin TAMAMI yerine, ondan çıkarılan BENZERSİZ kelime listesi
gönderilir — hem çok daha ucuz hem de modelin dikkatini gerçek karar noktasına
(bu kelime İngilizce mi, Türkçe TTS'i yanıltır mı) toplar.
"""

from __future__ import annotations

import json
import re

from app.gemini_retry import call_with_retry
from app.tts.pronunciation import _WORD_RE, effective_map

DEFAULT_MODEL = "gemini-3.5-flash-lite"
CHUNK_SIZE = 150

_SUGGEST_PROMPT = (
    "Aşağıda bir Türkçe ders anlatımından çıkarılmış BENZERSİZ kelime listesi var. Bu "
    "liste hem normal Türkçe kelimeler hem de yanlış okunması muhtemel İngilizce/teknik "
    "terimler içeriyor. Türkçe TTS motorları İngilizce kökenli kelimeleri harf harf "
    "Türkçe okuma kurallarıyla okuyunca çok kötü çıkıyor (örn. Türkçe'de 'c' harfi /dʒ/ "
    "okunur, bu yüzden 'case' ya da 'const' yanlış seslendirilir).\n\n"
    "GÖREVİN: Bu listeden SADECE gerçekten İngilizce/yabancı kökenli olup bir Türkçe TTS "
    "motorunun yanlış okuyacağı kelimeleri seç. Yaygın, zaten Türkçeleşmiş ya da normal "
    "Türkçe kelimeleri (ör. 'kod', 'bilgisayar', 'program', 'sistem', 'değişken') SEÇME "
    "— bunlar zaten doğru okunur. Özel isimlerden (kişi/yer adı) da EMİN DEĞİLSEN ekleme.\n\n"
    "ÜÇ KRİTİK KURAL:\n"
    "1) Bir kelime Türkçe ek almış hâliyle geldiyse (ör. 'hiperkalsemiye' = 'hiperkalsemi' "
    "+ '-ye' hâl eki, 'insipidusa' = 'insipidus' + '-a' hâl eki) ve KÖK zaten normal Türkçe "
    "okuma kurallarıyla doğru çıkıyorsa, bu kelimeyi SEÇME — Latin kökenli tıbbi/bilimsel "
    "terimlerin çoğu (hiperkalsemi, polidipsi, bipolar, insipidus gibi) zaten Türkçe tıp "
    "dilinde yerleşiktir ve normal okunuşuyla doğru çıkar, düzeltme gerekmez.\n"
    "2) Önerdiğin fonetik yazım GERÇEK bir ses değişikliği içermeli — sadece kelimenin "
    "içine boşluk eklemek (ör. 'hiperkalsemi ye') bir düzeltme DEĞİLDİR, bunu asla yapma.\n"
    "3) Kısaltmalar için (ör. EKG, ADH, GFR) kısaltmanın TÜM harflerini Türkçe harf "
    "adlarıyla ayrı ayrı yaz, hiçbir harfi atlama (ör. 'EKG' -> 'e ka ge').\n\n"
    "Seçtiğin her kelime için, bir Türk TTS motorunun doğru telaffuza en yakın sesi "
    "çıkaracağı FONETİK TÜRKÇE YAZIMINI ver (Türkçe okuma kurallarına göre yazılmış hali "
    "— mevcut sözlükteki örneklerle AYNI tarzda):\n"
    "pointer -> pointır, printf -> printef, malloc -> meelok, cache -> keş, queue -> kyu, "
    "thread -> tred\n\n"
    "Çıktı SADECE geçerli bir JSON nesnesi olsun (başka hiçbir açıklama/markdown yazma): "
    '{{"kelime": "fonetik_yazim", ...}}. Listede uygun kelime yoksa boş bir nesne {{}} '
    "döndür.\n\n"
    "Kelime listesi:\n{words}"
)


def _candidate_words(narrations: list[str], known_map: dict[str, str]) -> list[str]:
    """Tüm anlatımlardan BENZERSİZ kelimeleri çıkarır, zaten sözlükte (yerleşik +
    kullanıcı override) olanları eler — LLM'e sadece gerçekten karar verilmesi gereken
    kelimeler gider. `_WORD_RE`, app/tts/pronunciation.py'nin normalize_pronunciation'da
    KULLANDIĞI regex'in AYNISI — hangi kelimenin "eşleşebilir" sayıldığı iki yerde de
    tutarlı olsun diye kasıtlı olarak buradan import edildi, kopyalanmadı."""
    seen: dict[str, str] = {}
    for text in narrations:
        for match in _WORD_RE.finditer(text or ""):
            word = match.group(0)
            key = word.lower()
            if len(key) > 2 and key not in known_map and key not in seen:
                seen[key] = word
    return list(seen.values())


def _extract_json_object(text: str) -> dict:
    """extract_json_array (app/llm/base.py) bir DİZİ bekler; burada modelden bir
    NESNE isteniyor, bu yüzden ayrı, küçük bir çıkarıcı. Model çıktısı bozuksa (nadiren)
    boş sözlük döner — bu chunk'ın önerileri sessizce atlanır, tüm tarama çökmez."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def suggest_pronunciations(
    narrations: list[str], api_key: str, model: str = DEFAULT_MODEL, progress_cb=None,
) -> dict[str, str]:
    """TÜM anlatımları tarar, sözlükte olmayan benzersiz kelimeleri gruplar hâlinde
    (CHUNK_SIZE'lık) modele gönderir. HİÇBİR ŞEY KAYDETMEZ — sadece {terim: fonetik}
    önerileri döndürür, kaydetme kararı çağıran tarafa (kullanıcının onayına) aittir."""
    if not api_key:
        raise ValueError("Telaffuz önerisi için bir Gemini API anahtarı gerekli.")

    candidates = _candidate_words(narrations, effective_map())
    if not candidates:
        return {}
    candidate_set = {w.lower() for w in candidates}

    from google import genai

    client = genai.Client(api_key=api_key)
    chunks = [candidates[i:i + CHUNK_SIZE] for i in range(0, len(candidates), CHUNK_SIZE)]
    suggestions: dict[str, str] = {}
    for done, chunk in enumerate(chunks, start=1):
        if progress_cb:
            progress_cb(done, len(chunks))
        prompt = _SUGGEST_PROMPT.format(words=", ".join(chunk))

        def _run():
            return client.models.generate_content(model=model, contents=prompt)

        resp = call_with_retry(_run, feature_name="telaffuz önerisi")
        data = _extract_json_object(resp.text)
        for term, phonetic in data.items():
            term_clean = str(term).strip().lower()
            phonetic_clean = str(phonetic).strip()
            # Savunma: model aday listesinin DIŞINDA bir terim uydurmuş ya da kelimeyi
            # olduğu gibi (fonetik dönüşüm yapmadan) geri döndürmüş olabilir — ikisi de
            # anlamsız bir öneri olur, sessizce atlanır.
            if not term_clean or not phonetic_clean:
                continue
            if term_clean not in candidate_set:
                continue
            phonetic_key = phonetic_clean.lower()
            if phonetic_key == term_clean:
                continue
            # Gerçek bir Gemini çağrısında görüldü: model bazen sadece kelimenin içine
            # boşluk sokup bunu "düzeltme" gibi sunuyor (ör. "hiperkalsemiye" ->
            # "hiperkalsemi ye") — Türkçe hâl eki almış, zaten doğru okunan bir kelimeyi
            # gerçek bir ses değişikliği OLMADAN "önermiş" oluyor. Prompt'ta bu ayrıca
            # yasaklandı ama modele güvenmek yetmez; boşluksuz hâli orijinalle aynıysa
            # bu öneri sessizce atlanır.
            if phonetic_key.replace(" ", "") == term_clean:
                continue
            suggestions[term_clean] = phonetic_clean
    return suggestions
