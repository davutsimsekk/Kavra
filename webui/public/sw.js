// Kavra service worker — TEK amacı: Chrome/Android'in "Ana ekrana ekle" / "Uygulamayı
// yükle" davetini tetiklemek için gereken kurulabilirlik şartını (bir fetch dinleyicisi
// olan kayıtlı bir service worker) sağlamak. Bu SADECE bir istemci-tarafı arayüz kabuğu
// önbellekleme mekanizması — asıl uygulama her zaman canlı bir Kavra sunucusuna
// (bkz. DOCKER.md §8, Tailscale üzerinden) ihtiyaç duyar, o yüzden veri API'sini ASLA
// önbelleklemez.
const CACHE_NAME = 'kavra-shell-v1';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // İş durumu, proje verisi, render ilerlemesi gibi HER ŞEY /api/ altında — bunlar ASLA
  // önbelleklenmemeli, aksi halde bayat bir iş durumu ya da kalite raporu gösterilebilir.
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      try {
        const response = await fetch(request);
        if (response.ok) cache.put(request, response.clone());
        return response;
      } catch (err) {
        const cached = await cache.match(request);
        if (cached) return cached;
        throw err;
      }
    })()
  );
});
