"""
Türkçe TTS motorları İngilizce/kod terimlerini harf harf Türkçe okuma kurallarıyla
okuyunca çok kötü çıkıyor (örn. Türkçe'de 'c' /dʒ/ okunur, bu yüzden 'case' ya da
'const' yanlış seslendirilir). Bu modül, SADECE seslendirme öncesi anlatım metnine
uygulanan, kulağa daha yakın gelen fonetik Türkçe yazım karşılıkları sağlar.

Ekrandaki slayt/kod/başlık metni ASLA değişmez — sadece TTS motoruna giden ses
metni değişir; altyazılar da orijinal (doğru yazımlı) kelimeyi gösterir
(bkz. app/pipeline.py'deki hizalama mantığı).

Bu liste kesin/bilimsel değil, en iyi tahminimdir — sesi gerçekten dinleyip
"şu kelime hâlâ kötü çıkıyor" dersen buraya ekleyip düzeltebiliriz.
"""
import json
import re
from pathlib import Path

from app.config import ROOT

OVERRIDES_PATH = ROOT / "pronunciation_overrides.json"

# Kod içinde sabit kodlanmış, gemiyle gelen varsayılan sözlük. Kullanıcı arayüzden
# (Ayarlar > Telaffuz Sözlüğü) yeni terim ekleyebilir veya bu varsayılanlardan
# birini geçersiz kılabilir — o değişiklikler OVERRIDES_PATH'e yazılır, bu sabit
# sözlük hiç değişmez (güncellemelerde kaybolmaması için).
PRONUNCIATION_MAP = {
    "else": "els",
    "switch": "sviç",
    "case": "keys",
    "default": "difolt",
    "break": "breyk",
    "continue": "kontinyu",
    "return": "riturn",
    "void": "voyd",
    "struct": "strakt",
    "union": "yunyın",
    "enum": "inum",
    "typedef": "taypdef",
    "const": "konst",
    "volatile": "volatıl",
    "extern": "ekstörn",
    "register": "recistır",
    "sizeof": "sayzof",
    "define": "difayn",
    "include": "inklud",
    "ifdef": "ifdef",
    "ifndef": "ifendef",
    "endif": "endif",
    "pragma": "pragma",
    "malloc": "meelok",
    "calloc": "kelok",
    "realloc": "riyelok",
    "free": "fri",
    "null": "nal",
    "true": "tru",
    "false": "fols",
    "printf": "printef",
    "scanf": "skenef",
    "pointer": "pointır",
    "array": "arey",
    "buffer": "bafır",
    "callback": "kolbek",
    "header": "hedır",
    "compiler": "kımpaylır",
    "linker": "linkır",
    "debugger": "dibagır",
    "interrupt": "intırapt",
    "stack": "stek",
    "heap": "hip",
    "thread": "tred",
    "mutex": "myutex",
    "semaphore": "semafor",
    "queue": "kyu",
    "timer": "taymır",
    "driver": "drayvır",
    "overflow": "overflov",
    "underflow": "andırflov",
    "deadlock": "dedlok",
    "watchdog": "vaçdog",
    "sleep": "slip",
    "boot": "but",
    "vector": "vektör",
    "cast": "kest",
    "byte": "bayt",
    "flash": "fleş",
    "clock": "klok",
    "reset": "riset",
    "toggle": "togıl",
    "polling": "polling",
    "fetch": "feç",
    "stream": "istrim",
    "cache": "keş",
    "class": "kles",
    "template": "templeyt",
    "exception": "exepşın",
    "override": "overrayd",
    "virtual": "vörtüıl",
    "inline": "inlayn",
    "namespace": "neymspeys",
}

_WORD_RE = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşü]+")


def load_overrides() -> dict[str, str]:
    """User-added/edited entries, layered on top of the built-in map."""
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k).strip().lower(): str(v) for k, v in data.items() if str(k).strip()}
    except (OSError, ValueError, TypeError):
        return {}


def save_overrides(overrides: dict[str, str]) -> None:
    cleaned = {
        str(term).strip().lower(): str(phonetic).strip()
        for term, phonetic in overrides.items()
        if str(term).strip() and str(phonetic).strip()
    }
    temp = OVERRIDES_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(OVERRIDES_PATH)


def effective_map() -> dict[str, str]:
    merged = dict(PRONUNCIATION_MAP)
    merged.update(load_overrides())
    return merged


def normalize_pronunciation(text: str, mapping: dict[str, str] | None = None) -> str:
    active_map = mapping if mapping is not None else effective_map()

    def repl(m: re.Match) -> str:
        word = m.group(0)
        key = word.lower()
        target = active_map.get(key)
        if target is None:
            return word
        if word.isupper() and len(word) > 1:
            return target.upper()
        if word[0].isupper():
            return target[:1].upper() + target[1:]
        return target

    return _WORD_RE.sub(repl, text)
