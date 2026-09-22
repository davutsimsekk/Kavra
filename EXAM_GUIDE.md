# Sınav hazırlama

Ders projesindeki **Sınav hazırla** kartını aç. **Yeni sınav oluştur** ile soldan ders
kaynaklarını seç; soru sayısı (1–40), şıklı/klasik/karma tür, şık sayısı (3–5), zorluk
ve isteğe bağlı odak talimatını belirle. Karma sınavda şıklı soru adedi ayrı seçilir.

## Çıkmış sınavlar

PDF, TXT, Markdown ve PowerPoint dosyaları ayrı örnek kitaplığına yüklenir. Ders
kaynaklarıyla karıştırılmaz; her üretimde hangi örneklerin kullanılacağını seçersin.
Örnekler soru dili ve yapısını yönlendirir, doğru bilginin kaynağı seçili ders
dosyalarıdır. Zorlukta **Çıkmış sınava benzer** seçilebilir. Soruların birebir
kopyalanmaması modele açıkça belirtilir; bu, anlamsal özgünlük garantisi değildir.

Yükleme ücretsiz yerel metin çıkarımı yapar. Metni okunamayan taranmış PDF'lerde
OCR gerektiği açıkça bildirilir. Dosya başına 20 MB, en fazla 5 seçili örnek ve
örneklerin toplamında 24.000 karakter sınırı vardır.

## Üretim ve kalite

Gemini varsayılandır; kayıtlı anahtar kullanılır. Claude Agent ve OpenAI uyumlu
sağlayıcılar da seçilebilir. Anahtarlar sınav dosyasına kaydedilmez.
Seçili ders içeriği önceden özetlenmeden gönderilir; 100.000 karakteri aşarsa
sessizce kesilmez, kapsamı daraltman istenir.

Bir ana üretim çağrısı yapılır. Soru sayısı/tür dağılımı, doğru şık indeksi,
farklı şıklar, cevap/açıklama ve kaynak kimlikleri doğrulanır. Geçersiz sonuç için
en fazla bir düzeltme çağrısı yapılır; ikinci hatalı yanıt yayımlanmaz.
Sağlayıcının ağ/hız sınırı tekrarları bu sayımdan ayrıdır. Maliyet defteri istek
sayısını kaydeder; Gemini için doğrulanmamış dolar maliyeti hesaplanmaz.
Biçim ve kaynak kimliği doğrulaması içerik doğruluğunun yerini tutmaz.

## Çözme ve saklama

Şıklı yanıtlar yerel olarak değerlendirilir. Klasik sorularda örnek cevap,
açıklama ve değerlendirme ölçütleri gösterilir; otomatik yapay zeka puanlaması yoktur.
Cevap taslağı ve bitirme durumu aynı tarayıcıda saklanır. Sınavın kendisi dersin
exams/<id>/exam.json dosyasına kaydedilir. Çıkmışlar exam_references/ altında tutulur.
Sorular ve cevap anahtarı ayrı Markdown dosyaları olarak indirilebilir.

Arka plan üretimi başka ekrana geçince devam eder. İş kimliği tarayıcıda saklanır;
derse dönünce sonuç alınır. Servis kapanırsa mevcut kalıcı iş sistemi işi kesildi
olarak işaretler; tekrar üretim kullanıcı tarafından başlatılır.


## Geliştirilmiş kalite ve çalışma akışı

Yeni sınavlarda model, cevabın dayanağını önceden numaralanmış özgün kaynak
parçalarından seçer. Alıntıları model yeniden yazmaz; uygulama seçilen parçanın
metnini doğrudan sınava ekler. Geçersiz parça kimliği ve yanlış kaynak reddedilir.
Cevap anahtarındaki **Kaynak dayanaklarını göster** ile bu metin incelenebilir.
Bu doğrulama alıntının kaynaktaki varlığını doğrular; sorunun anlamsal doğruluğunu
tek başına garanti etmez.

Doğru şık indeksi ile cevap metni arasındaki çelişkiler, birbirine çok benzeyen
sorular ve çıkmış metninden aynen alınmış uzun ifadeler kontrol edilir.
Bu yerel kontroller ek model incelemesi gerektirmez. Mevcut en fazla bir düzeltme
çağrısı kuralı korunur. Okunamayan kaynak bölümleri ve hiç soru dayanağı olarak
kullanılmayan seçili kaynaklar görünür biçimde belirtilir.

Sınav oluşturma taslağı (kaynaklar, çıkmış seçimleri, ad ve ayarlar) aynı tarayıcıda
saklanır. API anahtarı taslağa yazılmaz.
Çalışırken sorular işaretlenebilir; tüm/boş/işaretli filtreleri, bitirdikten sonra
yanlış filtresi kullanılabilir. Soru haritası sorulara hızlı geçiş sağlar.
Boş cevap varken bitirme işlemi kullanıcıya sorulur. **Yeniden çöz** cevapları
temizler, son 10 tamamlamanın sonuç özetini korur. Klasik sorular şıklı başarı
sayımına dahil edilmez. Eski sınavlar ve cevap kayıtları çalışmaya devam eder.


## Word ve PDF indirme

Sınav ekranının üstündeki **Dosya biçimi** alanından PDF, Word (.docx) veya Markdown (.md) seçilir. **Soruları indir** cevapları içermeyen soru kâğıdını, **Cevap anahtarını indir** sorularla birlikte cevapları, açıklamaları, ölçütleri ve mevcut kaynak dayanaklarını indirir.

PDF ve Word çıktıları A4 düzenindedir; soru kâğıdında ad-soyad/tarih alanı, klasik sorularda yazma boşlukları bulunur. Dışa aktarma yereldir ve LLM çağrısı yapmaz. PDF mevcut PyMuPDF ile, Word `python-docx` ile oluşturulur. Markdown ve belge çıktıları aynı kayıtlı sınavı kullanır; yeniden soru üretmez. Kurulumda `pip install -r requirements.txt` yeterlidir.

API: `GET /api/projects/{project_id}/exams/{exam_id}/export?format=pdf&answers=false`. Desteklenen biçimler `md`, `docx`, `pdf`; geriye uyumluluk için varsayılan `md`.
