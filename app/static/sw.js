/* TC Platform service worker — installable PWA + offline-resilient shell.
   Network-first for pages, cache-first fill for static assets. */
const CACHE = "tcp-v1";
const CORE = [
  "/static/css/tokens.css", "/static/css/app.css", "/static/js/app.js",
  "/static/i18n/en.json", "/static/i18n/ar.json", "/static/i18n/tr.json",
  "/static/img/logo-mark.svg", "/static/img/favicon.svg",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (url.pathname.startsWith("/static/")) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});
