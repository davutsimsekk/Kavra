# Ders Stüdyosu — Claude Devir Teslim Notu (2026-09-13 gece turu)

Bu dosya `D:\proje\ders_video` projesini bir sonraki oturuma güvenle devretmek için güncellendi. Önceki CUTOFF.md'nin (Codex turu) tüm içeriği hâlâ geçerli arka plan bilgisidir; bu bölüm ONUN ÜZERİNE, aynı gece (Claude) yapılan ek turu anlatır.

**Okuma sırası:** `HANDOFF.md` → bu dosya → `CUTOFF_ROADMAP.md`

## Bu turda tamamlanan ve TEST EDİLMİŞ özellikler

Hepsi gerçek 331 slaytlık/136 bölümlük `Gomulu_Programlama_Rehberi` projesinde Playwright ile canlı doğrulandı, ayrıca birim/API testleri eklendi (toplam test sayısı 116).

1. **Kaynak izlenebilirliği + hedefli yeniden üretim** (`app/regenerate.py`, `app/models.py`'de `Slide.source_section_ids/source_titles/manually_edited`)
   - Her üretilen slayt, `app.generation_checkpoint.source_fingerprint` ile aynı kimlikle hangi kaynak bölüm(ler)den geldiğini taşır.
   - Editörde "Kaynak: X" etiketi + "Bu slaytı yeniden üret" butonu — sadece o slaydın kaynağını gönderip tek bir küçük LLM çağrısıyla yeniler, aynı kaynağı paylaşan komşu slaytları (sibling group) bulup topluca değiştirir.
   - Regenerate öncesi otomatik snapshot alınır (bkz. madde 6).
   - **Yan bulgu ve düzeltme:** `AgentCliNarrationGenerator`'da (varsayılan sağlayıcı) Gemini/OpenAI'da olan "bozuk JSON'u düzelt" retry mantığı hiç yoktu — canlı testte gerçek bir JSON parse hatası yakalandı, düzeltildi (`app/llm/agent_cli_provider.py`).

2. **Çalışma materyali dışa aktarımı** (`app/export.py`) — LLM çağrısı yok, tamamen deterministik
   - Ders Notu (.md), Düz Transkript (.txt), Anki kartları (.tsv, front=başlık/back=anlatım+maddeler), Kendini Test Et + Cevap Anahtarı (.md).
   - `GET /api/projects/{id}/export/{notes|transcript|anki|quiz}`, Stüdyo sekmesinde indirme linkleri.

3. **Render öncesi tahmin kartı** (`app/render_estimate.py`)
   - Tahmini süre/kelime, bu render'da kaç slaytın üretileceği/atlanacağı, tahmini disk (gerçek segment boyutlarından, ilk render'da kaba fallback), boş disk alanı, yerel/ücretsiz vs ücretli sağlayıcı notu (kesin fiyat ASLA uydurulmuyor).

