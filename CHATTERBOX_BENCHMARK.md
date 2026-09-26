# Chatterbox Paralel Slayt Benchmark'ı

Ölçüm tarihi: 25 Eylül 2026  
GPU: NVIDIA GeForce RTX 4060 Laptop GPU, 8 GB VRAM  
Referans ses: Damien Black (XTTS ile üretilmiş 8,6 saniyelik Türkçe WAV)  
İş yükü: Turbojet dersinden 6 gerçek anlatım başlangıcı, toplam 165 kelime / 1.239 karakter

## Yöntem

Her worker önce Chatterbox modelini tamamen yükledi ve ortak bir başlangıç bariyerinde bekledi. Kronometre yalnız bütün modeller hazır olduktan sonra başlatıldı. Bu nedenle aşağıdaki sentez sürelerine ilk model yüklenmesi dahil değildir. Çıktılar WAV yazılarak ffmpeg/MP3 dönüştürme maliyeti ölçüm dışında bırakıldı.

| Worker | Sentez duvar süresi | Sn/slayt | Slayt/dakika | RTF | Tepe VRAM | 1×'e göre |
|---:|---:|---:|---:|---:|---:|---:|
| 1× | 165,788 sn | 27,631 | 2,171 | 2,109 | 3.959 MB | 1,00× |
| 2× | 83,468 sn | 13,911 | 4,313 | 1,147 | 7.806 MB | **1,99×** |
| 3× | >270 sn, 0/6 tamamlandı | — | — | — | 7.946 MB | Kullanılamaz |

## Sonuç

Bu makinede 2× Chatterbox gerçek anlamda iki kata yakın hız kazandırıyor. Üç model yüklenebiliyor, fakat yalnız yaklaşık 240 MB VRAM payı bırakıyor; GPU %100'de kalırken yaklaşık 270 saniye içinde tek bir slayt dahi tamamlanmadı. Bu nedenle uygulamanın Chatterbox üst sınırı 2 worker olarak korunmalıdır.

Tek worker'ın ilk sentezi yaklaşık 50 saniye, sonraki kısa slaytları 22–25 saniye sürdü. İki worker'da worker başına ilk sentez 43–45 saniye, sonraki kısa slaytlar 17–20 saniye sürdü. Uzun anlatımlar 220 karakterlik güvenli parçalara ayrıldığı için tam slayt süresi metin uzunluğuna göre artar.

Ham raporlar uygulamanın kendi `_cache\tts_benchmarks\chatterbox_parallel_20260925` klasöründedir.
