# Ders Stüdyosu — Ayrıntılı Gelecek Yol Haritası

Bu belge `CUTOFF.md` dosyasının teknik ürün yol haritası ekidir. Claude önce `HANDOFF.md` ve `CUTOFF.md`, ardından bu dosyayı okumalıdır.

## Uzun vadeli ürün vizyonu

Uygulamanın hedefi yalnızca bir PDF'yi videoya çevirmek değildir. İdeal ürün, öğrencinin elindeki dağınık ders materyalini kaynakla bağlantısı korunmuş ve farklı biçimlerde tekrar çalışılabilir bir öğrenme paketine dönüştürmelidir.

```text
Kaynak yükle
  → bölüm ve sayfaları doğrula
  → üretilecek kapsamı seç
  → anlatı/slayt planını oluştur
  → kalite ve kaynak tutarlılığı kontrolü
  → kullanıcı düzenlemesi ve onayı
  → TTS ve video renderı
  → video + ses + transkript + quiz + flashcard paketi
  → daha sonra kaldığın yerden çalışma ve yeniden üretme
```

Ana ilke: Aynı veri ikinci kez gereksiz yere üretilmemeli. Kaynak parse sonucu, LLM çıktısı, kullanıcı düzenlemeleri, ses, görsel ve video segmentleri birbirinden ayrı cache/checkpoint katmanlarına sahip olmalıdır.

## Öncelik ve maliyet matrisi

| Öncelik | Özellik | Fayda | Geliştirme | Çalışma maliyeti |
|---|---|---:|---:|---:|
| P0 | Mevcut kalite/job/asset paketinin gerçek smoke testi | Çok yüksek | Düşük | Ücretsiz |
| P0 | TTS/video slayt-seviyesi resume görünümü | Çok yüksek | Orta | Ücretsiz |
| P0 | Kaynak referansı ve bölüm bazlı regenerate | Çok yüksek | Orta | Yalnız istenen LLM çağrısı |
| P1 | Render süresi/disk/API maliyet tahmini | Yüksek | Düşük | Ücretsiz |
| P1 | Proje sağlık paneli ve anlaşılır hata günlükleri | Yüksek | Düşük-Orta | Ücretsiz |
| P1 | Anki kartı, quiz ve Markdown dışa aktarımı | Yüksek | Orta | İlk sürüm ücretsiz |
| P1 | Snapshot, undo ve geri yükleme | Yüksek | Orta | Ücretsiz |
| P2 | Yakın tekrar ve konu kapsamı analizi | Orta-Yüksek | Orta | Ücretsiz |
| P2 | Bölüm bazlı ayrı MP4/MP3 ve YouTube chapters | Yüksek | Orta | Ücretsiz |
| P2 | Telaffuz sözlüğü editörü | Orta-Yüksek | Düşük | Ücretsiz |
| P2 | Gelişmiş altyazı editörü | Orta | Yüksek | Ücretsiz |
| P3 | Yerel semantik arama/RAG | Yüksek | Yüksek | Donanıma bağlı |
| P3 | Otomatik AI görselleri | Orta | Yüksek | Model/API'ye göre pahalı |
| P3 | Çok konuşmacılı podcast modu | Orta | Yüksek | TTS'ye göre pahalı |

P0 tamamlanmadan P2/P3 görsel özelliklere geçilmemelidir. Önce bütünlük ve resume güvenilir olmalıdır.

## P0 — Önce yapılması gerekenler

### 1. Son paketin gerçek uygulamada smoke testi

Kontrol listesi:

- Kalite kartı proje açılınca görünüyor mu?
- Anlatımı boş bir slayt kaydedilince kritik hata gösteriliyor mu?
- Kritik hata varken render düğmesi kapanıyor mu?
- Doğrudan API çağrısı da worker başlamadan `409` dönüyor mu?
- Anlatım düzeltildiğinde rapor yenilenip render açılıyor mu?
- Slayt silindikten sonraki renderda fazla numaralı dosyalar `_orphaned` altına taşınıyor mu?
- Uygulama yeniden açıldığında yarım job anlaşılır şekilde raporlanıyor mu?
- `requirements.txt` farkı kullanıcı değişimi olarak korunuyor mu?

### 2. TTS/video slayt-seviyesi resume

Amaç: Dört saatlik video 80. slaytta kesildiğinde ilk 79 slayt yeniden üretilmesin.

Önerilen katmanlı hash yapısı:

