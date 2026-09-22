# Flashcard çalışma masası

Flashcard bölümü tamamen yerel çalışır. Çalışma, istatistik, kart düzenleme ve aktarım sırasında LLM/TTS çağrısı yapılmaz. Yalnız kullanıcının ayrıca seçtiği yapay zekâ ile deste üretimi eski sağlayıcı akışını kullanır.

## Kullanım

Proje → Flashcard Çalışma → deste seç. İstersen kaynak anlatımından kart üret veya bir ad yazıp **Boş deste oluştur** ile elle kart ekle / TSV içe aktar.

- **Deste:** Bugünkü yeni, öğrenme ve tekrar sayıları. Şimdi çalış ile başla.
- **Kartlar:** Soru, cevap ve etikette arama; durum ve etiket filtresi; 40 kartlık sayfalama. Seçilen kartları ertele, askıya al, işaretle veya topluca etiketle.
- **Seçenekler:** Deste adı, günlük yeni kart / tekrar limitleri, öğrenme adımları, mezuniyet ve kolay aralıkları, gün başlangıcı, kardeş kart erteleme.
- **İstatistikler:** Bugünkü yanıt dağılımı/süre, son yedi günün çalışmaları, gelecek yedi günün mevcut vadeleri.
- **İçe aktar:** UTF-8 TSV / TXT; soru, cevap ve isteğe bağlı etiket sütunu. Tırnakla çevrili çok satırlı alanlar desteklenir. Aynı soru-cevap çifti tekrar eklenmez.
- **Anki'ye aktar:** Başlık direktifleri, etiketler ve kayıpsız metin içeren TSV. Gerçek Anki'de Temel not tipiyle içe aktarılır; boşluk kartları da soru/cevap çifti olarak taşınır. Takvim ve geçmiş taşınmaz.

## Öğrenme davranışı

Varsayılan: günde 20 yeni kart, 200 tekrar; öğrenme 1 ve 10 dakika; yeniden öğrenme 10 dakika; mezuniyet 1 gün, kolay 4 gün. Yeni çalışma günü bilgisayarın yerel saatinde 04.00'te başlar.

Yeni kartta Tekrar → 1 dakika; Zor → 5,5 dakika; İyi → 10 dakika; Kolay → 4 gün. Son öğrenme adımındaki İyi kartı tekrar durumuna geçirir. Unutulan tekrar kartı kısa yeniden öğrenme adımına döner. Başlanmış öğrenme adımları günlük tekrar sınırına takılmaz.

Aynı slayttan veya aynı ters/boşluk notundan gelen kardeş yeni/tekrar kartları, seçenek açıksa ertesi çalışma gününe ertelenir. Askıya alma süresizdir; bugün erteleme gün başlangıcında kendiliğinden kalkar.

Geri al, sunucuda kayıtlı son değerlendirmenin zamanlamasını, günlük sayacını ve o işlemde ertelenen kardeşlerini geri getirir. Uygulama yeniden açılsa da çalışır. Eşzamanlı veya yinelenen değerlendirmeler kart sürümüyle denetlenir.

## Kart türleri

- Temel: tek soru/cevap.
- Temel + ters: aynı nottan iki yönde bağımsız kart.
- Boşluk: `{{c1::cevap}}` veya `{{c1::cevap::ipucu}}`. Her farklı numara ayrı kart; aynı numara birden fazla yerde kullanılabilir. İç içe boşluk sözdizimi desteklenmez.

Üretilen kartların ön/arka yüzleri ayrı düzenlenir. Aynı nottan türeyen diğer kartlar otomatik olarak yeniden yazılmaz.

## Kısayollar

Boşluk / Enter: cevabı göster; cevap açıkken İyi. 1–4: Tekrar, Zor, İyi, Kolay. Z: geri al. B: bugün ertele. E: düzenle. Esc: deste görünümü. Metin yazarken çalışma kısayolları devreye girmez.

## Veri uyumluluğu

Eski JSON desteleri açılırken yeniden zamanlanmaz; mevcut dueAt, easeFactor, repetitions ve içerik korunur. Açık öğrenme durumu ve sürüm bilgisi ilk yeni değerlendirmede eklenir. Yeni kayıtlar proje içindeki flashcards/<deste>.json dosyasında options ve reviewLog alanlarını kullanır. Eski çalışmalar için olay geçmişi uydurulmaz; istatistikler bu sürümde kaydedilen değerlendirmelerle başlar. Kaynaktan yeniden üretimde eşleşen kartların yeni zamanlama alanları, etiketleri ve işaretleri korunur.

Bu, Anki tarzı öğrenme adımları ve SM-2 temelli yerel zamanlayıcıdır; Anki'nin birebir kopyası veya FSRS uygulaması değildir. AnkiWeb senkronizasyonu, .apkg, medya/image occlusion ve gelişmiş not şablonları bu pakette yoktur. Uygulama tek yerel API süreciyle çalıştırılmalıdır; dosya yazma kilidi süreç içidir.

## Doğrulama

- tests/test_flashcard_study.py: öğrenme, günlük limit, gün değişimi, kardeş erteleme, kalıcı geri al, yinelenen istek, veri koruma, TSV gidiş-dönüş ve API akışı.
- tests/test_spaced_repetition.py: zamanlayıcı aralıkları ve eski kart uyumluluğu.
- tests/test_flashcards.py ve tests/test_web_api.py: var olan özelliklerin regresyonu ve Anki metin dışa aktarımı.

Referans davranışlar: [Anki deste seçenekleri](https://docs.ankiweb.net/deck-options), [Anki metin aktarımı](https://docs.ankiweb.net/importing/text-files.html).


## Bir dersteki birden fazla kaynaktan deste oluşturma

Yeni ders projelerinde kaynakları işaretleyip **Kart desteleri** sekmesini aç. Bir PDF için ayrı deste veya birkaç haftanın PDF'lerinden ortak deste oluşturabilirsin. Ücretsiz kaynak kartları ve yapay zeka ile soru üretimi bu seçime uyar; önce anlatı veya video üretmek gerekmez. Kart ve çalışma geçmişi yine deste bazında tutulur. Ayrıntılar: [COURSE_PROJECTS.md](COURSE_PROJECTS.md).
