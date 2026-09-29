"""Slaytlara destekleyici bir görsel eklemek için — anlatım/layout üretiminden AYRI,
isteğe bağlı bir zenginleştirme adımı (app/vision_caption.py'nin simetriği: o bir
görseli METNE çeviriyor, bu METİNDEN bir görsel buluyor/üretiyor).

Sıra kasıtlı: önce GERÇEK bir görsel aranır (Gemini'nin Google Search grounding'i ile) —
bulunursa hem daha güvenilir hem daha "gerçek" (bir cihazın/yerin/kişinin fotoğrafı).
Bulunamazsa ya da içerik zaten kavramsal/süsleme amaçlıysa yapay zeka ile bir
illüstrasyon üretilir (gemini-3.1-flash-lite-image, en ucuz görsel modeli).

KRİTİK güvenlik notu — ampirik olarak doğrulandı (bkz. commit notu): grounding
AÇIKKEN bile model URL'i HALÜSİNE EDEBİLİR (gerçekmiş gibi görünen ama var olmayan bir
Wikimedia linki üretti, 404 döndü). Bu yüzden döndürülen HER URL gerçekten indirilip
geçerli bir raster görsel olduğu (PIL ile açılıp) doğrulanmadan ASLA kullanılmaz;
başarısız olursa sessizce yapay zeka üretimine düşülür — bu adım hiçbir zaman render'ı
durduran bir hataya yol açmamalı, en kötü ihtimalle slayt görselsiz kalır.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Callable

from app.gemini_retry import call_with_retry
from app.models import Slide

DEFAULT_SEARCH_MODEL = "gemini-3.5-flash-lite"
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-lite-image"
ENRICHMENT_MODES = {"support", "background"}

# enrich_slides_with_images'ta kaç slaytın görseli AYNI ANDA aranıp/üretilsin — bu tamamen
# ağ G/Ç'ye bağlı (CPU değil), bu yüzden pass3'ün render paralelliğinden (CPU çekirdek
# sayısına bağlı) farklı olarak sabit, mütevazı bir sayı: Gemini'nin ücretsiz/düşük
# kademe kotalarını aşırı zorlamamak için PASS3_MAX_WORKERS'tan (app/pipeline.py) daha
# düşük tutuldu.
IMAGE_ENRICH_MAX_WORKERS = 3

_DOWNLOAD_TIMEOUT = 15
_MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024

_DECIDE_PROMPT = (
    "Aşağıda bir ders slaydının başlığı ve anlatım metni var. Bu slayda EŞLİK EDECEK bir "
    "görsel gerekip gerekmediğine karar ver.\n\n"
    "Başlık: {title}\n"
    "Anlatım: {narration}\n\n"
    "Üç seçeneğin var, SADECE birini, TAM OLARAK aşağıdaki formatlardan biriyle cevapla, "
    "başka hiçbir açıklama/markdown yazma:\n\n"
    "1) İçerikte GERÇEK, somut bir şey varsa (belirli bir cihaz, kişi, yer, resmi/bilinen bir "
    "teknik diyagram vb.) ve Google Search ile buna ait gerçek, doğrudan bir görsel dosyası "
    "(jpg/png/webp) bulabiliyorsan:\n"
    "GERÇEK: <bulduğun görselin tam URL'i>\n\n"
    "2) Aranacak somut/gerçek bir şey yok ama içerik yine de kavramsal/süsleme amaçlı bir "
    "illüstrasyonla güçlenecekse:\n"
    "ÜRET: <İngilizce, kısa illüstrasyon tarifi>\n"
    "ÖNEMLİ: bu tarif SADECE soyut/dekoratif bir illüstrasyon istemeli (ör. bir kavramı "
    "simgeleyen ikon/şekil/renk kompozisyonu). ASLA teknik bir diyagram, şema, grafik, "
    "tablo, adres/sayı/kod/etiket İÇEREN bir görsel isteme — görsel üretim modelleri "
    "böyle detaylarda YANLIŞ/UYDURMA bilgi üretir (ör. geçersiz bir onaltılık değer, "
    "anlamsız bir etiket) ve bu bir öğrenciyi yanlış yönlendirir. Teknik doğruluk "
    "gerektiren bir görsel için bu seçeneği KULLANMA, bunun yerine YOK de.\n\n"
    "3) Görsel bu slayt için gerçekten gerekli değilse (soyut, madde listesi ağırlıklı, görsel "
    "eklemek zorlama olur — EMİN DEĞİLSEN bunu seç):\n"
    "YOK"
)

_URL_RE = re.compile(r"GER[ÇC]EK\s*:\s*(\S+)", re.IGNORECASE)
_PROMPT_RE = re.compile(r"[ÜU]RET\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)

# İndirilen dosyanın PIL ile açılabildiğini doğrulamak yeterli değil — SVG gibi vektör
# formatları PIL ile hiç açılamaz ve render_slide'ın _paste_fitted_image'i de raster
# bekler; bu yüzden içerik tipi baştan bu listeye uymuyorsa hiç indirmeye çalışılmaz.
_ACCEPTED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp")


def _client(api_key: str):
    if not api_key:
        raise ValueError(
            "Görsel zenginleştirme için bir Gemini API anahtarı gerekli "
            "(anlatım sağlayıcısından bağımsız, sadece bu adım için)."
        )
    from google import genai
    return genai.Client(api_key=api_key)


def _decide_and_search(slide: Slide, api_key: str, model: str) -> tuple[str, str] | None:
    """Dönüş: ("url", <link>) | ("prompt", <üretim tarifi>) | None (görsel gerekmiyor
    ya da model çıktısı beklenen üç formattan hiçbirine uymuyor — güvenli varsayılan
    None'dur, asla tahmin yürütülmez)."""
    from google.genai import types

    client = _client(api_key)
    prompt = _DECIDE_PROMPT.format(title=slide.title, narration=(slide.narration or slide.title))

    def _run():
        return client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )

    resp = call_with_retry(_run, feature_name="görsel zenginleştirme")
    text = (resp.text or "").strip()
    url_match = _URL_RE.search(text)
    if url_match:
        return "url", url_match.group(1).strip()
    prompt_match = _PROMPT_RE.search(text)
    if prompt_match:
        return "prompt", prompt_match.group(1).strip()
    return None


