// Network-first service worker: always fresh when online, still opens offline.
const CACHE = "cairn-v68";
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
  "recap.js",
  "share.js",
  "leaderboard.js",
  "chat.js",
  "relay.js",
  "live.js",
  "autopilot.js",
  "vibelayout.js",
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
  // "reload": straight from the server. The browser's own HTTP cache may still hold the previous version
  // (GitHub Pages lets it reuse a file for 10 minutes), and a new worker must not store that as the new one.
  const fresh = SHELL.map((url) => new Request(url, { cache: "reload" }));
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(fresh)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => !KEEP(key)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

// "no-cache": ask the server every time (an unchanged file is a tiny 304), so an update shows the next
// time the app opens instead of whenever the browser's HTTP cache lets go. Range requests (audio, video)
// keep their own headers.
function freshRequest(request) {
  if (request.headers.has("range")) return request;
  try {
    return new Request(request, { cache: "no-cache" });
  } catch {
    return request;
  }
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.includes("/api/")) return;
  event.respondWith(
    fetch(freshRequest(event.request))
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match("index.html"))),
  );
});
