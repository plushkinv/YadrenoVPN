// Platform and application resources have independent cache identities.
const PLATFORM = __PLATFORM_CACHE__;
const APPLICATION = __APPLICATION_CACHE__;
const SHELL = __SHELL_CACHE__;
const FRAME = __FRAME_URL__;
const SYSTEM_ASSETS = __SYSTEM_ASSETS__;
const APPLICATION_ASSETS = __APPLICATION_ASSETS__;
const allowed = new Map([...SYSTEM_ASSETS.map(path => [new URL(path, self.location.origin).href, PLATFORM]),
  ...APPLICATION_ASSETS.map(path => [new URL(path, self.location.origin).href, APPLICATION])]);
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    for (const [name, assets] of [[PLATFORM, SYSTEM_ASSETS], [APPLICATION, APPLICATION_ASSETS]]) {
      const cache = await caches.open(name);
      await cache.addAll(assets.map(url => new Request(url, { credentials: 'omit', cache: 'reload' })));
    }
    const shell = await fetch('/', { credentials: 'omit', cache: 'reload' });
    if (!shell.ok) throw new Error('System shell unavailable');
    const cache = await caches.open(SHELL);
    await cache.put('/', shell);
    const frame = await fetch(FRAME, { credentials: 'omit', cache: 'reload' });
    if (!frame.ok) throw new Error('Application frame unavailable');
    await cache.put(FRAME, frame);
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
      return await (await caches.open(SHELL)).match('/') ?? Response.error();
    })());
  } else if (url.pathname + url.search === FRAME) {
    event.respondWith((async () => {
      try { const response = await fetch(request); if (response.ok) return response; } catch {}
      return await (await caches.open(SHELL)).match(FRAME) ?? Response.error();
    })());
  } else if (allowed.has(request.url)) {
    event.respondWith((async () => {
      const cache = await caches.open(allowed.get(request.url));
      return await cache.match(request.url) ?? fetch(request);
    })());
  }
});