- `narrationHash`: anlatım + TTS provider + voice + rate + telaffuz sözlüğü
- `imageHash`: slayt metni/kodu + tema + çözünürlük
- `segmentHash`: audio hash + image hash + altyazı + FPS + hareket seçenekleri

Kabul kriterleri:

- Aynı ayarlarla ikinci render hazır segmentleri atlar.
- Yalnız anlatımı değişen slaytın sesi ve segmenti yeniden oluşur.
- Tema değişince MP3 korunur; görsel ve segment yenilenir.
- Altyazı değişince MP3 korunur.
- Sıfır bayt veya okunamayan dosya `ready` sayılmaz.
- Uygulama kapatılıp açılınca hazır dosyalar kullanılabilir.
- UI `112/148 ses hazır`, `90/148 segment hazır` gibi durum gösterir.

Manifest zamanla her slayt için şu durumu tutabilir:

```json
{
  "slideIndex": 12,
  "contentHash": "...",
  "audio": {"status": "ready", "path": "slide_012.mp3", "duration": 31.4},
  "image": {"status": "ready", "path": "slide_012.png"},
  "segment": {"status": "ready", "path": "segment_012.mp4"}
}
```

İlk küçük sürüm yalnız doğru hazır/eksik sayımını API ve UI'da göstermelidir. Otomatik retry/cancel daha sonra eklenebilir.

### 3. Kaynak izlenebilirliği ve hedefli regenerate

Her slayt mümkünse şu metadata alanlarını taşımalıdır:

- `sourceSectionIds`
- `sourcePages`
- `generationId`
- `generatedAt`
- `manuallyEdited`

Kabul kriterleri:

- Düzenleyicide kaynak bölüm/sayfa görülebilir.
- Kullanıcı kaynak metni slaytla yan yana açabilir.
- `Bu slaytı yeniden üret` yalnız gerekli kaynak ve kompakt bağlamı gönderir.
- Sonuç kabul edilene kadar mevcut elle düzenlenmiş slayt ezilmez.
- Regenerate öncesi eski sürüm snapshot olarak saklanır.
- Bir bölümden birden fazla slayt üretildiğinde ilişki korunur.

## P1 — Yüksek faydalı ücretsiz özellikler

### 4. Proje sağlık merkezi

Tek ekranda gösterilebilecekler:

- Kaynak bölümü: toplam/tamamlanan/bekleyen/başarısız
- Slayt: toplam/kritik hata/uyarı
- Ses: hazır/eksik/orphan
- Segment: hazır/eksik
- Tahmini ve gerçekleşen toplam süre
- Son başarılı aşama ve son hata
- Kullanılan LLM/TTS sağlayıcısı; anahtarın kendisi gösterilmez
- `Devam et`, `Yalnız hatalıları dene`, `Manifesti yenile` eylemleri

Kabul kriteri: Kullanıcı dosya klasörünü açmadan projenin neden tamamlanmadığını anlayabilmelidir.

### 5. Render öncesi maliyet ve kaynak tahmini

Gösterilecekler:

- Toplam karakter/kelime
- Tahmini konuşma süresi
- Yeniden kullanılabilecek hazır ses ve segment sayısı
- Yeniden üretilecek slayt sayısı
- Yaklaşık geçici disk ihtiyacı ve boş disk alanı
- Yerel sağlayıcı için `ücretsiz/yerel`
- Bulut sağlayıcı için tahmini maliyet veya `fiyat bilinmiyor`

Bulut fiyatları hard-code edilmemelidir. Sağlayıcı metadata'sı veya kullanıcı tarafından girilen birim fiyat kullanılmalıdır. Fiyat bilinmiyorsa tahmin uydurulmamalıdır.

### 6. Öğrenme paketi ve dışa aktarımlar

Aynı projeden üretilebilecek çıktılar:

- Markdown ders notu
- Düz transkript
- SRT/ASS altyazı
- Anki uyumlu TSV/CSV
- Mini quiz ve cevap anahtarı
- Bölüm özetleri
- Yalnız ses/podcast çıktısı

İlk sürüm LLM çağrısı yapmadan slayt başlıkları, maddeleri ve anlatımdan temel Markdown/Anki çıktısı oluşturmalıdır. Kullanıcı isterse daha sonra LLM ile iyileştirilebilir.

### 7. Snapshot ve undo

Snapshot alınması gereken işlemler:

- Slayt silme veya toplu silme
- Regenerate sonucunu kabul etme
- Büyük sıra değişikliği
- Checkpoint reset
- Orphan dosya temizliği

Kabul kriterleri:

