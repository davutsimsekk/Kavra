# XTTS v2 ve Chatterbox Kadın Ders Sesleri

Seçim tarihi: 25 Eylül 2026

On bir XTTS v2 kadın konuşmacı adayı aynı 32 kelimelik Türkçe teknik ders metniyle üretildi. Model yükleme süresi değerlendirmeye katılmadı. Uzun derslerde yoruculuğu azaltmak için perde kararlılığı, ses seviyesi, konuşma süresi ve spektral parlaklık birlikte değerlendirildi.

## Eklenen sesler

| Ses | Kullanım önerisi | Medyan perde | Süre |
|---|---|---:|---:|
| **Claribel Dervla** | Önerilen; sıcak, tok ve kararlı uzun ders anlatımı | 145,5 Hz | 14,06 sn |
| Ana Florence | Sakin ve düşük tonlu açıklamalar | 158,9 Hz | 14,67 sn |
| Tanja Adelina | Dengeli ve kararlı genel anlatım | 180,5 Hz | 14,76 sn |
| Tammy Grit | Orta tonlu, net alternatif | 166,7 Hz | 15,03 sn |
| Sofia Hellen | Daha parlak ve enerjik alternatif | 190,5 Hz | 14,24 sn |

## Elenen adaylar

Daisy Studious ve Brenda Stern uzun ders için daha yüksek perdeli; Gracie Wise ve Henriette Usha daha yavaş/değişken; Alison Dietlinde ve Asya Anara ise seçilen dengeli alternatiflere göre ek bir avantaj sağlamadığı için ana listeye alınmadı. Örneklerin tamamı uygulamanın kendi `_cache\voice_selection\xtts_female_candidates` klasöründe korunmaktadır.

XTTS v2 bu konuşmacıları doğrudan checkpoint kimliğiyle kullanır. Chatterbox aynı Türkçe WAV dosyalarını zero-shot klon referansı olarak kullanır; bu nedenle iki motorun tınısı benzer hedefe yönelse de birebir aynı çıkması beklenmez.
