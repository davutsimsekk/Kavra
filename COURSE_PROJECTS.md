# Çok kaynaklı ders projeleri

## Kullanım

1. Proje kitaplığında **Ders projesi oluştur** ile dersine ad ver.
2. Proje açılınca kartlı genel bakıştan **Kaynaklarını yönet** alanına gir; PDF, PowerPoint ve Markdown dosyalarını birlikte veya ayrı ayrı ekle.
3. Genel bakışta **Dersini hazırla** ile video kitaplığını aç. **Yeni video oluştur** ile kaynak seçimini aç. İlk yüklemede yalnızca ilk kaynak seçilir.
4. **Video nasıl görünsün?** altında **PDF üzerinden anlat** veya **Yeni slaytlar oluştur** seç. PDF modunda tüm sayfalar (metinsiz sayfalar dahil) sırayla korunur: 20 sayfa = 20 slayt. PDF kaynaklarında bu seçenek varsayılandır. Ardından **Video alanını oluştur** ile bağımsız bir video çalışması aç. Anlatı ve ses üretimi sonraki ekrandan başlatılır.
5. Ardışık konuları tek videoda birleştirmek için birkaç kaynak seçip yukarı/aşağı düğmeleriyle sırala.
6. Genel bakışta **Kartlarla öğren** alanından mevcut destelerini çalış veya **Yeni deste oluştur** ile bir veya birden fazla kaynaktan deste oluştur. Önceden anlatı/video oluşturmak gerekmez.

Örnek: “Biyoloji” dersine 10 haftalık PDF yüklenebilir; her PDF'nin ayrı videosu, 1–5. haftaların ortak vize destesi ve tüm kaynakları kapsayan final destesi aynı projede saklanır.

## Tek kaynak kalitesi ve bağımsız çıktılar

Tek kaynaklı video, ayrıştırıcının özgün bölümlerini değiştirmeden mevcut anlatı üretim hattına verir. Parçalama, ders hafızası, süre hedefi, PDF sayfa görselleri, kalite denetimi ve ses/video önbelleği aynı şekilde çalışır.

Birden fazla kaynak seçilen sırada art arda eklenir. Kaynak sınırları bölüm yollarında ve ayrı referans dosyasında korunur. Metinler önceden özetlenmez veya tek dev isteğe zorlanmaz. Sunum biçimi yükleme sırasında değil video oluşturulurken seçilir. PDF üzerinden anlatım için tüm seçili kaynakların PDF olması gerekir; yeni slayt modunda farklı dosya türleri birleştirilebilir. Aynı PDF'den her iki biçimde ayrı videolar oluşturulabilir. Önceden yüklenmiş PDF'ler tekrar yüklenmeden kullanılabilir.

PDF sayfa görüntüleri video oluşturulurken yerel olarak hazırlanır; bu işlem ücretli görsel açıklaması çağrısı yapmaz. Her video kendi sayfa görüntülerini saklar. Tek slaytı yeniden üretmek PDF sayfasını ve slayt sayısını korur. Sayfa modunda çağrı başına kaynak sayısı 1–20 arasında seçilebilir (varsayılan 4); kısa kaynaklar için güvenli tek istek seçeneği de kullanılabilir. Çok sayfalı yanıt kaynak kimlikleriyle doğrulanıp doğru PDF sırasına getirilir. Eksik/geçersiz kimlikli bir parça kaydedilmez ve otomatik ek üretim çağrısı yapılmaz. Birden fazla slayta bölünmüş bir sayfa sadece kendi parçalarıyla birleştirilir. Gemini/OpenAI çağrıları önceki anlatımların sınırlı özetini alır; Claude resume seçeneği mevcut oturumu sürdürür. Kaynak görseli bulunamazsa sessizce farklı bir tasarıma geçilmez, hata gösterilir.

Her video kendi anlatısını, üretim kontrol noktasını, agent oturumunu, sürümlerini, seslerini, önizlemesini ve çıktı dosyalarını tutar. Başka video oluşturmak mevcut anlatıyı veya çıktıları değiştirmez. Kaynak seçimi video oluşturulduğunda sabitlenir; farklı kaynaklarla çalışmak için yeni video açılır. Başlatılan anlatı ve video işlerinin gizli anahtar içermeyen ayarları video ile saklanır.