- Son en az 10 `script.json` sürümü saklanır.
- UI tarih ve slayt sayısıyla sürümleri listeler.
- Geri yükleme atomiktir.
- Ağır MP3/MP4 dosyaları kopyalanmaz; manifest/cache referansları kullanılır.

### 8. Bölüm bazlı MP4/MP3 çıktısı

Uzun bir tek video yanında:

- bölüm başına MP4,
- bölüm başına MP3,
- birleşik ders MP4/MP3,
- YouTube chapter zaman damgaları

üretilebilir.

Bir bölüm başarısız olduğunda diğer bölüm çıktıları korunmalıdır. Tek bölüm yeniden üretildiğinde tüm dersin TTS/renderı tekrarlanmamalıdır.

### 9. Telaffuz sözlüğü editörü

`app/tts/pronunciation.py` üzerine proje/global sözlük arayüzü eklenebilir:

- Teknik terim → okunacak biçim
- Kısaltma, URL ve İngilizce terim normalizasyonu
- Yalnız tek cümle için hızlı ses önizlemesi
- Noktalama/duraklama kontrolü

Sözlük değişince yalnız etkilenen slaytların `narrationHash` değeri değişmelidir.

## P2/P3 — Daha sonra değerlendirilecekler

### 10. Yakın tekrar ve kapsam analizi

Ücretsiz başlangıç için normalize kelime kümeleri, Jaccard veya TF-IDF/cosine kullanılabilir. Sonuçlar otomatik silinmemeli, `muhtemel tekrar` olarak gösterilmelidir. Büyük projede bütün slaytları O(n²) karşılaştırmaktan kaçınılmalıdır.

### 11. Gelişmiş altyazı editörü

- Kelime/cümle zaman çizelgesi
- Slayt başına altyazı aç/kapat
- Satır kırma önizlemesi
- Konuşma ve görüntü senkron düzeltmesi
- SRT/ASS import/export

Bu özellik yüksek geliştirme maliyetlidir; temel resume tamamlandıktan sonra yapılmalıdır.

### 12. Yerel semantik arama/RAG

- Kaynak ve transkriptte arama
- `Bu kavram hangi bölümlerde?`
- Kaynak referanslı soru-cevap
- Yerel embedding modeli seçeneği

Embedding cache'i bölüm hash'ine bağlı olmalıdır. Bulut embedding varsayılan olmamalıdır.

### 13. Otomatik görseller

Önce ücretsiz seçenekler:

- Kaynaktan görsel/şema çıkarma
- Koddan deterministik diyagram
- İkon ve şekil presetleri
- Basit SVG akış şemaları

AI görsel üretimi isteğe bağlı olmalı ve tahmini maliyeti önceden göstermelidir. Telif ve kaynak bilgisi saklanmalıdır.

### 14. Podcast/çok konuşmacı modu

- Öğretmen–öğrenci diyalogu
- Birden fazla TTS sesi
- Konuşmacı bazlı ses cache'i
- Video olmadan hızlı ses çıktısı

Bu mod LLM/TTS maliyetini artırabileceğinden varsayılan akışa eklenmemelidir.

## UI/UX yol haritası

### Gerçek pipeline durumu

```text
Kaynak ✓ → Kapsam ✓ → Anlatı 44/71 → İnceleme 3 hata → Ses 112/148 → Video 90/148
```

Her aşamaya tıklanınca yalnız ilgili sorunlar ve eylemler gösterilmelidir.

### Büyük listeler

300+ bölüm ve 500+ slayt için:

- Liste virtualization
- Arama ve filtre
- `Yalnız sorunlular`
- `Tamamlananları gizle`
- `Yalnız seçilenler`
- Drag/drop sırasında sınırlı yeniden render

### Editör

- Kaydedilmemiş değişiklik rozeti
- Güvenli debounced draft ve belirgin kayıt durumu
- Kaydet/önceki/sonraki/sil/taşı klavye kısayolları
- Kelime, karakter ve tahmini süre sayacı
- Kalite uyarısından ilgili alana odaklanma
- Eski ve regenerate sonucunu yan yana karşılaştırma

### Erişilebilirlik ve düşük kaynak modu

- Tam klavye navigasyonu
- Görünür focus stilleri
- Durumu yalnız renkle anlatmama
- `prefers-reduced-motion` desteğini koruma
- Three.js olmadan tam işlev
- WebGL context lost olduğunda CSS fallback
- Düşük güç modunda animasyonları ve yüksek çözünürlüklü preview'i kapatma

## Teknik mimari önerileri

### Pydantic API modelleri

`dict = Body(...)` uçları aşamalı olarak şu tiplere taşınabilir:

