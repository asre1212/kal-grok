// kal-grok service worker. BUILD is stamped at deploy time (hash of the app shell), so every
// code change ships a new SW -> installs, skipWaiting, claims clients -> page reloads once.
// Strategy: network-first for everything same-origin (HTML/JS/CSS/data), cache as offline fallback.
const BUILD = "__BUILD__";
const CACHE = "kal-grok-" + BUILD;
const SHELL = ["./", "./index.html", "./app.js?v=" + BUILD, "./style.css?v=" + BUILD, "./manifest.json",
  "./icons/apple-touch-icon.png", "./icons/icon-192.png", "./icons/icon-512.png"];

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "reload" })))).catch(() => {}));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("message", (e) => { if (e.data === "skipWaiting") self.skipWaiting(); });

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  // navigations can't take a RequestInit; refetch by URL with cache revalidation instead
  const net = req.mode === "navigate" ? fetch(url.href, { cache: "no-cache", credentials: "same-origin" })
    : fetch(req);
  e.respondWith(net.then((r) => {
    if (r && r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
    return r;
  }).catch(() => caches.match(req, { ignoreSearch: url.pathname.includes("/data/") })
    .then((hit) => hit || caches.match("./index.html"))));
});
