# Kavra'yı başka bir bilgisayarda çalıştırma

Bu paket; React arayüzünü, Python API'yi, FFmpeg'i, Linux fontlarını, Coqui
XTTS v2'yi, Anka TTS'yi ve ayrı Chatterbox ortamını tek Docker imajında kurar.
Proje, model ve önbellek dosyaları imajın dışında kalır; container silinse bile
veriler kaybolmaz.

## 1. Gereksinimler

- Docker Desktop veya Docker Engine + Docker Compose v2
- GPU modu için NVIDIA ekran kartı, güncel NVIDIA sürücüsü ve Linux'ta NVIDIA
  Container Toolkit
- İlk imaj kurulumu ve ilk TTS model yüklemeleri için internet
- Ağır TTS motorları nedeniyle yeterli disk alanı (imaj + modeller için en az
  20 GB boş alan önerilir)

Resmî GPU kurulum kaynakları:

- Docker Compose GPU: https://docs.docker.com/compose/how-tos/gpu-support/
- NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html

## 2. Başlatma

NVIDIA GPU ile (Chatterbox/Coqui/Anka için önerilen):

```bash
docker compose -f compose.yaml -f compose.gpu.yaml up --build
```

GPU vermeden (Edge-TTS, Piper ve genel arayüz için):

```bash
docker compose up --build
```

Arayüz: http://127.0.0.1:8768

İlk build uzun sürebilir; CUDA Torch ve TTS bağımlılıkları indirilir. Coqui,
Anka ve Chatterbox model ağırlıkları ise ilgili motor ilk kullanıldığında kalıcı
`_cache/` klasörüne indirilir.

Arka planda çalıştırmak için komuta `-d` ekleyin. Durdurmak için:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml down
```

`down` verileri silmez. `-v` kullanmayın; bu projede bind mount kullanılsa da
alışkanlık olarak veri silme seçeneklerini çalıştırmamak daha güvenlidir.

## 3. GPU doğrulama

Container başladıktan sonra:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml exec kavra python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

Çıktıda `True` ve ekran kartı adı görünmelidir. Linux'ta görünmüyorsa NVIDIA
Container Toolkit kurulumunu kontrol edin. Windows'ta Docker Desktop'ın WSL2
backend'i ve güncel NVIDIA Windows sürücüsü kullanılmalıdır.

## 4. Kalıcı klasörler

| Bilgisayardaki klasör | Container yolu | İçerik |
|---|---|---|
| `projects/` | `/data/projects` | Kaynaklar, slaytlar, sesler ve videolar |
| `models/` | `/data/models` | Klon referansları ve Piper modeli |
| `_cache/` | `/data/_cache` | Hugging Face, Coqui, Torch ve geçici render cache'i |
| `study_data/` | `/data/study_data` | Çalışma takibi veritabanı |
| `docker-data/` | `/data/runtime` | Docker ayarları ve kaydedilen API anahtarları |

Başka bilgisayara taşımak için uygulama klasörünü bu beş klasörle birlikte
kopyalayın. `venv/`, `chatterbox_venv/`, `webui/node_modules/` ve
`webui/dist/` klasörlerini taşımanız gerekmez; Docker bunları yeniden üretir.

Hugging Face cache'i işletim sistemleri arasında kopyalanırken sorun çıkarırsa
yalnız `_cache/` klasörünü boş bırakın; modeller ilk kullanımda yeniden iner.
`projects/`, `models/`, `study_data/` ve `docker-data/` kullanıcı verisidir.

## 5. API anahtarları ve ayarlar

`.env.example` dosyasını `.env` adıyla kopyalayıp anahtarları doldurabilirsiniz.
Alternatif olarak anahtarları arayüzden kaydedebilirsiniz; Docker içinden
girilen değerler `docker-data/.env` dosyasında kalıcı tutulur. `.env` ve
`docker-data/` Git'e eklenmez.

Portu değiştirmek için `.env` içinde örneğin `KAVRA_PORT=8877` kullanın.

## 6. Docker kullanmadan kurulum

Python 3.14, Node.js 22 ve FFmpeg kurulu olmalıdır.

```bash
python -m venv venv
```

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-tts.txt
python install_chatterbox.py
npm --prefix webui ci
npm --prefix webui run build
python -m uvicorn studio_web.api:app --host 127.0.0.1 --port 8768
```

Linux/macOS kabuğu:

```bash
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-tts.txt
python install_chatterbox.py
npm --prefix webui ci
npm --prefix webui run build
python -m uvicorn studio_web.api:app --host 127.0.0.1 --port 8768
```

Sonraki çalıştırmalarda Linux/macOS veya Git Bash üzerinden `bash run_web.sh`
komutu ortam ve frontend build kontrolünü otomatik yapar.