- `GenerateRequest`
- `RenderRequest`
- `SlideUpdateRequest`
- `QualityReport`
- `AssetManifest`
- `JobStatus`

Tüm API bir defada yeniden yazılmamalıdır. Önce generate/render gibi yüksek riskli uçlar tiplendirilmelidir.

### Job durum makinesi

```text
queued → running → complete
queued → cancelled
running → failed
running → interrupted
interrupted → queued (resume)
failed → queued (retry)
```

- Aynı projede iki ağır render varsayılan olarak engellenmelidir.
- Script generate ile render çakışıyorsa açık hata verilmelidir.
- Cancel eklenirse alt süreç güvenle kapatılmalıdır.
- Job JSON dosyaları için retention/prune eklenmelidir.

### Atomik dosya ve proje kilidi

- JSON aynı klasörde `.tmp` yazılıp `replace` edilmelidir.
- Proje başına yazma kilidi değerlendirilmelidir.
- `script.json`, checkpoint ve quality raporu ortak fingerprint/sürüm bilgisi taşımalıdır.
- Bozuk JSON sessizce ezilmemeli; `.corrupt-<timestamp>` olarak karantinaya alınmalıdır.

### Şema sürümleme

`script.json`, checkpoint, manifest ve proje metadata'sında `schemaVersion` olmalıdır. Eski projeler yedek alındıktan sonra migrate edilmeli; migration fixture testleri yazılmalıdır.

### İçerik adresli cache

Uzun vadede:

```text
cache/audio/<narration_hash>.mp3
cache/images/<image_hash>.png
cache/segments/<segment_hash>.mp4
```

Windows symlink izinleri nedeniyle ilk sürüm hardlink/copy veya doğrudan cache path kullanabilir. Temizlik yalnız referans ve yaş bilgisiyle yapılmalıdır.

## Performans ve kaynak yönetimi

### RAM/VRAM

- Coqui modeli ana FastAPI prosesine taşınmamalıdır.
- Worker bitince proses kapanmalı ve VRAM serbest kalmalıdır.
- Varsayılan aynı anda bir ağır TTS worker olmalıdır.
- Büyük ses dosyaları tamamen RAM'e alınmamalıdır.

### Disk

- Render öncesi boş alan kontrol edilmelidir.
- Ağır cache için `D:` ilkesi korunmalıdır.
- `_orphaned` kurtarılabilir kalmalı fakat kullanıcı onaylı temizlik sunulmalıdır.
- Kaynak, script, checkpoint ve son çıktı otomatik silinmemelidir.

### CPU/GPU ve WebGL

- Sekme görünür değilken Three.js animasyonu durmalıdır.
- WebGL kaybında otomatik CSS fallback kullanılmalıdır.
- Video render concurrency sınırlandırılmalıdır.
- Preview düşük çözünürlüklü hızlı modda üretilebilir; final etkilenmemelidir.

## Güvenlik ve gizlilik

- Anahtarlar bootstrap cevabı, job JSON, log veya export paketine girmemelidir.
- UI yalnız `configured: true/false` göstermelidir.
- Endpoint yalnız `http/https` kabul etmelidir; yerel `127.0.0.1` desteklenebilir.
- Origin/host ve path traversal kontrolleri korunmalıdır.
- Kaynağın yerel mi bulut sağlayıcıya mı gideceği açık gösterilmelidir.
- Telemetry varsayılan kapalı olmalıdır.
- Proje dışa aktarımında `.env` ve gizli ayarlar kesinlikle bulunmamalıdır.

## Hata gözlemlenebilirliği

Kullanıcı yalnız `ERR_CONNECTION_RESET` veya `ECONNREFUSED` görmek yerine neden ve çözüm görmelidir.

Önerilen hata sınıfları:

- `provider_timeout`
- `provider_quota`
- `provider_invalid_response`
- `checkpoint_conflict`
- `disk_full`
- `worker_crash`
- `missing_dependency`
- `asset_corrupt`
- `webgl_context_lost`

Her hata kullanıcıya bir sonraki eylemi önermelidir. Örneğin quota hatası `Kaldığı yerden devam et`, disk dolu hatası boş alan/cache yolu göstermelidir.

Proje bazlı küçük `events.jsonl` düşünülebilir. Kaynak metni ve API anahtarı loglanmamalıdır. Tanılama paketi gizli değerleri maskeleyerek sürümleri, manifesti ve son hataları içerebilir.

## Test stratejisi

### Birim

