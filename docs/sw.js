// Network-first service worker: always fresh when online, still opens offline.
const CACHE = "cairn-v54";
// Offline voice models live in their own "cairn-voice-*" cache and survive app updates.
const KEEP = (key) => key === CACHE || key.startsWith("cairn-voice");
const SHELL = [
  "./",
  "index.html",
  "styles.css",
  "app.js",
  "ui.js",
  "state.js",
  "views.js",
  "map.js",
  "map3d.js",
  "graph.js",
  "obsidian.js",
  "people.js",
  "proof.js",
  "proofview.js",
  "vibe.js",
  "progress.js",
  "chat.js",
  "relay.js",
  "live.js",
  "autopilot.js",
  "founder.js",
  "projects.js",
  "database.js",
  "slash.js",
  "drawer.js",
  "vault.js",
  "hover.js",
  "tour.js", "rich.js", "canvas.js", "shortcuts.js", "daylog.js", "voicelog.js", "localvoice.js", "plugins.js",
  "actions.js",
  "palette.js",
  "welcome.js",
  "core.js",
  "md.js",
  "store.js",
  "brain-cache.js",
  "demo.js",
  "github.js",
  "auth.js",
  "signin.js",
  "dash.html",
  "dash.js",
  "dash.webmanifest",
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
      .then((keys) => Promise.all(keys.filter((key) => !KEEP(key)).map((key) => caches.delete(key))))
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