def _download_image(url: str) -> tuple[bytes, str] | None:
    """URL'i indirir ve GERÇEKTEN geçerli, desteklenen bir raster görsel olduğunu
    doğrular. Model URL'i halüsine etmiş olabilir (bkz. modül docstring'i) — bu yüzden
    her adımda (istek başarısız, içerik tipi uymuyor, PIL açamıyor) sessizce None
    döner, asla istisna fırlatmaz; çağıran taraf bunu "yapay zekaya düş" sinyali sayar."""
    import requests
    from PIL import Image, UnidentifiedImageError

    try:
        resp = requests.get(url, timeout=_DOWNLOAD_TIMEOUT, stream=True,
                            headers={"User-Agent": "Mozilla/5.0 (Kavra ders stüdyosu)"})
        if resp.status_code != 200:
            return None
        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type not in _ACCEPTED_CONTENT_TYPES:
            return None
        data = resp.content[:_MAX_DOWNLOAD_BYTES + 1]
        if len(data) > _MAX_DOWNLOAD_BYTES:
            return None
    except requests.RequestException:
        return None

    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError):
        return None
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
           "image/gif": "gif", "image/bmp": "bmp"}[content_type]
    return data, ext


def _generate_image(prompt: str, api_key: str, model: str) -> tuple[bytes, str] | None:
    client = _client(api_key)

    def _run():
        return client.models.generate_content(model=model, contents=prompt)

    resp = call_with_retry(_run, feature_name="görsel zenginleştirme")
    candidates = resp.candidates or []
    if not candidates:
        return None
    for part in candidates[0].content.parts or []:
        if part.inline_data and part.inline_data.data:
            mime = (part.inline_data.mime_type or "image/png").split(";")[0].strip().lower()
            ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime, "png")
            return part.inline_data.data, ext
    return None


