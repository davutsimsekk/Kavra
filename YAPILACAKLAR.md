# Yapılacaklar

## PDF üzerinden anlatım kalitesi — yüksek öncelik

Mevcut anlatım modeli PDF'den çıkarılan metni alıyor; sayfa görseli videoda kullanılıyor.
Opsiyonel Gemini görsel açıklaması yalnızca metni çok az sayfalarda devreye giriyor.
Toplu sayfalarda kimlik kontrolü yapısal eşleşmeyi doğrular; model doğru kimlik altında
yanlış sayfanın içeriğini anlatırsa bu kontrol anlamsal hatayı yakalayamaz.

- [ ] Hedef sayfanın görseli ve metnini birlikte inceleyen kalite modu.
- [ ] Her çağrıda tek hedef sayfanın anlatımı; komşu sayfalar ve önceki anlatımlar yalnızca bağlam.
- [ ] Yazı bulunan sayfalardaki grafik, tablo, şema, formül ve ok ilişkilerini de incele.
- [ ] Görsel incelemesini kaynak dosyası/sayfa/model/talimat sürümüne göre önbellekle; tekrar ücretlendirme.
- [ ] Kullanıcıya metin tabanlı / görsel destekli modları, kapsamı ve tahmini istek sayısını göster.
- [ ] Görsel okunamıyorsa veya bilgi eksikse tahmin üretme; ilgili sayfayı açıkça işaretle.
- [ ] Sayfa bazında kaynak dayanağı ve anlatım uygunluğu kontrolü; isteğe bağlı ikinci model incelemesi.
- [ ] Grafik ağırlıklı, taranmış, formüllü ve metinli gerçek sunumlarla kalite değerlendirmesi.
- [ ] Aynı kaynakta 1/4 sayfalık üretimleri anlam doğruluğu, akıcılık, süre ve maliyetle karşılaştır.

Öneri: Kalite modunda geniş bağlamı tek hedef anlatımdan ayır. Sadece çağrı boyutunu
1'e indirmek görsel anlama eksikliğini çözmez. Claude resume mevcut konuşmayı sürdürür;
Gemini/OpenAI için seçilmiş önceki anlatım bağlamı gönderilir. Model yanıtını ve kaliteyi
gerçek ders örnekleriyle değerlendirmeden doğruluk garantisi verme.

## Sınav sistemi — sonraki geliştirmeler

- [ ] Taranmış çıkmış sınavlar için isteğe bağlı OCR/görsel okuma ve maliyet önizlemesi.
- [ ] Klasik cevapların kaynak ve rubriğe dayalı isteğe bağlı yapay zeka değerlendirmesi.
- [ ] Soru bazında hata bildirimi ve tek soruyu yeniden üretme.
- [ ] Zamanlı deneme, deneme geçmişi ve konu bazında başarı istatistikleri.


## Tamamlanan sınav iyileştirmeleri

- [x] Numaralanmış kaynak parçalarından doğrulanan dayanak alıntıları.
- [x] Doğru şık/cevap tutarlılığı, yakın yinelenen soru ve uzun çıkmış metni kopyası kontrolleri.
- [x] Sınav hazırlama taslağını API anahtarı olmadan saklama.
- [x] Soru işaretleme, soru haritası, boş/yanlış filtreleri ve sınav araması.
- [x] Sonuç geçmişini koruyarak yeniden çözme.

## Çalışma takibi

- [x] Derslere bağlanabilen bağımsız çalışma projeleri ve iç içe klasörler.
- [x] Görevler/alt görevler, plan günü-saati, tahmin, öncelik, etiket ve kontrol listeleri.
- [x] Kalıcı Pomodoro/kronometre/geri sayım, kısa-uzun mola, uygulama genelinde sayaç.
- [x] Bugün/gelen kutusu/liste/pano/haftalık plan, günlük hedef ve not.
- [x] Süre ekleme/düzeltme/silme, rapor, arşiv, JSON yedek/geri yükleme ve CSV.
- [ ] Harici görev servisleri ve cihazlar arası senkronizasyon (kullanıcı tercihiyle sonraki aşama).
- [ ] Masaüstü boşta kalma algılama, gelişmiş tekrar kuralları ve alt görev şablonları.
- [ ] Takvim abonelikleri, plan çakışma uyarıları, görev bağımlılıkları ve eklentiler.
