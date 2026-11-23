// Basis (Finance Console) service worker: keeps the app shell available so the Home Screen app opens instantly.
// Data (/api/) always comes from the network.
const CACHE = 'basis-v4';
const SHELL = ['/', '/static/app.css', '/static/app.js', '/static/fonts/fonts.css', '/static/icon.svg'];
self.addEventListener('install', (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  // network first, cache as a fallback when the server can't be reached
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok && (url.pathname.startsWith('/static/') || url.pathname === '/')) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request).then((m) => m || caches.match('/'))));
});

// notifications sent by the server (fin/push.py)
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'Basis', {
    body: d.body || '', tag: d.tag || undefined, data: { url: d.url || '/' }, icon: '/static/icon-192.png', badge: '/static/icon-192.png' }));
});
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ('focus' in c) { if ('navigate' in c) c.navigate(url); return c.focus(); } }
    return clients.openWindow(url);
  }));
});