def _fallback_generation_prompt(slide: Slide) -> str:
    """Aranan gerçek görsel indirilemediğinde (bozuk/halüsine URL) kullanılacak genel
    bir illüstrasyon tarifi — slaydın başlığından üretilir, İngilizce (görsel modelleri
    İngilizce tariflerde daha tutarlı)."""
    return (
        f"Minimalist, modern flat illustration representing the concept of "
        f"'{slide.title}', clean vector art style, soft color palette. "
        f"Purely decorative/abstract — NOT a technical diagram, chart, schematic or table. "
        f"No text, no labels, no numbers, no addresses, no code, no watermarks. "
        f"Suitable as an educational slide's supporting visual."
    )


def _background_generation_prompt(slide: Slide) -> str:
    """Metin bindirilecek 16:9 arka plan için güvenli, sakin bir üretim tarifi."""
    context = " ".join((slide.narration or "").split())[:420]
    return (
        "Create a cinematic 16:9 educational presentation background inspired by "
        f"the topic '{slide.title}'. Context: '{context}'. "
        "Use a polished editorial illustration or atmospheric photographic style, "
        "with restrained detail, soft depth, coherent lighting and generous negative "
        "space for overlaid lesson text. Keep the center and left-middle visually calm. "
        "No text, no letters, no labels, no numbers, no logos, no watermark, no UI, "
        "no technical diagram, no chart, no table, no code. The image must support the "
        "topic without inventing factual details. Full-bleed widescreen composition."
    )


def find_image_for_slide(slide: Slide, api_key: str, search_model: str = DEFAULT_SEARCH_MODEL,
                         image_model: str = DEFAULT_IMAGE_MODEL) -> tuple[bytes, str, str] | None:
    """Bir slayt için görsel bulur/üretir. Dönüş: (görsel_baytları, uzantı, kaynak) —
    kaynak "search" (internetten bulunan gerçek görsel) ya da "generated" (yapay zeka
    ile üretilen illüstrasyon). Görsel gerekmiyorsa ya da her iki yol da başarısız
    olursa None — bu ASLA bir hata değildir, slayt sadece görselsiz kalır."""
    decision = _decide_and_search(slide, api_key, search_model)
    if decision is None:
        return None
    kind, value = decision
    if kind == "url":
        downloaded = _download_image(value)
        if downloaded:
            return downloaded[0], downloaded[1], "search"
        generated = _generate_image(_fallback_generation_prompt(slide), api_key, image_model)
        return (generated[0], generated[1], "generated") if generated else None
    # kind == "prompt"
    generated = _generate_image(value, api_key, image_model)
    return (generated[0], generated[1], "generated") if generated else None


def find_background_for_slide(
    slide: Slide, api_key: str, image_model: str = DEFAULT_IMAGE_MODEL,
) -> tuple[bytes, str, str] | None:
    """Arama yapmadan, metin bindirmeye uygun dekoratif bir arka plan üretir."""
    generated = _generate_image(_background_generation_prompt(slide), api_key, image_model)
    return (generated[0], generated[1], "generated") if generated else None


def eligible_for_enrichment(slide: Slide, force: bool = False) -> bool:
    """Bir slaydın görsel zenginleştirmeye aday olup olmadığı — render'da gerçekten
    kullanılamayacak durumları baştan eler (boşa API çağrısı yapmamak için):
    "chapter" slaytları (_draw_chapter embedded_image'i hiç okumaz), kod örneği olan
    slaytlar (render kodu her zaman önceliklendirir, görsel hiç gösterilmez) ve sayfa
    modundaki slaytlar (background_image zaten tüm ekranı kaplar). `force=True`,
    zaten bir embedded_image'i olan slaytların da (ör. kaynaktan çıkarılmış bir
    diyagramın) üzerine yazılmasına izin verir — varsayılan bunu YAPMAZ, kaynağın
    kendi gerçek görseli her zaman daha güvenilirdir."""
    if slide.level == "chapter":
        return False
    if slide.code:
        return False
    if slide.background_image:
        return False
    # Destek görseli + tam ekran YZ arka plan aynı anda slaydı gereksiz kalabalıklaştırır.
    if slide.ai_background_image:
        return False
    if slide.embedded_image and not force:
        return False
    return True