## Kartlar ve maliyet

Ücretsiz kaynak kartları, seçilen dosyaların metin bölümlerinden başlık/açıklama kartları üretir. Hangi kaynak ve bölümden geldikleri korunur; “Kaynağa git” özgün kaynak bölümünü açar.

Yapay zeka kartları yalnızca seçilen kaynakları kullanır. İçerik en fazla 24.000 karakterlik isteklere bölünür; arayüz üretim öncesinde tahmini çağrı sayısını gösterir. En fazla 12 üretim parçasına izin verilir; daha büyük seçimlerde kaynak kapsamının daraltılması istenir. Yanıt biçimi düzeltmesi bir parça için ek çağrı gerektirebilir. Toplam kart hedefi en fazla 60'tır; her parçaya hedefin bir payı verilir.

Destenin kaynak kimlikleri ve adları kalıcıdır. Statik destenin yeniden oluşturulması sadece kendi kaynaklarını kullanır. Yapay zeka kartları birden fazla bölümden sentezlenebildiğinden tek kart için doğrulanmamış bir bölüm referansı üretilmez; deste düzeyinde kaynak listesi tutulur.

Toplam maliyet hesabına ders kökündeki kart işlemleri ve tüm alt video çalışmalarındaki üretim kayıtları dahil edilir.

## Disk yapısı

    projects/
      ders-<kimlik>/
        course.json
        sources/
          <kaynak-id>/
            source.json
            document.pdf
            raw_sections.json
            ... PDF sayfa/görselleri ...
        videos/
          <video-id>/
            video.json
            raw_sections.json
            source_refs.json
            script.json
            generation_checkpoint.json
            snapshots/
            assets/
            ders.mp4
            ders.mp3
        flashcards/
          <deste-id>.json

Yüklenen dosya projeye kopyalanır; geçici yükleme dosyası silinebilir. Kaynak içeriği başarılı ayrıştırmadan sonra yayımlanır. Aynı adlı iki dosyanın kimlikleri farklıdır ve birbirlerini ezmez. Bir dosya yüklenemediğinde önceden başarıyla eklenen dosyalar korunur.

Eski tek kaynaklı projeler silinmez; kitaplıkta “Eski proje” olarak kendi mevcut düzenlerinde açılır. Yeni oluşturulan projeler ders modelini kullanır.

## API

- POST /api/projects: ders oluştur.
- POST /api/projects/{id}/sources/upload: bir dosya yükle (arayüz çoklu seçimi sırayla gönderir).
- POST /api/projects/{id}/sources/path: yerel dosya ekle.
- GET /api/projects/{id}/sources/{sourceId}: kaynak bölümleri.
- GET /api/projects/{id}/sources/{sourceId}/file: özgün dosyayı indir.
- POST /api/projects/{id}/videos: sıralı sourceIds ve presentationMode (pdf veya generated) ile bağımsız video oluştur. Alanı göndermeyen eski istemciler kaynakta kayıtlı modu kullanır.
- GET /api/projects/{id}/videos/{videoId}: video çalışma alanı.
- Anlatı, slayt, önizleme, render, çıktı, bölüm ve export işlemleri videonun apiBase alanı altında çalışır.
- POST /api/projects/{id}/flashcards/decks: ders projelerinde sourceIds gerektirir.
- POST /api/projects/{id}/flashcards/source-estimate: ücretli üretim başlamadan parça/karakter sayısını hesaplar.

## Doğrulama

tests/test_course_projects.py: ad çakışmaları, başarısız yükleme, kaynak seçim sırası, projeler/video çıktıları arasında izolasyon, doğrudan çok kaynaklı kart üretimi, kaynak referansları, iç içe maliyet hesabı, çağrı/kart sınırları, tek kaynaklı anlatı uyumluluğu ve gerçek PDF sayfa görselleri.
