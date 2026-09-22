# Çalışma takibi

Çalışma takibi ana ekrandan veya dersin **Çalışmanı takip et** kartından açılır. Çalışma projeleri ders projelerinden bağımsızdır; istenirse bir derse bağlanır. Bu özellik yapay zekâ çağrısı veya harici hizmet gerektirmez.

## Kullanım

1. Sol taraftaki **Projeler +** ile bir çalışma projesi oluştur. İstersen bağlı dersi ve proje rengini seç.
2. Proje içinde klasörler, görevler ve alt görevler oluştur. Klasörler ve görevler en fazla 8 seviye derinleşebilir.
3. Göreve plan günü/saati, son tarih, tahmin, öncelik, etiket ve not ekle. Notlarda `[ ]` ve `[x]` ile kontrol listesi kullanılabilir.
4. Göreve tıkla; odak panelinde Pomodoro, kronometre veya geri sayım seç. Paneli büyüterek yalnızca o göreve odaklan.
5. Bugün görünümünde bugüne veya daha önceye planlanan, son tarihi gelen ve üzerinde çalışılan görevleri gör. Gelen kutusu planlanmamış görevleri içerir.
6. Liste/pano düğmeleriyle görünümü değiştir. Pano durumunu menüyle veya sütuna sürükleyerek değiştir; listede yukarı düğmesi/sürükleme ile sırayı değiştir.
7. Haftalık planda görevleri günler arasında sürükle. Görev formu aynı işlemin klavye ve mobil alternatifidir.
8. Raporlar son 7 günün sürelerini ve proje dağılımını gösterir. Süre kayıtlarında yanlış veya unutulmuş zamanları ekle, düzenle veya sil.

## Sayaç davranışı

- Tek aktif sayaç vardır. Başka görev başladığında öncekinin süresi kaydedilir.
- Başlama zamanı ve duraklatılan toplam yerel SQLite veritabanındadır. Sekme yenileme, başka bir ekrana geçme veya uygulamayı yeniden açma sayacı sıfırlamaz.
- Pomodoro/geri sayım hedef sürede biter. Arayüz uzun süre kapalı kalsa da fazla süre yazılmaz. Bitme durumu bir sonraki sunucu kontrolünde hesaplanır.
- Kısa/uzun mola süreleri çalışma toplamına eklenmez. Yeni aralık kullanıcı başlatınca çalışır; kapalı tarayıcıda kendiliğinden yeni çalışma oturumları yazılmaz.
- Kronometre duvar saatini kullanır; bilgisayarın uyku süresini de sayar. Masaüstü boşta kalma algılama henüz yoktur. Yanlış süreler Kayıtlar ekranından düzeltilebilir.
- Tarayıcı açıkken ses ve izin verilmiş bildirimler kullanılabilir. İşletim sistemi/tarayıcı kısıtları nedeniyle kapalı tarayıcıya bildirim garantisi yoktur.
- Tüm süreler görevde doğrudan tutulan sürelerdir; alt görev süresi üst göreve tekrar eklenmez. Proje raporu hepsini bir kez toplar.

## Tekrar ve arşiv

Günlük, haftalık ve aylık tekrar; görev tamamlandığında bir sonraki açık görevi oluşturur. Geçmiş günler için görev yığını oluşturmaz. Aynı görevi tekrar açıp bitirmek ikinci kopya çıkarmaz. Bir sonraki tekrar üst düzey görevdir; alt görev ağacı otomatik kopyalanmaz. Açık alt görevler tamamlanmadan üst görev tamamlanamaz.

Görev/proje arşivlemek süre geçmişini silmez. Arşivlenen görevlerin alt görevleri de arşivlenir; geri alınabilir. Klasör kaldırıldığında görevler ve alt klasörler üst klasöre taşınır. Süre kaydı silme işleminde kullanıcıya ayrıca sorulur.

## Veri ve testler

- Veri dosyası: `study_data/study.sqlite3`. Önbellek değildir; klasörü yedeklere dahil et.
- Ayarlar ekranındaki JSON yedeği tüm projeleri/görevleri/notları/süreleri içerir. Aktif sürenin yedekleme anına kadar olan kısmı dahil edilir; geri yüklenen sayaç çalışmaz.
- Geri yükleme mevcut çalışma alanını değiştirir. Önce kendi yedeğini indir. Geri yükleme öncesindeki son 5 durum SQLite içindeki `recovery` tablosunda ayrıca korunur.
- CSV yalnızca kaydedilmiş oturumları içerir; başlangıç UTC olarak belirtilir. Aktif oturumu CSV'ye dahil etmek için önce duraklat.
- Yerel işlemler SQLite transaction ve sürüm kontrolü kullanır. Başka sekmeden eski veriye yazma isteği 409 ile reddedilir; ekran güncellenir, kullanıcı işlemi yeniden yapar.
- `python -m pytest` artık yalnızca `tests/` dizinini toplar. `_cache` içindeki eski, dış servis çağırabilen deneme betikleri kapsam dışıdır.
- `node --test webui/tests/*.test.mjs` süre, gün bölme ve görünüm filtrelerini doğrular.

## Kapsam ve sonraki aşamalar

Super Productivity'nin görev/süre/odak akışını temel alan bu modül özgün uygulama arayüzüne uyarlanmıştır; birebir tüm ürünün kopyası değildir. Referans: https://super-productivity.com/

Hazır: bağımsız ve ders bağlantılı projeler, iç içe klasörler/alt görevler, görev notları/kontrol listeleri, plan/tarih/tahmin/öncelik/etiket, günlük/haftalık/aylık tekrar, hızlı ekleme, arama/filtre, liste/pano, haftalık plan, kalıcı sayaç, Pomodoro/molalar/odak modu, günlük hedef/not, zaman düzeltme, rapor, arşiv, JSON/CSV, açık/koyu/mobil, temel klavye kısayolları.

Sonraki aşama: harici görev servisleri ve cihazlar arası senkronizasyon (kullanıcı tercihiyle ertelendi), takvim abonelikleri, işletim sistemi boşta kalma algılama, gelişmiş tekrar kuralları/alt görev şablonları, tam zaman çizelgesinde çakışma uyarıları, genişletilebilir eklentiler ve görev bağımlılıkları.