def eligible_for_background(slide: Slide, force: bool = False) -> bool:
    """Normal tema üst yazılarını koruyan YZ arka planı için uygunluk kontrolü."""
    if slide.code or slide.background_image or slide.embedded_image:
        return False
    if slide.ai_background_image and not force:
        return False
    return True


def enrich_slides_with_images(
    pdir: Path, slides: list[Slide], api_key: str, force: bool = False,
    progress_cb: Callable[[int, int, str], None] | None = None,
    max_workers: int = IMAGE_ENRICH_MAX_WORKERS,
    mode: str = "support",
) -> list[Slide]:
    """Uygun slaytlara (bkz. eligible_for_enrichment) görsel eklemeye çalışır — her
    slaydın araması/üretimi BAĞIMSIZ olduğundan (birinin sonucu diğerini etkilemez,
    vision_narration.py'nin sayfa-bağlamı zincirinin AKSİNE) `max_workers` kadar iş
    parçacığında PARALEL çalıştırılır; asıl darboğaz ağ gecikmesi olduğundan bu gerçek
    bir hızlanma sağlar.

    KRİTİK: paylaşılan dosyalara (script.json, cost_ledger.json) yazma HER ZAMAN ana iş
    parçacığında, `as_completed` sonuçları işlenirken, SIRAYLA yapılır — worker'lar
    SADECE ağ çağrısını (find_image_for_slide) yapar. Bu, birden fazla iş parçacığının
    aynı JSON dosyasına aynı anda yazıp onu bozmasını (race condition) engeller. Her
    başarılı slayttan sonra script.json HEMEN kaydedilir — süreç yarıda kesilirse (API
    hatası, kullanıcı iptali, ağ sorunu) o ana kadar eklenen görseller kaybolmaz. Tek
    bir slaydın başarısız olması diğerlerini durdurmaz (bkz. modül docstring'i)."""
    from app.cost_ledger import record as record_cost
    from app.pipeline import save_script

    if mode not in ENRICHMENT_MODES:
        raise ValueError(f"Bilinmeyen görsel zenginleştirme modu: {mode!r}")

    background_mode = mode == "background"
    images_dir = pdir / "assets" / ("backgrounds" if background_mode else "images")
    images_dir.mkdir(parents=True, exist_ok=True)

    eligibility = eligible_for_background if background_mode else eligible_for_enrichment
    eligible_indices = [i for i, s in enumerate(slides) if eligibility(s, force)]
    total = len(eligible_indices)
    if total == 0:
        return slides

    def work(index: int):
        if background_mode:
            return find_background_for_slide(slides[index], api_key)
        return find_image_for_slide(slides[index], api_key)

    done = 0
    with ThreadPoolExecutor(max_workers=min(max_workers, total), thread_name_prefix="image-enrich") as pool:
        futures = {pool.submit(work, index): index for index in eligible_indices}
        for future in as_completed(futures):
            index = futures[future]
            slide = slides[index]
            try:
                result = future.result()
            except Exception:
                kind = "image_background_error" if background_mode else "image_enrich_error"
                record_cost(pdir, provider="gemini", kind=kind, requests=1)
                result = None
                errored = True
            else:
                kind = "image_background" if background_mode else "image_enrich"
                record_cost(pdir, provider="gemini", kind=kind, requests=1)
                errored = False
            done += 1
            if progress_cb:
                progress_cb(done, total, slide.title)
            if errored or result is None:
                continue
            image_bytes, ext, source = result
            suffix = "_background" if background_mode else ""
            image_path = images_dir / f"slide_{index + 1:03d}{suffix}.{ext}"
            image_path.write_bytes(image_bytes)
            if background_mode:
                slide.ai_background_image = str(image_path)
            else:
                slide.embedded_image = str(image_path)
                slide.image_source = source
            save_script(pdir, slides)
    return slides
