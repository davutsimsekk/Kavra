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
import re

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


def normalize_pronunciation(text: str) -> str:
    def repl(m: re.Match) -> str:
        word = m.group(0)
        key = word.lower()
        target = PRONUNCIATION_MAP.get(key)
        if target is None:
            return word
        if word.isupper() and len(word) > 1:
            return target.upper()
        if word[0].isupper():
            return target[:1].upper() + target[1:]
        return target

    return _WORD_RE.sub(repl, text)