- Katmanlı hash geçersizleştirme matrisi
- Quality gate sınır değerleri
- Checkpoint migration/pending commit
- Job geçişleri ve retention
- Orphan audit/karantina
- Maliyet/süre tahminleri
- Kaynak kimliği/regenerate eşlemesi

### Entegrasyon

- Fake LLM ile iki chunk üret; ikincide kes; resume yalnız eksik chunk'ı çalıştırsın.
- Fake TTS ile beş slayt üret; üçüncüde hata; ikinci render ilk iki sesi üretmesin.
- Slayt sırası değişince ses/segment eşleşmesi doğru kalsın.
- Tema değişince MP3 hash/mtime değişmesin, segment değişsin.
- Boş anlatımda worker başlamadan 409 dönsün.
- API restart sonrası job/checkpoint tutarlı olsun.

### UI/E2E

- Kaynak seçimi görünür biçimde seçili kalsın.
- Progress gerçek job ilerlemesini göstersin.
- Resume tamamlanmış bölüm sayısını doğru göstersin.
- Kalite uyarısı doğru slaytı açsın.
- Silme/sıralama refresh sonrası korunsun.
- WebGL kapalıyken UI çalışsın.
- Backend kapalıyken açıklayıcı hata ve yeniden dene olsun.

### Büyük proje fixture'ı

Ücretli servis kullanmadan 100 bölüm, 300–500 slayt, fake MP3/segment ve birkaç bozuk asset üret. Proje açılışı, kalite analizi, audit, DOM performansı ve maksimum RAM ölçülmelidir.

## Bilinen teknik borç ve riskler

1. `requirements.txt` farkı incelenmeden commitlenmemelidir; encoding bozulması olabilir.
2. `studio_web/api.py` içindeki eski kullanılmayan `JobStore` temizlenebilir.
3. Persistent job dosyaları için retention yoktur.
4. Manifest final concat sonrası yazıldığı için yarım render ara durumunu tam göstermeyebilir.
5. Quality gate kaynak doğruluğu/hallüsinasyon garantisi vermez.
6. Yakın anlamlı tekrar algısı henüz yoktur.
7. Sıra numaralı asset yapısı büyük sıra değişikliklerinde cache kaybına neden olabilir.
8. Son tarayıcı smoke testi sandbox nedeniyle tamamlanamadı.
9. FastAPI TestClient/httpx deprecation uyarısı vardır.
10. Thread job restart sonrası devam etmez; yalnız güvenli kesinti durumu ve generation checkpoint vardır.

## Yapılmaması gerekenler

- Pipeline'ı bir defada baştan yazma.
- Migration olmadan checkpoint/manifest formatını kırma.
- Büyük projeyi varsayılan tek LLM isteğine gönderme.
- Her chunk için bağlamsız Claude session açma.
- Kullanıcı istemeden ücretli LLM/TTS çağrısı yapma.
- Kalite uyarılarıyla içeriği otomatik silme/değiştirme.
- Orphan dosyaları kullanıcı görmeden kalıcı silme.
- Coqui'yi ana API prosesine taşıma.
- Ağır cache/modeli `C:` kullanıcı profiline indirme.
- Three.js'i temel işlev için zorunlu yapma.
- Anahtarları commit/log/export etme.
- Hata sonrası tamamlanmış checkpoint/cache'i topluca sıfırlama.

## Her özellik paketi için çalışma yöntemi

1. İlgili kodu ve mevcut testleri oku.
2. En küçük veri modeli/API değişikliğini tasarla.
3. Kullanıcı verisini koruyan migration/fallback ekle.
4. Hedefli birim testi yaz.
5. Backend değişikliğini uygula.
6. UI gerekiyorsa mevcut tasarım sistemine uyumlu ekle.
7. Hedefli testleri çalıştır.
8. Tüm Python regresyon testlerini çalıştır.
9. React production build çalıştır.
10. Fake/ücretsiz verili smoke test yap.
11. `git diff --check` ve `git status` incele.
12. Kullanıcıya sonucu, maliyet etkisini ve deneme yolunu anlat.

## Bir sonraki agent için kesin başlangıç görevi

Önce kalite/job/asset paketini gerçek uygulamada smoke test et. Ardından yalnız şu küçük dilimi uygula:

> `asset_manifest.json` ve API audit verisinden hazır/eksik MP3 ile segment sayılarını doğru hesapla; proje sağlık kartında salt okunur göster. Var olan pipeline cache davranışını bozma ve henüz otomatik retry/concurrency ekleme.

Kabul sonrası katmanlı hash ve gerçek TTS/video resume işine geçilebilir.