4. **Bölüm bazlı MP4/MP3 + YouTube chapters** (`app/chapters.py`) — roadmap'te P2 idi ama gerçek proje 6.4 saatlik çıktığı için önceliklendirildi
   - `level="chapter"` slaytlarına göre otomatik bölümleme (ilk chapter'dan önceki slaytlar "Giriş" bölümü olur).
   - Var olan `segment_NNN.mp4`/`slide_NNN.mp3` dosyalarını ffmpeg concat ile birleştirir — **yeniden render YOK**, ek TTS/LLM maliyeti yok.
   - Gerçek projede 47 bölüm tespit edildi, tamamı export edildi ve doğrulandı; YouTube açıklama kutusuna yapıştırılacak zaman damgalı bölüm listesi üretiliyor.

5. **Snapshot listeleme + geri yükleme** (`app/pipeline.py`: `list_snapshots`, `restore_snapshot`)
   - `snapshot_script()` zaten Codex turunda vardı (sadece dosyaya kopyalıyordu); bu turda listeleme + tek tıkla geri yükleme UI'sı eklendi.
   - Geri yükleme öncesi MEVCUT hâl de otomatik snapshot'lanır — geri yükleme her zaman geri alınabilir.
   - Anlatı sekmesinde, kalite kartının altında kompakt bir liste.

6. **Telaffuz sözlüğü düzenleyici** (`app/tts/pronunciation.py`: `load_overrides/save_overrides/effective_map`)
   - Üstteki "Ayarlar" dişli butonu (Codex'in bıraktığı, hiçbir işlevi olmayan ölü buton) artık bu modalı açıyor.
   - Kullanıcı yeni terim ekleyebilir/var olan bir varsayılanı ezebilir/kaldırabilir; `pronunciation_overrides.json` (proje kökünde, tüm projelerde geçerli global dosya) içinde saklanır.
   - **Hızlı ses önizlemesi**: bir cümle yaz, Edge-TTS ile anında gerçek ses üretilip tarayıcıda çalınıyor — Claude'un kendisi sesi duyamadığı için bu, kullanıcının telaffuzu doğrulayabileceği tek yol.

7. **Katmanlı hash / akıllı önbellek** (`app/pipeline.py`) — roadmap'in asıl P0-2 maddesi
   - Artık `_narration_hash` (anlatım+sağlayıcı+ses+hız, telaffuz normalizasyonundan SONRAKİ metinle) ayrı tutuluyor; tema/altyazı/geçiş/Ken-Burns değişince ses dosyası ASLA yeniden sentezlenmiyor, sadece görsel+segment yenileniyor.
   - Gerçek word-timing verisi de `slide_NNN.words.json`'da önbelleğe alınıyor (ses yeniden kullanılınca altyazı tahminen değil gerçek zamanlamayla üretiliyor).
   - Telaffuz sözlüğü değişince SADECE etkilenen slaytların hash'i değişir (çünkü hash normalize EDİLMİŞ metni kapsıyor) — "yalnız etkilenen slaytlar" gereksinimini ayrı bir versiyon takibi olmadan otomatik karşılıyor.

## ✅ ÇÖZÜLDÜ: `ders.mp4` kazayla ezildi → onarıldı ve doğrulandı (2026-09-13 06:17)

**Son durum:** Onarım render'ı tamamlandı. `ders.mp4` şu an 331 slaytın tamamını içeriyor, **6.77 saat**, **~566 MB** — `ffprobe` ile doğrulandı. Tam test paketi (117 test) yeşil. Aşağıdaki eski analiz (kök neden vb.) hâlâ geçerli arka plan bilgisi olarak bırakıldı.

**Onarım sırasında ek bir aksama oldu:** İlk onarım denemesi (331 slaytın ~294'üne kadar ilerlemişti) sistem düşük bellek uyarısıyla kesildi; aynı anda backend API süreci (uvicorn, port 8765) de düşmüştü. İkisi de yeniden başlatıldı (`_cache/restore_full_render.py` tekrar çalıştırıldı, backend `run_web_dev.bat`'teki ile aynı ortam değişkenleriyle port 8765'te yeniden ayağa kaldırıldı) ve onarım ikinci denemede ~59 dakikada (3525.8 sn) sorunsuz tamamlandı.

**Küçük bir bulgu (acil değil, ileride bakılabilir):** İkinci onarım denemesinde `_slide_hash` (tam önbellek — "hiçbir şey değişmedi, hepsini atla" hızlı yol) hiçbir slaytta eşleşmedi; sadece `_narration_hash` (ses yeniden kullanımı) eşleşti, yani ses yeniden sentezlenmedi ama görsel+segment her slayt için ffmpeg ile yeniden üretildi (~9 sn/slayt). En olası açıklama: ilk onarım denemesi, `_renderable_signature` fonksiyonu bu oturumda hâlâ düzenlenirken (kök neden analizi sürerken) başlatılmıştı, dolayısıyla o denemenin yazdığı `.hash` dosyaları fonksiyonun ERKEN bir sürümüyle hesaplanmış olabilir — kod sabitlendikten sonraki bu ikinci tam geçiş, tüm `.hash` dosyalarını GÜNCEL fonksiyonla yeniden yazdı. Bir sonraki render (tema/altyazı değişikliği gibi) artık gerçek anlamda anlık önbellek isabeti almalı; bu teoriyi doğrulamak istenirse `render_video`'yu değişiklik yapılmadan art arda iki kez (test projesinde, GERÇEK projede değil) çalıştırıp ikinci koşunun anlık bitip bitmediğine bakmak yeterli.

## (Eski analiz — hâlâ geçerli arka plan bilgisi)

Madde 7'yi canlı doğrularken `render_video()`'yu doğrudan 5 slaytlık kısaltılmış bir listeyle çağırdım (hızlı test amaçlı). **`render_video` her çağrıldığında `ders.mp4`/`ders.mp3`'ü verilen slayt listesiyle YENİDEN YAZAR** — bunu unutup gerçek 331 slaytlık/6.4 saatlik final videonun üzerine 5 slaytlık bir test videosu yazdım.

Hemen fark edip tüm 331 slaytla, orijinal ayarlarla (piper, tema=auto) bir onarım render'ı başlattım. Bunu araştırırken **asıl kök neden bug'ını** da buldum: `_slide_hash`, `Slide.to_dict()`'in TAMAMINI hash'liyordu; bu turda `Slide`'a eklenen yeni metadata alanları (`sourceSectionIds` vb.) yüzünden TÜM projenin (sadece bozduğum 5 slayt değil) hash'i geçersiz sayıldı, onarım render'ı bu yüzden ~331 slaytın tamamını (ses dahil) yeniden üretmek zorunda kaldı — normalde saniyeler sürecek bir onarım ~1.5-2 saate çıktı (Piper ücretsiz olduğu için sadece zaman kaybı, para değil).

**Düzeltildi:** `_slide_hash` artık sadece render'ı gerçekten etkileyen alanları (title/bullets/code/narration/level) hash'liyor, `Slide.to_dict()`'in tamamını değil. Regresyon testi eklendi (`tests/test_render_cache.py::SlideHashIgnoresMetadataFieldsTests`) — Slide'a gelecekte yeni bir metadata alanı eklenirse artık render cache'ini asla geçersiz kılmayacak.

**Bu oturumun sonunda kontrol et:**
```bash
ls -la projects/Gomulu_Programlama_Rehberi/ders.mp4
# beklenen: ~331 slaytlık, ~6.4 saatlik, birkaç yüz MB'lık gerçek dosya
# eğer hâlâ küçükse (≈5MB) onarım render'ı bitmemiş demektir, _cache/restore_full_render.py'yi
# tekrar çalıştır: venv\Scripts\python.exe _cache\restore_full_render.py
```

**Ders:** `render_video()`'yu asla kısmi bir slayt listesiyle GERÇEK bir projede test etme — her zaman `render_video`'yu ya (a) test-only geçici bir proje dizininde ya da (b) tam slayt listesiyle çağır. Kısmi test gerekiyorsa `_slide_hash`/`_narration_hash` gibi saf fonksiyonları doğrudan çağır, `render_video`'nun kendisini değil.

## Son test durumu

- `venv\Scripts\python.exe -m pytest tests\ -q` → **117 passed** (son doğrulama 06:17'de, onarım sonrası)
- `cd webui && npm run build` → başarılı
- Playwright ile canlı doğrulanan akışlar: kaynak izlenebilirliği+regenerate (gerçek Claude Agent çağrısıyla), dışa aktarım (4 format), render tahmini, bölüm export (47 bölüm, gerçek ffmpeg), snapshot restore, telaffuz sözlüğü (ekle/kaldır/önizleme, gerçek Edge-TTS sesi).

## Çalışma ağacında dikkat edilecekler

Yeni değişen/eklenen dosyalar (önceki CUTOFF'un listesine ek olarak):

```text
 M app/llm/agent_cli_provider.py   (JSON-repair retry eklendi)
 M app/llm/base.py                 (tag_slides_with_source)
 M app/models.py                   (Slide: source_section_ids/source_titles/manually_edited)
 M app/pipeline.py                 (katmanlı hash, snapshot list/restore, önbellek düzeltmesi)
 M studio_web/api.py                (regenerate/export/chapters/snapshots/pronunciation endpoint'leri)
 M webui/src/App.jsx, styles.css   (tüm yeni UI parçaları)
?? app/chapters.py
?? app/export.py
?? app/regenerate.py
?? app/render_estimate.py
?? pronunciation_overrides.json   (varsayılan olarak YOK/boş — kullanıcı doldurunca oluşur, .gitignore'a eklenmeli mi düşünülebilir)
?? tests/test_chapters.py, test_export.py, test_regenerate.py, test_render_cache.py,
   test_render_estimate.py, test_snapshots.py, test_pronunciation_overrides.py
```

`requirements.txt` farkı hâlâ önceki turdan (Claude tarafından encoding düzeltilip Coqui notu geri eklendi) — sorun yok, incelendi.

## Bir sonraki agent için öneriler

Roadmap'teki (`CUTOFF_ROADMAP.md`) P0/P1 maddelerinin büyük kısmı artık tamamlandı. Kalanlar:

- **P2: Yakın tekrar/kapsam analizi** — quality_gate şu an sadece birebir tekrarı buluyor, yakın-anlamlı tekrarı bulmuyor.
- **P1: Proje sağlık merkezi (tek ekran)** — şu an kalite/asset/tahmin/bölüm kartları farklı sekmelerde dağınık; tek bir "sağlık" görünümünde toplamak istenirse mümkün, ama şu an her biri kendi bağlamında (Anlatı'da kalite, Stüdyo'da asset+tahmin+bölüm) mantıklı duruyor — zorunlu değil.
- **Snapshot UI'sı** şu an sadece son 10 sürümü gösteriyor, diff/karşılaştırma yok — istenirse eklenebilir.
- `ders.mp4` onarımı tamamlandı ve doğrulandı (yukarıdaki "ÇÖZÜLDÜ" bölümüne bak) — tekrar kontrol etmeye gerek yok.
- İstenirse yukarıdaki "küçük bulgu"yu (tam hash'in ikinci onarım denemesinde neden anlık isabet almadığı) bir test-projesinde doğrulayıp kapatmak iyi olur, ama acil değil.