NVIDIA/CUDA olmayan macOS sistemlerinde `requirements-tts.txt` içindeki CUDA
Torch kurulumu uygun değildir. Böyle bir makinede yalnız `requirements.txt`
ile Edge-TTS/Piper kullanın.

## 7. Güncelleme ve yedekleme

Kod güncellendikten sonra imajı tekrar oluşturun:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml build --pull
docker compose -f compose.yaml -f compose.gpu.yaml up -d
```

Yedek için container'ı durdurup `projects/`, `models/`, `study_data/` ve
`docker-data/` klasörlerini kopyalamak yeterlidir. `_cache/` yeniden
indirilebildiği için zorunlu değildir.

## 8. Sunucuda çalıştırma (yalnızca kendin, Tailscale üzerinden)

> **Uyarı:** Kavra'nın API'sinin girişi/şifresi yoktur. Bağlanabilen herkes
> sunucuda komut çalıştırabilir (Agent CLI sağlayıcısı) ve sunucudaki dosyaları
> kaynak olarak okutabilir. Bu yüzden port internete **asla** açılmamalıdır.
> Erişimi ağ katmanı kısıtlar; aşağıdaki kurulum bunu Tailscale ile sağlar.

Kurallar:

- `compose.yaml` portu yalnız `127.0.0.1`'e bağlar. Bunu `0.0.0.0` yapma.
  (Linux'ta Docker'ın yayımladığı portlar `ufw` kurallarını atlar.)
- `tailscale funnel` **kullanma**; funnel siteyi herkese açar. Yalnız
  `tailscale serve` kullan (yalnızca tailnet'indeki cihazlar).
- Tailnet'ine yalnızca kendi cihazlarını ekle.

### Adımlar

1. Sunucuya Docker Engine + Compose ve Tailscale kur, `sudo tailscale up` ile
   tailnet'e katıl.
2. Tailscale yönetim panelinde (DNS sayfası) **MagicDNS** ve **HTTPS
   Certificates** özelliklerini aç.
3. Sunucunun tam adını öğren: `<makine>.<tailnet-adi>.ts.net`
   (`tailscale status` veya yönetim paneli).
4. Proje klasöründe `.env` dosyasına bu adı yaz:

   ```bash
   KAVRA_ALLOWED_HOSTS=makine.tailnet-adi.ts.net
   ```

   `KAVRA_ALLOWED_ORIGINS` verilmezse aynı adın `https://` ve `http://`
   sürümleri türetilir. Joker (`*`) kabul edilmez; hatalı değer uygulamayı
   açılışta hata vererek durdurur.
5. Uygulamayı başlat (sunucuda GPU yoksa `compose.gpu.yaml` ekleme):

   ```bash
   docker compose up -d --build
   ```
6. Tailscale'e yerel portu HTTPS olarak yayımlat (Tailscale 1.52 veya üstü):

   ```bash
   sudo tailscale serve --bg 8768
   tailscale serve status
   ```
7. Tailnet'indeki bir cihazdan `https://makine.tailnet-adi.ts.net` adresini aç.

### Sorun giderme

| Belirti | Neden |
|---|---|
| `Invalid host header` (400) | Adres `KAVRA_ALLOWED_HOSTS` içinde değil; `.env`'i düzeltip `docker compose up -d` çalıştır. |
| "yalnızca Kavra arayüzünden kullanılabilir" (403) | Tarayıcının adresi izinli origin listesinde yok; `KAVRA_ALLOWED_ORIGINS`'i kontrol et. |
| Sayfa açılmıyor | `docker compose ps` sağlıklı mı, `tailscale serve status` yönlendirme gösteriyor mu, cihazın tailnet'e bağlı olduğundan emin ol. |

### GPU'suz sunucu

