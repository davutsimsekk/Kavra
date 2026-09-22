# Uzak GPU ile seslendirme (PC ve/veya Colab)

GPU'su olmayan bir Kavra sunucusu (ör. VPS), **XTTS v2** ve **Piper** seslendirmesini GPU'lu uzak bir
makineye devredebilir. Ses ayarlarında **iki sabit GPU profili** vardır — **Bu bilgisayar** (kendi
ekran kartın, Tailscale üzerinden) ve **Colab** — hangisinin *aktif* olduğunu tek tıkla değiştirirsin;
ikisi de kaydedilir, ikisi arasında geçişte adres/token'ı yeniden yazmana gerek kalmaz. Bilgisayarında
render alırken ise aynı seçenekler her zamanki gibi **yerelde** çalışır (*Çalıştırma yeri: Bu bilgisayar*).

```
VPS'teki Kavra ── aktif profil = "Bu bilgisayar" ──▶ Tailscale ──▶ PC'nin GPU'su (run_tts_server.bat)
                └─ aktif profil = "Colab" ──────────▶ cloudflared tüneli ──▶ Colab GPU'su
Kendi bilgisayarın (render alırken) ── "Bu bilgisayar" ────────────────────▶ yerel GPU (değişen yok)
```

Uzakta desteklenen motorlar: **XTTS v2 (coqui)** ve **Piper**. Chatterbox, Anka ve Edge/ElevenLabs bu
özelliğin kapsamı dışındadır (Chatterbox ayrı ortam ister; Edge/ElevenLabs zaten bulut).

## Bilgisayarını uzak GPU yap (kalıcı, önerilen)

Bilgisayarın açıkken Tailscale bağlıysa GPU'n hazır olsun istiyorsan sunucuyu Windows açılışında
otomatik, görünmeden başlat:

```powershell
powershell -ExecutionPolicy Bypass -File install_tts_autostart.ps1
```

Bunu **kendi normal masaüstü oturumunda** (Başlat menüsünden açtığın bir PowerShell penceresi)
çalıştır — uzak/otomasyon oturumlarından Görev Zamanlayıcı'ya görev eklenmesi Windows tarafından
engellenebilir; script böyle bir durumda "KURULAMADI" diye açıkça söyler, sessizce başarılı görünmez.

- Durum ve token: `powershell -ExecutionPolicy Bypass -File install_tts_autostart.ps1 -Status`
- Kaldırmak için: `powershell -ExecutionPolicy Bypass -File install_tts_autostart.ps1 -Uninstall`
- Elle, tek seferlik başlatmak için (otomatik başlatmadan bağımsız): `run_tts_server.bat`'ı çift tıkla.

Sunucu modelleri **ilk render isteğinde** yükler (`--no-preload`); boştayken VRAM'i işgal etmez, ilk
render'da ~15-30 sn ekstra bekleme olur. Sonra bir kez, kalıcı olarak:

```powershell
tailscale serve --bg 8790
```

Adres artık `https://BILGISAYAR-ADI.tailnet-adi.ts.net` — Tailscale bağlıyken sabit kalır, Colab
oturumu gibi kopmaz. Token, `_cache/tts_server_token.txt` içinde saklanır ve sunucu yeniden
başlasa da aynı kalır.

**Not:** Bilgisayarını video render'ı için yerelde de kullanıyorsan aynı anda uzaktan da render
isteği gelirse VRAM (8 GB) paylaşılır; 2 XTTS kopyası ~5-6 GB tutar. İkisini aynı anda yoğun
kullanma.

## Colab'ı kullan (bilgisayarın kapalıyken veya alternatif olarak)

### 1. Colab dosyalarını hazırla (Kavra klasöründe, bir kez)

```bash
python tools/build_colab.py
```

`colab/` klasöründe iki dosya oluşur: `Kavra_TTS_Sunucusu.ipynb` ve `kavra-tts-bundle.zip`.
Kod değişirse bu komutu yeniden çalıştırıp zip'i Colab'a yeniden yükle.

### 2. Colab'da sunucuyu başlat

