// Generated constants contain only signed, public shell asset names.
const CACHE = __CACHE_NAME__;
const ASSETS = __ASSET_URLS__;
const INDEX = __INDEX_URL__;
const allowed = new Set(ASSETS.map(path => new URL(path, self.location.origin).href));
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    try { await cache.addAll(ASSETS.map(url => new Request(url, { credentials: 'omit', cache: 'reload' }))); }
    catch (error) { await caches.delete(CACHE); throw error; }
  })());
});
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('message', event => {
  if (event.data === 'activate-ui') void self.skipWaiting();
});
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  if (request.mode === 'navigate' && (url.pathname === '/' || /^\/orders\/[A-Za-z0-9_.:-]+$/.test(url.pathname))) {
    event.respondWith((async () => {
      try { const response = await fetch(request); if (response.ok) return response; } catch {}
      return await (await caches.open(CACHE)).match(INDEX) ?? Response.error();
    })());
  } else if (allowed.has(request.url)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      return await cache.match(request.url) ?? fetch(request);
    })());
  }
});
