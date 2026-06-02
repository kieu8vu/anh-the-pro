// sw.js — Service Worker (PWA offline cache)
// ============================================
const CACHE = "anh-the-pro-v1";
const PRECACHE = [
  "/",
  "/index.html",
  "/manifest.json",
  "/src/app.js",
  "/src/utils/api.js",
  "/src/utils/canvas.js",
  "/src/components/camera.js",
  "/src/components/preview.js",
];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE))
  );
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", e => {
  // Network first cho API, cache first cho assets
  if (e.request.url.includes("/api/")) {
    e.respondWith(fetch(e.request).catch(() => new Response("Offline", { status: 503 })));
  } else {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
  }
});
