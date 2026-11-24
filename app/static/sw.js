/* TC Platform service worker — installable PWA + offline-resilient shell.
   Network-first for pages, cache-first fill for static assets. */
const CACHE = "tcp-v3";
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

/* ---- Web Push: notifications addressed to the signed-in user, even when the app
   is closed. Payload {title, body, url, tag} from app/services/push.py. ---- */
function tcSameOriginPath(u) {
  return (typeof u === "string" && u.charAt(0) === "/" && u.charAt(1) !== "/" && u.indexOf("\\") < 0) ? u : "/my-work";
}

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data ? e.data.text() : "" }; }
  const opts = {
    body: d.body || "",
    icon: "/static/img/icon-192.png",
    badge: "/static/img/favicon.png",
    data: { url: tcSameOriginPath(d.url) },
  };
  if (d.tag) opts.tag = d.tag;
  e.waitUntil(self.registration.showNotification(d.title || "TC Platform", opts));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = new URL(tcSameOriginPath(e.notification.data && e.notification.data.url), self.location.origin).href;
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      const win = list.find((c) => new URL(c.url).origin === self.location.origin);
      if (!win) return self.clients.openWindow(target);
      return win.focus()
        .then((w) => (w && w.navigate ? w.navigate(target) : null))
        .catch(() => self.clients.openWindow(target));
    })
  );
});

function tcKeyBytes(b64) {
  const s = (b64 + "===".slice((b64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}

// The browser rotated the subscription: register the new one (best effort; needs
// a signed-in session — otherwise the old one 410s and is dropped server-side).
self.addEventListener("pushsubscriptionchange", (e) => {
  e.waitUntil((async () => {
    const r = await fetch("/push/key", { credentials: "same-origin" });
    if (!r.ok || !(r.headers.get("content-type") || "").includes("json")) return;
    const k = await r.json();
    const sub = await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: tcKeyBytes(k.key) });
    await fetch("/push/subscribe", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": k.csrf },
      body: JSON.stringify(sub.toJSON()),
    });
  })().catch(() => {}));
});
