// Network-first service worker: always fresh when online, still opens offline.
const CACHE = "cairn-v2";
const SHELL = [
  "./",
  "index.html",
  "styles.css",
  "app.js",
  "core.js",
  "md.js",
  "store.js",
  "demo.js",
  "github.js",
  "fonts/instrument-serif-normal.woff2",
  "fonts/instrument-serif-italic.woff2",
  "fonts/geist-normal.woff2",
  "fonts/geist-mono-normal.woff2",
  "manifest.webmanifest",
  "icons/icon.svg",
  "icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.includes("/api/")) return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match("index.html"))),
  );
});