1. [colab.research.google.com](https://colab.research.google.com) → **Dosya → Not defterini yükle** → `Kavra_TTS_Sunucusu.ipynb`.
2. **Çalışma zamanı → Çalışma zamanı türünü değiştir → GPU**.
3. Hücreleri sırayla çalıştır. İlk hücre `kavra-tts-bundle.zip` dosyasını ister.
4. Son hücrenin yazdırdığı **Adres** (`https://….trycloudflare.com`) ve **Token**'ı not al.
5. Render sürerken **son hücreyi ve sekmeyi açık bırak.**

*İsteğe bağlı:* Token'ın her oturumda aynı kalması için Colab **Secrets (🔑)** bölümüne
`KAVRA_TTS_TOKEN` adıyla en az 16 karakterlik bir değer ekle. (Adres ise cloudflared'in ücretsiz
"quick tunnel" özelliğinde her oturumda değişir.)

## Kavra'da bağlan (PC ve Colab için ortak)

1. Video çalışma alanı → **Ses** → Sağlayıcı: `coqui` (XTTS v2) veya `piper`.
2. **Çalıştırma yeri: Uzak GPU (Colab / Tailscale)**.
3. **Bu bilgisayar** veya **Colab** kartına adres ve token'ı gir → **Kaydet**.
4. Kullanmak istediğin kartın radio düğmesine tıkla (o profil **aktif** olur) → **Bağlantıyı dene**
   (GPU adını ve motor durumunu gösterir).
5. **Aynı anda gönderilen slayt** sayısını sunucudaki model kopyası sayısına eşitle (varsayılan 2).
6. Renderı başlat. Bağlantı sorunu varsa render başlamadan anlaşılır bir hata görürsün.

İki profil de her zaman kayıtlı kalır; aralarında geçiş yalnızca **hangisinin aktif olduğunu**
değiştirir. Bilgisayarın kapanır/Colab oturumu biterse diğerini aktif yapıp renderı yeniden başlat.

Adresler `settings.json`'a, token'lar diğer API anahtarları gibi `.env` dosyasına (profil başına ayrı
değişkende: `KAVRA_REMOTE_TTS_TOKEN_PC`, `KAVRA_REMOTE_TTS_TOKEN_COLAB`) yazılır; token arayüze veya
API yanıtlarına geri döndürülmez. Yerelde render alırken *Bu bilgisayar*'ı (üstteki *Çalıştırma yeri*)
seçmen yeterli, GPU profilleriyle bir ilgisi yoktur.

## Klonladığın sesleri VPS'ten de kullanmak istersen

Uzak GPU sesi **üretir**, ama Kavra'nın ses listesi ve dosya yükleme mantığı API'nin ÇALIŞTIĞI
makinedeki (VPS) `models/` klasörüne bakar. Yeni bir ses klonladığında:

```bash
python tools/sync_models_to_vps.py kullanici@vps-adresi
```

Bu, `models/` klasörünü (Piper modeli + klon referans WAV'ların) VPS'e kopyalar; Kavra'yı yeniden
başlatman gerekmez. `--dry-run` ile önce ne kopyalanacağını görebilir, `--identity`/`-i` ile belirli
bir SSH anahtarı, `--port` ile farklı bir SSH portu verebilirsin.

## Bilmen gerekenler

- **Önbellek ortaktır.** Motor ve ses aynıysa uzakta üretilen ses de yerelde üretilen ses gibi
  önbelleğe girer; aynı slaytı tekrar üretmez. Yerel ↔ uzak, PC ↔ Colab geçişi cache'i bozmaz.
- **Ses referansları Kavra tarafında durur.** Klon referansı ve Piper modeli, API'nin çalıştığı
  makinede (`models/`) bulunur ve ilk kullanımda aktif GPU'ya **bir kez** yüklenir (içeriğine göre
  adreslenir; değişmediyse tekrar yüklenmez). Cloudflare'in yükleme sınırı yaklaşık 100 MB'dır.
- **Aktif kaynak kapanırsa:** Colab oturumu bitince veya PC'nin Tailscale'i düşünce render hata verir.
  Diğer profili aktif yapıp renderı yeniden başlat; **biten sesler korunur**, yalnız eksikler üretilir.
- **Piper uzakta genelde işe yaramaz:** Piper CPU'da zaten hızlıdır. Uzağa göndermek çoğunlukla
  gecikme ekler; seçenek, tutarlılık için sunuldu.
- **Gizlilik:** Anlatı metinleri ve klon referansın (Colab için) Cloudflare ve Google altyapısından
  geçer (TLS ile, ama üçüncü taraflar). PC profili yalnızca kendi Tailscale ağından geçer. Adres
  kimsenin bilmediği rastgele bir alan adıdır ve token olmadan hiçbir uç çalışmaz.
- **Colab şartları:** Colab; web servisi barındırma benzeri kullanımları ve ücretsiz GPU'yu
  kısıtlayabilir, oturum süresi ve GPU erişimi garanti değildir. Güncel kullanım şartlarını kontrol et.

## Performans (ölçüldü: RTX 4060 Laptop, bu depoda)

| Durum | 4 slayt (~28 sn ses) | Gerçek zaman oranı |
|---|---|---|
| Sunucu 2 model, istemci 1 slayt | 13,7–14,2 sn | 0,49–0,51 |
| Sunucu 2 model, istemci 2 slayt | 11,1–11,2 sn | 0,39–0,40 |

Model kopyaları aynı süreçte thread'lerle çalıştığı için 2 kopya ~1,25× hızlanma verir; yerel
çok-süreçli Coqui yolu (bkz. `app/tts/coqui_parallel.py`) daha yüksek ölçeklenir. **Colab T4'ün hızı
ölçülmedi**, yalnızca bu bilgisayarın ekran kartı ölçüldü. Uzak GPU'nun asıl faydası hız değil,
GPU'suz sunucunun ağır motorları kullanabilmesidir.

## Sorun giderme

| Belirti | Neden / çözüm |
|---|---|
| "token'ı reddetti" | Ayardaki token, sunucunun yazdırdığıyla aynı değil. |
| "Bu adreste Kavra TTS sunucusu bulunamadı" | Adres eski/yanlış; PC'de `-Status`, Colab'da 4. hücrenin güncel adresini gir. |
| "Aktif GPU profili (…): ulaşılamıyor…" | O profilin sunucusu kapalı/tünel değişmiş; diğer profili aktif yap veya adresi güncelle. |
| "protokol sürümü uyumsuz" | `python tools/build_colab.py` ile zip'i yenileyip Colab'a yeniden yükle. |
| "'coqui' motoru kullanılamıyor" | Kurulum hatası; PC'de `_cache/tts_server.log`, Colab'da `/content/kavra_tts.log`'a bak. |
| Sunucu başlamıyor (bellek) | `--coqui-models 1` yap (PC: `run_tts_server.bat`, Colab: 3. hücrede `COQUI_MODELS`). |
| `install_tts_autostart.ps1` "KURULAMADI" diyor | Kendi masaüstü oturumunda (uzak/otomasyon araçlarından değil) tekrar çalıştır. |
