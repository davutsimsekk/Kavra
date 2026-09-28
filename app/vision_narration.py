"""PDF kaynaklarını GÖRÜNTÜ + METİN birlikte kullanarak anlatan, normal metin-tabanlı
üretimden (app/llm/*) tamamen AYRI, isteğe bağlı bir üretim modu — gerçek bir hocanın
sayfaya BAKARAK anlattığı gibi. Normal akışta anlatım LLM'i sadece ayrıştırmadan çıkan
DÜZ METNİ görür; bir tablo, diyagram, formül ya da ok/şema içeren bir sayfada bu metin
genelde bozulmuş/eksik olur. Bu modül her hedef sayfa için sayfanın kendi rasterize
edilmiş görüntüsünü DOĞRUDAN modele gönderir.

Mimari kararlar:
- TEK bir API çağrısı TAM OLARAK BİR hedef sayfa için çalışır (komşu sayfaların/önceki
  slaytın anlatımı sadece kısa bir BAĞLAM notu olarak eklenir, kendileri anlatılmaz) —
  bu, modelin birden çok sayfayı karıştırıp yanlış sayfaya ait bilgi üretmesini önler.
- Sayfa görüntüsü, ayrıştırma sırasında DEĞİL, bu özellik gerçekten tetiklendiğinde
  ON-DEMAND rasterize edilir (kaynak PDF'in kendisinden, pdir/source_path.txt üzerinden
  yeniden açılarak) — bu yüzden ayrıştırma akışına (app/parsers/*) hiçbir değişiklik
  gerekmedi, sadece "hangi PDF sayfası" bilgisini section başlığındaki "N. ..." önekinden
  çıkarıyoruz (bkz. app/parsers/pdf_parser.py: her section başlığı HER ZAMAN gerçek
  sayfa numarasıyla başlar).
- Sonuçlar (görüntü+metin+model+prompt sürümüne göre) önbelleklenir — aynı sayfa
  değişmediği sürece yeniden işlenmez, bir kez çalıştırdıktan sonra tekrar çalıştırmak
  neredeyse bedavadır.
- Model GÖREMEDİĞİ bir detayı TAHMİN ETMEMELİ — bu kural prompt'a açıkça yazılır.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.config import PROMPTS_DIR
from app.gemini_retry import call_with_retry
from app.llm.base import extract_json_array, tag_slides_with_source
from app.models import RawSection, Slide

PROMPT_VERSION = 1
# vision_caption.py ile aynı varsayılan — bilinen-multimodal bir model.
DEFAULT_MODEL = "gemini-3.7-flash"
_PAGE_IMAGE_DPI = 150

_BASE_TEMPLATE_PATH = PROMPTS_DIR / "lecture_script_prompt.md"
_PAGE_NUMBER_RE = re.compile(r"^\s*(\d+)\.\s")

_VISION_PREAMBLE = (
    "ÖNEMLİ: Bu normal metin tabanlı bir istek DEĞİL. Sana bu ders materyalinin TEK BİR "
    "sayfasının hem GÖRÜNTÜSÜ (ekli resim) hem de o sayfadan çıkarılmış METNİ veriliyor.\n\n"
    "GÖRÜNTÜYE BAK — sayfada bir tablo, diyagram, formül, ok/şema, grafik varsa bunu "
    "METİNDEN değil DOĞRUDAN GÖRÜNTÜDEN oku ve anlat. Metin çıkarımı bunları kaçırmış ya "
    "da bozmuş olabilir; görüntü her zaman daha güvenilir kaynaktır.\n\n"
    "KRİTİK KURAL: Görüntüde net biçimde OKUYAMADIĞIN bir detayı (bulanık bir sayı, "
    "kesilmiş bir etiket, çok küçük bir yazı) ASLA TAHMİN ETME/UYDURMA — o detayı basitçe "
    "atla. Uydurma bilgi gerçek bir derste asla kabul edilemez.\n\n"
    "Çıktı SADECE TEK BİR slayt içeren bir JSON dizisi olmalı (aşağıdaki kurallara göre) "
    "— bu sayfa için tam olarak bir slayt üret, birden fazla değil, sıfır değil.\n\n"
)


def _build_prompt(section: RawSection, neighbor_context: str, style_note: str) -> str:
    base = _BASE_TEMPLATE_PATH.read_text(encoding="utf-8")
    page_block = f"## {section.title}\n{section.text}".strip()
    prompt = _VISION_PREAMBLE + base.replace("{RAW_CONTENT}", page_block)
    if neighbor_context:
        prompt += (
            "\n\n---\nBAĞLAM (SADECE geçiş/tutarlılığı korumak için — bunu ANLATMA, sadece "
            f"nerede olduğunu anlamak için oku):\n{neighbor_context}\n---\n"
        )
    if style_note:
        prompt += f"\n\nEk üslup notu: {style_note}\n"
    return prompt


def page_number_from_title(title: str) -> int | None:
    """app/parsers/pdf_parser.py her section başlığını HER ZAMAN gerçek pymupdf sayfa
    numarasıyla ("N. ...") başlatır — bu ön ekten hangi PDF sayfasının rasterize
    edileceğini çıkarır. Eşleşmezse (elle eklenmiş bir slayt, PDF olmayan bir kaynak)
    None döner — çağıran taraf bu bölümü görsel tabanlı üretime uygun saymaz."""
    match = _PAGE_NUMBER_RE.match(title or "")
    return int(match.group(1)) if match else None


def _rasterize_page(pdf_path: Path, page_number: int, pdir: Path) -> Path:
    """Sayfayı önbellekten (pdir/source_pages/) döndürür, yoksa kaynak PDF'ten yeniden
    rasterize edip kaydeder — "sayfaları birebir kullan" modu zaten aynı sayfayı
    üretmişse (aynı klasör/adlandırma paylaşılır) burada tekrar işlenmez."""
    page_dir = pdir / "source_pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    image_path = page_dir / f"page_{page_number:03d}.png"
    if image_path.is_file():
        return image_path

    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    try:
        if not (1 <= page_number <= len(doc)):
            raise ValueError(f"Sayfa {page_number} kaynak PDF'te bulunamadı (toplam {len(doc)} sayfa).")
        pixmap = doc[page_number - 1].get_pixmap(dpi=_PAGE_IMAGE_DPI)
        pixmap.save(str(image_path))
    finally:
        doc.close()
    return image_path


def _cache_path(pdir: Path) -> Path:
    return pdir / "vision_narration_cache.json"


def _load_cache(pdir: Path) -> dict:
    path = _cache_path(pdir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_cache(pdir: Path, cache: dict) -> None:
    path = _cache_path(pdir)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _cache_key(image_bytes: bytes, section: RawSection, model: str) -> str:
    h = hashlib.sha256()
    h.update(image_bytes)
    h.update(section.text.encode("utf-8"))
    h.update(model.encode("utf-8"))
    h.update(str(PROMPT_VERSION).encode("utf-8"))
    return h.hexdigest()[:24]


def generate_slide_for_page(
    image_path: Path, section: RawSection, neighbor_context: str, style_note: str,
    api_key: str, model: str = DEFAULT_MODEL,
) -> Slide:
    """TEK bir sayfa için (görüntü + metin) TEK bir API çağrısıyla TEK bir slayt üretir."""
    if not api_key:
        raise ValueError("Görsel tabanlı anlatım için bir Gemini API anahtarı gerekli.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    image_bytes = image_path.read_bytes()
    prompt = _build_prompt(section, neighbor_context, style_note)
    contents = [prompt, types.Part.from_bytes(data=image_bytes, mime_type="image/png")]

    def _run():
        return client.models.generate_content(model=model, contents=contents)

    resp = call_with_retry(_run, feature_name="görsel tabanlı anlatım")
    data = extract_json_array(resp.text or "[]")
    if not data:
        raise ValueError(f"'{section.title}' sayfası için model boş/geçersiz bir yanıt döndürdü.")
    return Slide.from_dict(data[0])


def eligible_sections(pdir: Path, sections: list[RawSection]) -> list[RawSection]:
    """Görsel tabanlı üretime uygun bölümler — bkz. page_number_from_title (sadece
    gerçek bir PDF sayfasından gelen bölümler, elle eklenmiş ya da PDF-dışı kaynaklar
    hariç)."""
    return [s for s in sections if page_number_from_title(s.title) is not None]


def _resolve_source_pdf_path(pdir: Path) -> Path:
    """Kaynak PDF'in fiziksel yolunu iki farklı proje düzeni için bulur:

    1) Eski (tek kaynaklı) projeler: `pdir/source_path.txt` (bkz. app/pipeline.py
       `parse_and_cache` — ayrıştırma sırasında HER ZAMAN yazılır).
    2) "Ders" (course) projelerindeki video çalışma alanları (bkz. app/course_projects.py):
       bunların KENDİ `source_path.txt`'i YOK — `import_source`/`create_video`'nun hiçbiri
       bunu yazmıyor (bu fonksiyon eklenmeden önce course projelerinde görsel tabanlı
       anlatım HİÇBİR ZAMAN çalışmıyordu, her zaman "kaynak dosya yolu bulunamadı"
       hatası veriyordu). Bunun yerine `video.json`'daki `sourceIds`'ten, kaynağın kendi
       değişmez kopyasına (`<proje_kökü>/sources/<id>/document.*`) geri gidilir. Video TEK
       bir PDF kaynağından oluşturulmuşsa bu güvenle çözülebilir; birden fazla PDF kaynağı
       karışıksa (hangi sayfanın hangi PDF'ten geldiğini section bazında ayırmak gerekir,
       bu henüz yapılmıyor) açıkça desteklenmediği söylenir — YANLIŞ bir PDF'ten sayfa
       göstermek hiç göstermemekten çok daha kötü bir hata olurdu.
    """
    direct = pdir / "source_path.txt"
    if direct.is_file():
        return Path(direct.read_text(encoding="utf-8").strip())

    video_meta_path = pdir / "video.json"
    if not video_meta_path.is_file():
        raise ValueError("Bu projenin kaynak dosya yolu bulunamadı; görsel tabanlı anlatım üretilemez.")
    try:
        video_meta = json.loads(video_meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Bu projenin kaynak dosya yolu bulunamadı; görsel tabanlı anlatım üretilemez.")

    course_root = pdir.parent.parent
    pdf_candidates: list[Path] = []
    for source_id in video_meta.get("sourceIds") or []:
        source_meta_path = course_root / "sources" / source_id / "source.json"
        if not source_meta_path.is_file():
            continue
        try:
            source_meta = json.loads(source_meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if source_meta.get("type") == "pdf":
            pdf_candidates.append(course_root / "sources" / source_id / source_meta["filename"])

    if not pdf_candidates:
        raise ValueError(
            "Bu videonun kaynakları arasında görsel tabanlı anlatım için uygun bir PDF bulunamadı."
        )
    if len(pdf_candidates) > 1:
        raise ValueError(
            "Bu video birden fazla PDF kaynağından oluşturulmuş; görsel tabanlı anlatım şu an "
            "yalnızca TEK bir PDF kaynağından oluşturulan videolarda destekleniyor."
        )
    return pdf_candidates[0]


def generate_slides_with_vision(
    pdir: Path, sections: list[RawSection], api_key: str, model: str = DEFAULT_MODEL,
    style_note: str = "", progress_cb=None,
) -> list[Slide]:
    """Uygun TÜM bölümler için sırayla (görüntü+metin) tabanlı slaytlar üretir.
    Önbellekte olan sayfalar için API çağrısı YAPILMAZ. Bir sayfanın üretimi başarısız
    olursa (API hatası, okunamayan görüntü) o slayt tamamen kaybolmaz — düz metinden
    basit bir yedek slayta düşer, diğer sayfaların işlenmesi durmaz."""
    pdf_path = _resolve_source_pdf_path(pdir)
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("Görsel tabanlı anlatım şu an yalnızca PDF kaynaklar için destekleniyor.")
    if not pdf_path.is_file():
        raise ValueError(f"Kaynak PDF artık bulunamıyor: {pdf_path}")

    from app.cost_ledger import record as record_cost

    cache = _load_cache(pdir)
    targets = eligible_sections(pdir, sections)
    total = len(targets)
    slides: list[Slide] = []
    neighbor_context = ""
    for done, section in enumerate(targets, start=1):
        if progress_cb:
            progress_cb(done, total, section.title)
        page_number = page_number_from_title(section.title)
        try:
            image_path = _rasterize_page(pdf_path, page_number, pdir)
            image_bytes = image_path.read_bytes()
            key = _cache_key(image_bytes, section, model)
            cached = cache.get(key)
            if cached:
                slide = Slide.from_dict(cached)
            else:
                # Önbellekte yoksa gerçek bir API çağrısı yapılacak — sadece bu durumda
                # istek sayısı kaydedilir (bkz. app/cost_ledger.py'nin "asla fiyat
                # tahmini üretme" ilkesi; burada da yalnızca GERÇEKTEN yapılan çağrı
                # sayılıyor, önbellekten gelenler değil).
                record_cost(pdir, provider="gemini-vision", kind="vision_narrate", requests=1)
                slide = generate_slide_for_page(image_path, section, neighbor_context, style_note, api_key, model)
                cache[key] = slide.to_dict()
                _save_cache(pdir, cache)
        except Exception:
            # Görsel tabanlı üretim başarısız olsa bile elimizde zaten METİN var —
            # slayt tamamen kaybolmasın diye ondan basit bir yedek slayt kurulur.
            slide = Slide(title=section.title, narration=section.text or section.title)
        tag_slides_with_source([slide], [section])
        neighbor_context = (slide.narration or "")[:400]
        slides.append(slide)
    return slides