Yerel ağır sesler (Coqui XTTS, Anka, Chatterbox) GPU'suz sunucuda CPU'da çok
yavaş çalışır; Edge-TTS ve Piper sorunsuz çalışır. İmaj GPU olmasa da CUDA
Torch ile kurulduğu için çok yer kaplar (§1'deki 20 GB boş alan önerisi geçerli).

Ağır sesleri (XTTS v2, Piper) Colab gibi GPU'lu bir makinede çalıştırıp bu sunucudan
kullanmak için [REMOTE_TTS.md](REMOTE_TTS.md) rehberine bak.

### VPS'e adım adım (GPU'suz, yalnızca sen ve Tailscale)

Kavra'nın kendi kopyası VPS'te çalışır; telefondan ve bilgisayarından Tailscale ile o kopyaya bağlanırsın.
**Verini tek yerde tut:** VPS ve bilgisayarındaki iki ayrı Kavra kopyası birbirinden bağımsız
`projects/`, kart destesi ve çalışma takibi verisi tutar. Bilgisayarında da VPS'teki adresi tarayıcıdan
aç; ağır sesler için bilgisayarının GPU'sunu VPS'e "Uzak GPU" olarak ver ([REMOTE_TTS.md](REMOTE_TTS.md)).

1. **Kodu paketle (bilgisayarında):**

   ```bash
   python tools/pack_for_vps.py
   scp _cache/kavra-deploy.tgz kullanici@VPS-ADRESI:~/
   ```

   Paket yalnızca izin verilen dosyaları içerir; `.env`, `settings.json`, `projects/`, `models/` girmez.
2. **VPS'e Docker ve Tailscale kur** (Ubuntu/Debian):

   ```bash
   curl -fsSL https://get.docker.com | sh
   curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
   ```

   **VPS'te zaten başka bir site/servis çalışıyorsa** aşağıdaki `ufw` komutunu olduğu gibi
   çalıştırma — mevcut kurallarını sıfırlayıp o servisin (ör. 80/443) portlarını kapatabilirsin.
   Önce mevcut durumu gör:

   ```bash
   sudo ufw status verbose
   ```

   `ufw` hiç aktif değilse veya zaten 80/443'ü (Cloudflare/nginx için) açık tutuyorsa, sadece SSH'i
   ekleyip dokunmadan bırak:

   ```bash
   sudo ufw allow OpenSSH   # ufw zaten aktifse; değilse "sudo ufw enable" mevcut kuralları etkilemez
   ```

   Kavra'nın kendisi hiçbir genel porta ihtiyaç duymaz (yalnızca Tailscale üzerinden erişilir,
   bkz. §8 altında), bu yüzden mevcut sitenin 80/443 kuralına dokunmana hiç gerek yok.
3. **Paketi aç ve ayarla:**

   ```bash
   tar -xzf kavra-deploy.tgz && cd kavra
   cp .env.example .env && nano .env
   ```

   `.env` içinde: `KAVRA_LOCAL_TTS=0` (hafif imaj), `KAVRA_ALLOWED_HOSTS=vps.tailnet-adi.ts.net`
   ve gerekiyorsa API anahtarların.
4. **Verini taşı (bir kez, bilgisayarından):** `models/` (Piper modeli ve klon referansları; kaynak tarafta
   durur) ve istediğin `projects/` ile `study_data/` klasörlerini `~/kavra/` altına kopyala:

   ```bash
   scp -r models study_data kullanici@VPS-ADRESI:~/kavra/
   scp -r projects kullanici@VPS-ADRESI:~/kavra/      # büyük olabilir; ölçüsü kullanıcıya bağlı
   ```
5. **Başlat:** `docker compose up -d --build`, ardından `sudo tailscale serve --bg 8768`.
6. **Telefon:** Tailscale uygulamasını aynı hesapla kur, `https://vps.tailnet-adi.ts.net` adresini aç ve
   tarayıcı menüsünden **Ana ekrana ekle**.
7. **Güncelleme:** bilgisayarında `python tools/pack_for_vps.py` → `scp` → VPS'te aç → `docker compose up -d --build`.
   `.env`, `docker-data/`, `projects/`, `models/` ve `study_data/` bu adımdan etkilenmez.

**Anlatı üretimi:** Docker imajında `claude` komutu (Agent CLI) yoktur; VPS'te anlatıyı **Gemini veya OpenAI**
ile üret (`.env` içine `GEMINI_API_KEY` / `OPENAI_API_KEY` yaz veya arayüzden gir). Anahtar yokken üretim
maliyetsiz ve anlaşılır bir hatayla durur.

**Notlar:** VPS'te video birleştirme (FFmpeg) ve slayt çizimi CPU'da çalışır; süre VPS'in çekirdek
sayısına bağlıdır. Edge-TTS VPS'te internetle çalışır; XTTS/Piper için Uzak GPU'yu kullan. Yedek için
`docker-data/`, `projects/`, `study_data/` ve `models/` klasörlerini kopyalamak yeterlidir.

**VPS'te başka bir site/servis varsa (domain + Cloudflare vb.):** Kavra ile çatışmaz.
`compose.yaml` portu yalnızca `127.0.0.1:8768`'e bağlar (genel IP'de hiçbir port açmaz); Cloudflare/nginx
80/443'te çalışan siteni hiç görmez, dokunmaz. Kavra'ya erişim tamamen Tailscale üzerinden olduğu için
mevcut sitenin domain/DNS/sertifika ayarlarıyla da bir ilgisi yoktur. Tek gerçek paylaşım CPU/RAM/disk:
render sırasında FFmpeg CPU'yu doldurabilir, bu yüzden `compose.yaml`'da `deploy.resources.limits`
(`KAVRA_CPU_LIMIT`, `KAVRA_MEM_LIMIT`, varsayılan 2 çekirdek / 4 GB) ile üst sınır konuldu; mevcut
sitenin trafiği yoğunsa bu sınırı `.env`'den düşür, tek kullanıcılık boş bir VPS'te yükseltebilirsin.
Container adı çatışmasını önlemek için `docker ps` ile mevcut container adlarına bir göz at.
