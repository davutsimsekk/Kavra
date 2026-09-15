# Ders Stüdyosu

MD / PPTX / PDF dosyasını, gerçek bir hocanın tahtada anlattığı gibi **sesli ve görüntülü ders videosuna** çeviren, tamamen yerel (D: sürücüsünde) çalışan bir araç.

## Neden bu şekilde tasarlandı

- **C: sürücüsünde neredeyse hiç boş alan yok** (kurulum sırasında birkaç yüz MB'a kadar düştü). Bu yüzden Python sanal ortamı, tüm pip/torch/huggingface önbellekleri, indirilen ses modelleri ve geçici dosyalar **D:\proje\ders_video** altında tutulur. `run_gui.bat` ve `d_env.sh` bu ortam değişkenlerini otomatik ayarlar — elle bir şey yapmana gerek yok, ama kendi terminalinden bir şey çalıştırırsan önce `source d_env.sh` (bash) çalıştır.
- Ham metni doğrudan seslendirmek yerine önce bir **LLM ile "ders anlatım script'ine"** çevrilir (kod/tablo/markdown sembollerini olduğu gibi okumak yerine kavramsal, doğal bir anlatım üretir).
- Not: Kurulum sırasında iki kütüphane (huggingface `xet` önbelleği ve coqui-tts'in kendi model önbelleği) `HF_HOME`'u yok sayıp varsayılan olarak C'ye yazmaya çalıştı; ikisi de artık `HF_HUB_DISABLE_XET` ve `TTS_HOME` ile D'ye sabitlendi (bkz. `d_env.sh` / `app/config.py`). Yeni bir kütüphane eklersen aynı riske dikkat et: bazı kütüphaneler `HF_HOME`/`XDG_CACHE_HOME` dışında kendi env değişkenini kullanır.

## Kurulum durumu (bu oturumda tamamlandı)

- `venv/` — Python sanal ortamı (D: üzerinde)
- Kurulu: Pillow, edge-tts, python-pptx, pymupdf, pygments, python-dotenv, requests, onnxruntime, piper-tts, google-genai, FastAPI ve Uvicorn
- Modern arayüz: React 19 + Vite + Three.js; production build `webui/dist/` altında hazırdır.
- `models/piper/dfki/` — offline Türkçe Piper ses modeli (indirildi, hazır)
- Coqui XTTS v2 (opsiyonel, offline + ses klonlama): `install_coqui.py` ile kuruldu, bağımlılık çakışmaları çözüldü, model indirildi (D:'de, ~1.9GB, `_cache/tts_home`). **Ancak** bu makinede modeli belleğe yüklerken (RAM ~15GB, o an ~4-5GB boştu) iki kez "sistem bellek yetersiz" nedeniyle kesildi — muhtemelen XTTS v2'nin yüklenmesi ~3-5GB boş RAM istiyor. Diğer ağır programları (tarayıcı, IDE) kapatıp `venv\Scripts\python.exe test_coqui_voice.py` ile tekrar dene. Çalışırsa GUI'de "coqui" sağlayıcısı doğrudan kullanılabilir; çalışmazsa Edge-TTS veya Piper'la devam et, ikisi de zaten tam kalitede çalışıyor.

## Nasıl çalıştırılır

Önerilen modern **masaüstü uygulaması** için çift tıkla: **`run_gui.bat`**. React arayüzü, adres çubuğu ve tarayıcı sekmeleri olmayan ayrı bir Microsoft Edge/Google Chrome uygulama penceresinde açılır. Yerel servis yalnızca `127.0.0.1:8765` üzerinde çalışır; masaüstü ve web aynı API'yi ve aynı özellikleri kullanır.

Normal tarayıcı sekmesinde açmak için **`run_web.bat`**, eski Tkinter arayüzüne dönmek için **`run_legacy_gui.bat`** kullanılabilir.

Ya da terminalden:
```
source d_env.sh   # (PowerShell kullanıyorsan aşağıdaki env değişkenlerini elle set et)
venv/Scripts/python.exe gui/app_gui.py
```

### Adım adım kullanım

1. **1. Kaynak** sekmesi: `.md` / `.pptx` / `.pdf` dosyanı seç → **İçeriği Ayır**. Bölümler işaretli bir tabloda görünür; bir satıra tıklayarak açıp kapatabilir, **Tümünü Seç / Seçimi Temizle** düğmelerini ve görünür seçili bölüm sayacını kullanabilirsin. Script üretimi yalnızca açıkça işaretli bölümleri kullanır.
2. **2. Anlatım Metni** sekmesi: Dört yol var:
   - **Gemini API (otomatik):** `aistudio.google.com/apikey` adresinden ücretsiz bir anahtar al, yapıştır, **Kaydet**, model seç (varsayılan `gemini-3.5-flash-lite`). Sonra **Script Üret**. 429 (rate limit) alırsan otomatik üstel bekleme ile tekrar dener; kota gerçekten 0 ise (proje askıya alınmışsa) bunu hemen anlaşılır bir mesajla bildirir.
   - **OpenAI uyumlu API:** Resmî OpenAI için varsayılan endpoint'i bırak (`https://api.openai.com/v1/chat/completions`), API anahtarını ve model adını gir. OpenRouter, LM Studio, Ollama veya başka bir OpenAI-uyumlu servis kullanıyorsan endpoint/base URL ile o servisteki model kimliğini yazabilirsin. Base URL girilirse `/chat/completions` otomatik eklenir; yerel ve anahtarsız bir sunucuda API key boş bırakılabilir. Anahtar `.env` içinde, endpoint ve model ise `settings.json` içinde saklanır. OpenRouter için uzun Türkçe derste kalite/fiyat dengesi doğrulanan `openai/gpt-5.6-luna` seçilidir; OpenRouter çağrılarında geçersiz/yarım JSON riskini azaltan katı şema ve aşırı yavaş sağlayıcıları önleyen throughput yönlendirmesi kullanılır.
   - **Agent CLI (önerilen, kota derdi yok):** Bilgisayarında kurulu bir CLI ajanı (Claude Code, Gemini CLI, vb.) varsa, API anahtarına gerek kalmadan onu arka planda çağırır. Claude Code kullanıldığında parçalar varsayılan olarak aynı mantıksal Claude oturumunda (`--session-id` / `--resume`) işlenir; böylece önceki parçaların terminolojisi ve akışı korunur. Güvenilirlik için varsayılan parça boyutu 4 bölüm, timeout 900 saniyedir; ikisi de arayüzden değiştirilebilir. Komut kutusunu de düzenleyebilirsin (ör. `claude -p --model haiku --output-format json --restricted`).
   - **Manuel (herhangi bir LLM ile):** **Prompt'u Panoya Kopyala** → istediğin bir sohbet arayüzüne (Gemini web, ChatGPT, Claude...) yapıştır → dönen JSON'u bir dosyaya kaydet → **Manuel JSON Dosyası Yükle**.
   - Otomatik sağlayıcılarda **Tek istekte gönder** kutusunu işaretlersen seçili kaynak bölümlerinin tamamı tek LLM çağrısında işlenir. Tek çağrıda `session/resume` gerekmediği için bu ayarlar otomatik olarak devre dışı kalır. İşaretlemezsen **bir LLM çağrısındaki kaynak bölümü** ayarı, PDF/PPT'den aynı çağrıya en fazla kaç bölüm konacağını belirler; bu üretilen slayt sayısı değildir. Claude session seçeneği açıksa bu ayrı çağrıların tamamı `--resume` ile aynı mantıksal konuşmada devam eder.
   - Gemini ve OpenAI uyumlu API çağrıları sunucu tarafında oturum tutmaz. Uygulama bu nedenle her parçaya önceki slayt başlıkları ile son slaytların kısa özetinden oluşan kompakt bir **ders hafızası** ekler. Daha önce elle düzenlediğin veya kaydettiğin slaytlar da bu hafızaya dahildir; böylece API parçaları terminoloji ve akış bakımından birbirinden kopmaz.
   - Üretilen slaytları düzenleyebilir, silebilir, yenisini seçili slayttan sonra ekleyebilir ve **Yukarı / Aşağı** düğmeleriyle sıralayabilirsin. Yeni üretimi listenin sonuna veya seçili slayttan hemen sonraya yerleştirebilirsin. İlerleme çubuğu o anki parçayı gösterir; başarıyla biten her parça `script.json` dosyasına kaydedilir. Elle yapılan düzenlemeler Kaydet ile diske yazılır.
   - **Kaldığı yerden devam et** açıkken her başarılı LLM çağrısının kaynak parmak izleri `generation_checkpoint.json` dosyasına yazılır. Kota/timeout/uygulama kapanması sonrası aynı bölümler yeniden gönderilmez; yalnızca kalanlar üretilir. Claude session kimliği de aynı komut için saklanır. Kaynak metni değişirse parmak izi değiştiği için o bölüm otomatik olarak yeniden bekleyen duruma döner. Bilerek tekrar üretmek için resume'u kapatabilir veya yalnızca ilerleme işaretlerini sıfırlayabilirsin; mevcut slaytlar silinmez.
   - **Script'i Kaydet** ile `projects/<proje>/script.json` dosyasına yazılır (istersen elle de düzenleyebilirsin, düz JSON).
3. **3. Ses ve Video** sekmesi: TTS sağlayıcısını ve sesi seç; görsel tema, altyazı, geçiş ve yakınlaştırma seçeneklerini ayarla; **Videoyu Oluştur**. **Temayı Önizle** ile video üretmeden önce seçili slaydın görünümünü kontrol edebilirsin. Bitince **Videoyu Oynat** veya **Çıktı Klasörünü Aç**.

Her slayt için üretilen ses/görüntü/segment `projects/<proje>/assets/` altında saklanır ve **içerik değişmediği sürece tekrar üretilmez** (hash tabanlı önbellek) — yani bir slaytın metnini düzeltip videoyu tekrar oluşturduğunda sadece o slayt yeniden render edilir.

### Modern React arayüzü

- Üç aşamalı düzen: **Kaynak → Anlatı → Stüdyo**
- Dosya sürükle-bırak veya yerel dosya yolu, görünür bölüm seçimleri ve proje geçmişi
- Agent/Gemini/OpenAI ayarları, ders hafızası ve canlı iş ilerlemesi
- Sürükle-bırak slayt sıralama, araya ekleme, silme ve ayrıntılı master-detail editör
- Yedi tema, gerçek render önizlemesi, TTS/effect ayarları ve video oynatıcı
- Açık / koyu / sistem teması; görünüm tercihi cihazda hatırlanır. WebGL ve 3D arka plan kullanılmaz.
- Telefon, tablet ve masaüstüne uyarlanan proje kitaplığı; proje, kaynak bölümü, slayt ve deste araması.
- Anlatı ayarları ve PDF seçenekleri açılır panellerde; düzenleyici anlatım metnine öncelik verir.
- Slayt düzenlemeleri Kaydet veya Ctrl+S ile diske yazılır. Kaydedilmemiş taslaklar tarayıcıda korunur; hatalı kayıt başarılı gösterilmez.
- API anahtarları tarayıcıya geri gönderilmez; yalnızca yapılandırılmış olup olmadıkları gösterilir.
- `.env` Git tarafından yok sayılır. Kayıtlı bir uzak API anahtarı localhost LLM endpoint'lerine otomatik olarak gönderilmez.
- `run_gui.bat`, bu arayüzü ayrı bir masaüstü uygulama penceresinde açar; resume, OpenRouter, slayt editörü, tema önizlemesi ve izole TTS/render davranışı web ile masaüstünde birebir aynıdır.

Frontend geliştirme modu için `run_web_dev.bat`; production build yenilemek için `cd webui && npm run build` kullanılabilir.

## Ses (TTS) sağlayıcıları — karşılaştırma

| Sağlayıcı | Kalite | Çevrimiçi mi | Not |
|---|---|---|---|
| **edge** (önerilen, varsayılan) | Yüksek, çok doğal | Kısa bir internet isteği gerekir | Ücretsiz, API key yok. Sesler: `tr-TR-AhmetNeural`, `tr-TR-EmelNeural` |
| **piper** | Orta | Tamamen offline | `models/piper/dfki` sesi hazır kurulu. Kelime zamanlaması yok → altyazı süresi tahmini olarak hesaplanır |
| **elevenlabs** | En doğal | Bulut, API key gerekir | Ücretsiz kota çok sınırlı (~10 dk/ay). `elevenlabs.io`'dan key al |
| **coqui** (XTTS v2) | Yüksek + ses klonlama | Tamamen offline (ilk indirme hariç) | GPU varsa (bu makinede RTX 4060) **otomatik kullanılır** ve CPU'ya göre **~3.8x daha hızlı** (11s vs 41s, ölçüldü) — ayrıca CPU'da yaşanan "bellek yetersiz" çökmesi GPU'da hiç olmuyor. **CPML lisansı: kişisel/akademik kullanım serbest, ticari kullanım ayrı lisans ister** (coqui.ai/cpml) |

**GPU notu:** `CoquiTTSProvider`, `torch.cuda.is_available()` ile otomatik GPU algılar; elle `CoquiTTSProvider(gpu=False)` diyerek CPU'ya zorlayabilirsin. `install_coqui.py` de artık `nvidia-smi` ile GPU'yu otomatik tespit edip uygun (CUDA veya CPU) torch sürümünü kuruyor.

**Türkçe TTS manzarası (2026-09 araştırması):** 2026'nın trend açık kaynak modelleri (Kokoro-82M, Chatterbox, Zonos, CosyVoice) esas olarak İngilizce odaklı, Türkçe desteği zayıf/yok. Türkçe için gerçekçi en iyi offline seçenekler hâlâ **XTTS v2** (en kaliteli + ses klonlama, ağır) ve **Piper** (hafif, hızlı, orta kalite) — ikisi de zaten kurulu. **Meta'nın MMS-TTS-tur** modeli (VITS mimarili, tek konuşmacı) daha hafif bir orta-yol alternatifi olabilir ama henüz entegre edilmedi; istersen ekleyebiliriz.

İstediğin an sağlayıcı değiştirip aynı script ile farklı bir ses deneyebilirsin.

## Video özellikleri

- Modern slayt tasarımı: kart tabanlı içerik, başlık, numaralı maddeler, breadcrumb, slayt sayacı ve ilerleme çubuğu
- **Otomatik tema:** bölüm slaytlarında Aurora, kod slaytlarında Gece Mavisi; normal slaytlarda dengeli açık temalar otomatik seçilir
- Hazır presetler: **Beyaz Minimal**, **Notebook Açık**, **Gece Mavisi**, **Sıcak Kağıt**, **Mint Akademik**, **Aurora**
- Her temada uyumlu gradyan/dekorasyon, metin kontrastı, kart yüzeyleri ve kod renklendirme stili
- Kod blokları **Pygments ile sözdizimi renklendirmeli** gösterilir
- Bölüm-arası büyük başlık slaytları (`level: "chapter"`)
- Geçişlerde yumuşak **fade in/out** (video + ses)
- Opsiyonel **Ken Burns** (hafif yakınlaştırma) efekti
- Konuşmayla senkronize **gömülü altyazı** (Edge-TTS'in kelime zamanlaması kullanılır; diğer sağlayıcılarda karakter sayısına göre tahmin edilir). Kod içeren slaytlarda altyazı, kod kutusuyla çakışmaması için otomatik kapanır.

## İngilizce/kod terimi telaffuz düzeltmesi

Türkçe TTS motorları İngilizce kelimeleri (if, else, switch, pointer, struct...) Türkçe harf okuma kurallarıyla okuyunca kulağa çok kötü geliyor (ör. Türkçe'de "c" harfi /dʒ/ okunur, bu yüzden "case" veya "const" yanlış seslendirilir). `app/tts/pronunciation.py` içindeki bir sözlükle, SADECE seslendirmeye giden metinde bu kelimeler "kulağa yakın" fonetik Türkçe yazımla değiştiriliyor (ör. "switch" → "sviç", "break" → "breyk"); ekrandaki slayt/kod/altyazı metni her zaman orijinal (doğru) yazımla kalıyor. Bu liste kesin değil — bir kelime hâlâ kötü çıkıyorsa bana söyle, sözlüğe ekleyip düzeltirim (sesi kendim dinleyemediğim için senin geri bildirimin gerekiyor).

## Bilinen sınırlamalar

- **137 bölümlük dev bir rehberi tek seferde işlemek** hem LLM kotasını hem render süresini zorlar — bölüm bölüm (ör. önce "Bölüm 1: C Programlama Dili") ilerlemen önerilir.
- PDF metin çıkarma, kaynağın fontuna bağlıdır; taranmış (image) PDF'lerde OCR yoktur, metin çıkmaz.
- Piper/ElevenLabs/Coqui'de altyazı zamanlaması tahminidir (Edge-TTS kadar hassas değildir).
- Coqui CPU'da yavaştır; büyük bir dersi Coqui ile üretmek saatler sürebilir.

## Klasör yapısı

```
ders_video/
  app/                  çekirdek Python paketi (parser, LLM, TTS, video, pipeline)
  studio_web/desktop.py modern React masaüstü pencere başlatıcısı
  gui/app_gui.py        eski/legacy Tkinter arayüzü
  prompts/              LLM'e verilen "ders script'i" prompt şablonu
  models/piper/         offline Türkçe ses modeli
  projects/<ad>/        her kaynak dosya için: ham bölümler, script.json, assets/, ders.mp4/mp3
  venv/                 Python sanal ortamı (D:)
  _cache/               pip/hf/torch/temp önbellekleri (D:, C:'ye asla yazmaz)
  install_coqui.py      opsiyonel Coqui XTTS v2 kurulumu
  run_gui.bat           modern masaüstü uygulamasını çift tıkla başlat
  run_legacy_gui.bat    eski Tkinter arayüzünü başlat
```

## Gelişmiş flashcard çalışma masası

Flashcard bölümüne günlük limitler, dakika bazlı öğrenme, kalıcı geri al, çoklu boşluk/ters kart, etiketli kart tarayıcısı, istatistikler ve TSV içe aktarma eklendi. Kullanım, kısayollar ve Anki ile farklar için [FLASHCARD_GUIDE.md](FLASHCARD_GUIDE.md) dosyasına bak.
