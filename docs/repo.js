// Repo pages: look inside one GitHub repo without leaving Cairn.
//
// A repo, or any folder inside it, opens as three tabs:
//   Look    every picture and video under the folder, its sub-folders as cards with a cover, and its README
//   Pushes  the latest commits that touched the folder; a commit opens to its pictures, files and changes
//   Files   the folder's listing; a file opens as a picture, a video, sound, rendered Markdown or text
//
// The address holds the place (#/projects/<owner>/<repo>/<tab>/<path>), so Back, reload and a link all work.
// Everything is read from the GitHub API with the sign-in Cairn already has. Nothing is written.
//
// A public repo's pictures load straight from raw.githubusercontent.com. Videos and sound, and every file of a
// private repo, are fetched and handed to the page as a blob: the raw host serves video as
// "application/octet-stream", which Safari will not play, and gives nothing from a private repo without a token.
// A blob is only ever shown in <img>, <video> or <audio>, or saved with a download link. It is never opened as a
// page, because a blob page would run as Cairn and could read the sign-in.

import * as gh from "./github.js";
import { escapeHtml as esc } from "./md.js";
import { $, icon, plural, relativeTime, toast } from "./ui.js";
import { state } from "./state.js";
import { renderView } from "./app.js";

const RAW = "https://raw.githubusercontent.com";
const TABS = ["look", "pushes", "files", "file"];
const LOGIN_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const REPO_RE = /^[A-Za-z0-9._-]{1,100}$/;

const IMAGE_TYPES = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", avif: "image/avif", svg: "image/svg+xml", bmp: "image/bmp", ico: "image/x-icon" };
const VIDEO_TYPES = { mp4: "video/mp4", m4v: "video/mp4", mov: "video/quicktime", webm: "video/webm", ogv: "video/ogg" };
const AUDIO_TYPES = { mp3: "audio/mpeg", wav: "audio/wav", ogg: "audio/ogg", m4a: "audio/mp4", aac: "audio/aac", flac: "audio/flac" };
const MARKUP_RE = /\.(md|markdown|mdown|mkd|rst|adoc|asciidoc|org|textile)$/i;
const README_RE = /^readme(\.(md|markdown|mdown|mkd|rst|adoc|org|txt))?$/i;
const BINARY_RE = /\.(zip|gz|tgz|bz2|xz|7z|rar|jar|war|apk|aab|ipa|exe|dll|so|dylib|bin|dat|db|sqlite3?|pdf|docx?|xlsx?|pptx?|woff2?|ttf|otf|eot|psd|ai|sketch|fig|glb|gltf|fbx|obj|stl|step|stp|iges|blend|pyc|class|o|a|wasm|onnx|pt|pth|safetensors|npy|npz|parquet|mcpb|vsix|dmg|pkg|msi|iso|keystore|jks)$/i;

const GALLERY_STEP = 12; // tiles shown before "Show more" (two rows on a wide screen)
const GALLERY_MORE = 48;
const PREVIEW_VIDEO_BYTES = 12 * 1024 * 1024; // a video this small is loaded for its first frame in the grid
const MAX_TEXT_BYTES = 600 * 1024; // a bigger text file is offered as a download, not printed
const PUSHES = 30;
const PATCH_LINES = 400;
const STALE_MS = 5 * 60 * 1000;

const ext = (path) => (/\.([A-Za-z0-9]+)$/.exec(path)?.[1] || "").toLowerCase();
const baseName = (path) => path.split("/").pop();
const parentOf = (path) => path.split("/").slice(0, -1).join("/");
const encodePath = (path) => path.split("/").map(encodeURIComponent).join("/");

// ============================================================ pure helpers (tested)

// "image", "video", "audio" or null, by the file's ending.
export function mediaKind(path) {
  const e = ext(path);
  if (IMAGE_TYPES[e]) return "image";
  if (VIDEO_TYPES[e]) return "video";
  if (AUDIO_TYPES[e]) return "audio";
  return null;
}

const mimeOf = (path) => IMAGE_TYPES[ext(path)] || VIDEO_TYPES[ext(path)] || AUDIO_TYPES[ext(path)] || "";

export function humanSize(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

function safeDecode(part) {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

// "#/projects/juw12/projects/look/apps/hazel" -> { owner, name, tab, path }, or null for any other address.
export function parseRepoHash(hash) {
  const parts = String(hash || "").replace(/^#\/?/, "").split("?")[0].split("/").filter(Boolean).map(safeDecode);
  if (parts[0] !== "projects" || parts.length < 3) return null;
  const [, owner, name, tab = "look", ...rest] = parts;
  if (!LOGIN_RE.test(owner) || !REPO_RE.test(name)) return null;
  const path = rest.flatMap((part) => part.split("/")).filter((part) => part && part !== "." && part !== "..").join("/");
  return { owner, name, tab: TABS.includes(tab) ? tab : "look", path };
}

export function repoHash({ owner, name, tab = "look", path = "" }) {
  const head = `#/projects/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`;
  if (!path && tab === "look") return head;
  return `${head}/${tab}${path ? `/${encodePath(path)}` : ""}`;
}

// How well a picture or video shows what a project is: a banner or a screenshot leads, an app icon trails.
export function mediaScore(path) {
  const lower = path.toLowerCase();
  const name = baseName(lower);
  let score = 0;
  if (/(banner|cover|hero|demo|preview|screenshot|screen|showcase|promo|social)/.test(name)) score += 4;
  if (/(^|\/)(screenshots?[^/]*|media|images?|img|gallery|shots|docs)(\/|$)/.test(lower)) score += 2;
  if (/(icon|favicon|logo|sprite|splash|mipmap|drawable|badge|avatar|maskable|apple-touch|launcher)/.test(lower)) score -= 5;
  if (/(^|\/)(node_modules|vendor|dist|build|fixtures?|tests?|__snapshots__|export|sheets)(\/|$)/.test(lower)) score -= 3;
  if (mediaKind(path) === "video") score += 3;
  if (ext(path) === "ico") score -= 4;
  return score - lower.split("/").length * 0.25;
}

// A git tree ({ path, type, size, sha } items) as folders: what sits in each, and how much is under each.
//   dirs:   folder -> { dirs: [names], files: [{ name, path, size, sha }] }   ("" is the repo root)
//   counts: folder -> { files, images, videos }                               (everything under it)
//   media:  every picture and video as { path, kind, size, sha }
export function indexTree(tree) {
  const dirs = new Map();
  const counts = new Map();
  const media = [];
  const dir = (path) => {
    if (!dirs.has(path)) dirs.set(path, { dirs: [], files: [] });
    return dirs.get(path);
  };
  const count = (path) => {
    if (!counts.has(path)) counts.set(path, { files: 0, images: 0, videos: 0 });
    return counts.get(path);
  };
  dir("");
  count("");
  for (const item of tree || []) {
    if (item.type !== "blob") continue;
    const parts = item.path.split("/");
    const name = parts.pop();
    const kind = mediaKind(item.path);
    let folder = "";
    const bump = (path) => {
      const c = count(path);
      c.files += 1;
      if (kind === "image") c.images += 1;
      if (kind === "video") c.videos += 1;
    };
    bump("");
    for (const part of parts) {
      const next = folder ? `${folder}/${part}` : part;
      if (!dirs.has(next)) dir(folder).dirs.push(part);
      dir(next);
      bump(next);
      folder = next;
    }
    dir(folder).files.push({ name, path: item.path, size: item.size || 0, sha: item.sha || "" });
    if (kind === "image" || kind === "video") media.push({ path: item.path, kind, size: item.size || 0, sha: item.sha || "" });
  }
  for (const entry of dirs.values()) {
    entry.dirs.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
    entry.files.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
  }
  return { dirs, counts, media };
}

// The pictures and videos under one folder, the most telling first.
export function galleryOf(index, folder) {
  const prefix = folder ? `${folder}/` : "";
  return index.media
    .filter((item) => item.path.startsWith(prefix))
    .map((item) => ({ ...item, score: mediaScore(item.path.slice(prefix.length)) }))
    .sort((a, b) => b.score - a.score || a.path.localeCompare(b.path));
}

// A link inside a README, resolved against the folder the README is in:
//   { kind: "anchor", name } | { kind: "repo", path } | { kind: "web", url } | null (not allowed)
export function resolveRef(raw, { owner, name, ref, dir }) {
  const value = String(raw || "").trim();
  if (!value) return null;
  if (value.startsWith("#")) return { kind: "anchor", name: safeDecode(value.slice(1)) };
  if (/^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith("//")) {
    let url;
    try {
      url = new URL(value, "https://github.com/");
    } catch {
      return null;
    }
    if (url.protocol === "mailto:") return { kind: "web", url: url.href };
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    const parts = url.pathname.split("/").filter(Boolean).map(safeDecode);
    const same = parts.length >= 2 && parts[0].toLowerCase() === owner.toLowerCase() && parts[1].toLowerCase() === name.toLowerCase();
    const refParts = ref.split("/");
    const onRef = (from) => refParts.every((part, i) => parts[from + i] === part);
    if (url.hostname === "github.com" && same && ["blob", "raw", "tree"].includes(parts[2]) && onRef(3)) {
      return { kind: "repo", path: parts.slice(3 + refParts.length).join("/") };
    }
    if (url.hostname === "raw.githubusercontent.com" && same && onRef(2)) {
      return { kind: "repo", path: parts.slice(2 + refParts.length).join("/") };
    }
    return { kind: "web", url: url.href };
  }
  const parts = value.startsWith("/") ? [] : dir.split("/").filter(Boolean);
  for (const part of value.split(/[?#]/)[0].split("/").map(safeDecode)) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (!parts.length) return null;
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return { kind: "repo", path: parts.join("/") };
}

const KEEP_TAGS = new Set("a abbr article b blockquote br center code dd del details div dl dt em figcaption figure h1 h2 h3 h4 h5 h6 hr i img input ins kbd li mark ol p picture pre q s samp section small span strike strong sub summary sup table tbody td tfoot th thead tr tt u ul var video".split(" "));
const DROP_TAGS = new Set("script style iframe object embed form textarea select button svg math template noscript link meta base title head audio canvas frame frameset applet source track".split(" "));
const KEEP_ATTRS = {
  "*": ["align", "title", "dir", "lang"],
  img: ["alt", "width", "height"],
  td: ["colspan", "rowspan"],
  th: ["colspan", "rowspan"],
  ol: ["start"],
  details: ["open"],
  video: ["width", "height"],
};

// GitHub's rendered HTML of a README (or any Markdown file), made safe to put in the page and pointed at this
// repo: only known tags and attributes are kept, relative pictures and links are resolved against `ctx.dir`,
// in-page links scroll, and links to the web open in a new tab.
//   ctx: { owner, name, ref, dir, isPrivate, isDir(path) }
// Pictures and videos that need a fetch (see the top of this file) carry data-repo-src and get their address later.
export function cleanHtml(html, ctx) {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  const out = doc.createElement("div");
  const here = repoHash({ owner: ctx.owner, name: ctx.name, tab: ctx.tab || "look", path: ctx.at ?? ctx.dir });

  const copy = (node, parent) => {
    if (node.nodeType === Node.TEXT_NODE) {
      parent.append(doc.createTextNode(node.nodeValue));
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const tag = node.tagName.toLowerCase();
    if (DROP_TAGS.has(tag)) return;
    // GitHub's permalink beside a heading: its name goes on the heading, the link itself is left out.
    if (tag === "a" && node.classList.contains("anchor")) {
      const heading = parent.querySelector(":scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6");
      const id = String(node.getAttribute("id") || "").replace(/^user-content-/, "");
      if (heading && id) heading.setAttribute("id", `repo-anchor-${id}`);
      return;
    }
    if (!KEEP_TAGS.has(tag)) {
      for (const child of node.childNodes) copy(child, parent);
      return;
    }
    const el = doc.createElement(tag);
    for (const attr of [...KEEP_ATTRS["*"], ...(KEEP_ATTRS[tag] || [])]) {
      if (node.hasAttribute(attr)) el.setAttribute(attr, node.getAttribute(attr));
    }
    // A place a link can jump to (<a id="highlights">). Its name gets a prefix, so it can never stand in for
    // an element of Cairn itself.
    const mark = String(node.getAttribute("id") || node.getAttribute("name") || "").replace(/^user-content-/, "");
    if (mark) el.setAttribute("id", `repo-anchor-${mark}`);
    if (tag === "input") {
      if (node.getAttribute("type") !== "checkbox") return;
      el.setAttribute("type", "checkbox");
      el.setAttribute("disabled", "");
      if (node.hasAttribute("checked")) el.setAttribute("checked", "");
    }
    if (tag === "a") {
      const target = resolveRef(node.getAttribute("href"), ctx);
      if (target?.kind === "anchor") {
        el.setAttribute("href", here);
        el.setAttribute("data-repo-anchor", target.name);
      } else if (target?.kind === "repo") {
        const folder = ctx.isDir(target.path);
        el.setAttribute("href", repoHash({ owner: ctx.owner, name: ctx.name, tab: folder ? "look" : "file", path: target.path }));
        el.setAttribute("data-repo-go", "");
      } else if (target?.kind === "web") {
        el.setAttribute("href", target.url);
        el.setAttribute("target", "_blank");
        el.setAttribute("rel", "noopener noreferrer");
      }
    }
    if (tag === "img" || tag === "video") {
      const from = node.getAttribute("src") || (tag === "video" ? node.querySelector("source[src]")?.getAttribute("src") : "");
      const target = resolveRef(from, ctx);
      if (target?.kind === "repo") {
        // A picture of a public repo loads directly; a video, or anything private, is fetched first.
        if (tag === "img" && !ctx.isPrivate) el.setAttribute("src", rawUrl(ctx, target.path));
        else el.setAttribute("data-repo-src", target.path);
      } else if (target?.kind === "web" && /^https?:/.test(target.url)) {
        el.setAttribute("src", target.url);
      } else {
        return;
      }
      if (tag === "img") el.setAttribute("loading", "lazy");
      if (tag === "video") {
        for (const flag of ["controls", "playsinline"]) el.setAttribute(flag, "");
        el.setAttribute("preload", "metadata");
      }
    }
    parent.append(el);
    for (const child of node.childNodes) copy(child, el);
  };

  for (const child of doc.body.childNodes) copy(child, out);
  return out.innerHTML;
}

function rawUrl({ owner, name, ref }, path, at = ref) {
  return `${RAW}/${owner}/${name}/${encodeURIComponent(at)}/${encodePath(path)}`;
}

// ============================================================ loading

const repos = new Map(); // "owner/name" in lower case -> what is known about that repo

function entryFor(target) {
  const key = `${target.owner}/${target.name}`.toLowerCase();
  if (!repos.has(key)) {
    repos.set(key, {
      owner: target.owner,
      name: target.name,
      full: `${target.owner}/${target.name}`,
      meta: null,
      ref: "",
      isPrivate: false,
      error: "",
      loading: false,
      checkedAt: 0,
      index: null,
      truncated: false,
      galleries: new Map(), // folder -> the sorted pictures and videos under it
      shown: new Map(), // folder -> how many gallery tiles are shown
      docs: new Map(), // file path -> { state, html, text }
      pushes: new Map(), // folder -> { state, list, error }
      commits: new Map(), // sha -> { state, data, error }
      open: new Set(), // commits opened on the Pushes tab
      urls: new Map(), // "ref:path" -> promise of a blob address
      ready: new Map(), // "ref:path" -> blob address, once fetched
      lists: new Map(), // list id -> { items, at } for the full-screen viewer
    });
  }
  return repos.get(key);
}

const current = () => (state.view === "projects" ? parseRepoHash(location.hash) : null);

function redraw(entry) {
  const now = current();
  if (now && `${now.owner}/${now.name}`.toLowerCase() === entry.full.toLowerCase()) renderView();
}

function forget(entry) {
  for (const url of entry.ready.values()) URL.revokeObjectURL(url);
  for (const key of ["galleries", "shown", "docs", "pushes", "commits", "urls", "ready", "lists"]) entry[key].clear();
  entry.open.clear();
  entry.index = null;
}

// The repo's facts and its whole file list. Asked again when the copy is older than five minutes, and the file
// list only when the repo was pushed to since.
async function load(entry, { force = false } = {}) {
  if (entry.loading) return;
  // A look that failed is not tried again on every redraw: Refresh, or five minutes, tries again.
  if (!force && (entry.index || entry.error) && Date.now() - entry.checkedAt < STALE_MS) return;
  entry.loading = true;
  entry.error = "";
  try {
    const meta = await (await gh.apiFresh(`/repos/${entry.full}`)).json();
    const changed = force || !entry.meta || meta.pushed_at !== entry.meta.pushed_at || meta.default_branch !== entry.ref;
    entry.meta = meta;
    entry.owner = meta.owner?.login || entry.owner;
    entry.name = meta.name || entry.name;
    entry.full = meta.full_name || entry.full;
    entry.ref = meta.default_branch || "main";
    entry.isPrivate = meta.private === true || Boolean(meta.visibility && meta.visibility !== "public");
    if (changed) {
      let tree = { tree: [], truncated: false };
      try {
        tree = await (await gh.apiFresh(`/repos/${entry.full}/git/trees/${encodeURIComponent(entry.ref)}?recursive=1`)).json();
      } catch (error) {
        // A repo with nothing pushed has no tree: that is an empty repo, not a failure.
        if (!/Not found|changed on GitHub/.test(error.message)) throw error;
      }
      forget(entry);
      entry.index = indexTree(tree.tree);
      entry.truncated = Boolean(tree.truncated);
    }
    entry.checkedAt = Date.now();
  } catch (error) {
    entry.error = error.message || "Couldn't reach GitHub.";
    entry.checkedAt = Date.now();
  } finally {
    entry.loading = false;
    redraw(entry);
  }
}

// At most four files are fetched at a time, the ones asked for last first (what is on screen now).
const waiting = [];
let active = 0;

function limited(task) {
  return new Promise((resolve, reject) => {
    waiting.push({ task, resolve, reject });
    pump();
  });
}

function pump() {
  while (active < 4 && waiting.length) {
    const job = waiting.pop();
    active += 1;
    job.task().then(job.resolve, job.reject).finally(() => {
      active -= 1;
      pump();
    });
  }
}

async function fileBlob(entry, path, at) {
  if (!entry.isPrivate) {
    const response = await fetch(rawUrl(entry, path, at));
    if (!response.ok) throw new Error(`The file answered ${response.status}.`);
    return response.blob();
  }
  const response = await gh.apiFresh(`/repos/${entry.full}/contents/${encodePath(path)}?ref=${encodeURIComponent(at)}`, { accept: "application/vnd.github.raw" });
  return response.blob();
}

// A file as an address the page can show (blob:), or null when it could not be fetched.
function blobUrl(entry, path, at = entry.ref) {
  const key = `${at}:${path}`;
  if (!entry.urls.has(key)) {
    const job = limited(async () => {
      const blob = await fileBlob(entry, path, at);
      // The type comes from the file's ending and is always a picture, video or sound type, never a page.
      const url = URL.createObjectURL(new Blob([blob], { type: mimeOf(path) || "application/octet-stream" }));
      entry.ready.set(key, url);
      return url;
    }).catch(() => {
      entry.urls.delete(key);
      return null;
    });
    entry.urls.set(key, job);
  }
  return entry.urls.get(key);
}

// What can be put in src right now: a direct address, a blob already fetched, or "" (fetch it first).
function srcNow(entry, item, at = entry.ref) {
  if (item.kind === "image" && !entry.isPrivate) return rawUrl(entry, item.path, at);
  return entry.ready.get(`${at}:${item.path}`) || "";
}

function gallery(entry, folder) {
  if (!entry.galleries.has(folder)) entry.galleries.set(folder, galleryOf(entry.index, folder));
  return entry.galleries.get(folder);
}

const cleanContext = (entry, dir, extra = {}) => ({
  owner: entry.owner,
  name: entry.name,
  ref: entry.ref,
  dir,
  isPrivate: entry.isPrivate,
  isDir: (path) => entry.index.dirs.has(path),
  ...extra,
});

// A Markdown (or other markup) file as GitHub renders it. A file GitHub cannot render comes back as its text.
function loadDoc(entry, path, extra) {
  if (entry.docs.has(path)) return entry.docs.get(path);
  const doc = { state: "loading", html: "", text: "" };
  entry.docs.set(path, doc);
  (async () => {
    try {
      const response = await gh.apiFresh(`/repos/${entry.full}/contents/${encodePath(path)}?ref=${encodeURIComponent(entry.ref)}`, { accept: "application/vnd.github.html" });
      doc.html = cleanHtml(await response.text(), cleanContext(entry, parentOf(path), extra));
      doc.state = "done";
    } catch {
      await loadText(entry, path, doc);
    }
    redraw(entry);
  })();
  return doc;
}

async function loadText(entry, path, doc) {
  try {
    const text = await (await limited(() => fileBlob(entry, path, entry.ref))).text();
    // A file with a zero byte in its first part is not text, whatever its name says.
    if (text.slice(0, 8000).includes("\u0000")) doc.state = "binary";
    else {
      doc.text = text;
      doc.state = "text";
    }
  } catch (error) {
    doc.state = "error";
    doc.text = error.message || "Couldn't load this file.";
  }
}

function loadFileText(entry, path) {
  if (entry.docs.has(path)) return entry.docs.get(path);
  const doc = { state: "loading", html: "", text: "" };
  entry.docs.set(path, doc);
  loadText(entry, path, doc).then(() => redraw(entry));
  return doc;
}

function loadPushes(entry, folder) {
  if (entry.pushes.has(folder)) return entry.pushes.get(folder);
  const pushes = { state: "loading", list: [], error: "" };
  entry.pushes.set(folder, pushes);
  (async () => {
    try {
      const query = `sha=${encodeURIComponent(entry.ref)}&per_page=${PUSHES}${folder ? `&path=${encodeURIComponent(folder)}` : ""}`;
      const list = await (await gh.apiFresh(`/repos/${entry.full}/commits?${query}`)).json();
      pushes.list = Array.isArray(list) ? list : [];
      pushes.state = "done";
    } catch (error) {
      pushes.state = "error";
      pushes.error = error.message || "Couldn't load the pushes.";
    }
    redraw(entry);
  })();
  return pushes;
}

function loadCommit(entry, sha) {
  if (entry.commits.has(sha)) return entry.commits.get(sha);
  const commit = { state: "loading", data: null, error: "" };
  entry.commits.set(sha, commit);
  (async () => {
    try {
      commit.data = await (await gh.apiFresh(`/repos/${entry.full}/commits/${sha}`)).json();
      commit.state = "done";
    } catch (error) {
      commit.state = "error";
      commit.error = error.message || "Couldn't load this push.";
    }
    redraw(entry);
  })();
  return commit;
}

// ============================================================ view

const link = (entry, tab, path) => repoHash({ owner: entry.owner, name: entry.name, tab, path });

function crumbs(entry, target) {
  const parts = target.path ? target.path.split("/") : [];
  const tab = target.tab === "file" ? "files" : target.tab;
  const items = [
    `<a href="#/projects" data-repo-go>Projects</a>`,
    `<a href="${esc(link(entry, tab, ""))}" data-repo-go>${esc(entry.owner)}/${esc(entry.name)}</a>`,
    ...parts.slice(0, -1).map((part, i) => `<a href="${esc(link(entry, tab, parts.slice(0, i + 1).join("/")))}" data-repo-go>${esc(part)}</a>`),
  ];
  return `<nav class="repo-crumbs" aria-label="Where you are">${items.join(`<span aria-hidden="true">/</span>`)}</nav>`;
}

function head(entry, target) {
  const meta = entry.meta;
  const folder = target.tab === "file" ? parentOf(target.path) : target.path;
  const title = target.path ? baseName(target.path) : entry.name;
  const githubUrl = meta ? `${meta.html_url}${target.path ? `/${target.tab === "file" ? "blob" : "tree"}/${encodeURIComponent(entry.ref)}/${encodePath(target.path)}` : ""}` : `https://github.com/${entry.full}`;
  const demo = meta ? String(meta.homepage || "").trim() || (meta.has_pages ? `https://${entry.owner}.github.io/${entry.name}/` : "") : "";
  const count = entry.index?.counts.get(folder);
  const chips = meta
    ? [
        meta.private ? `<span>${icon("lock", "sm")} private</span>` : "",
        !target.path && meta.language ? `<span>${esc(meta.language)}</span>` : "",
        !target.path ? `<span>${icon("star", "sm")} ${meta.stargazers_count || 0}</span>` : "",
        // The push time is the repo's; a folder's own pushes are on its Pushes tab.
        !target.path ? `<span>pushed ${esc(relativeTime(Date.parse(meta.pushed_at || "")))}</span>` : "",
        count && target.tab !== "file" ? `<span>${plural(count.files, "file")}</span>` : "",
        /^https?:\/\//.test(demo) && !target.path ? `<a href="${esc(demo)}" target="_blank" rel="noopener noreferrer">${icon("external", "sm")} live demo</a>` : "",
      ].filter(Boolean).join("")
    : "";
  const about = !target.path && meta?.description ? `<p>${esc(meta.description)}</p>` : "";
  return `
    <div class="page-head repo-head">
      <div>
        ${crumbs(entry, target)}
        <h1>${esc(title)}</h1>
        ${about}
        ${chips ? `<div class="proj-meta repo-chips">${chips}</div>` : ""}
      </div>
      <div class="actions">
        <button class="btn" data-repo-refresh ${entry.loading ? "disabled" : ""}>${icon("refresh", "sm")} ${entry.loading ? "Loading…" : "Refresh"}</button>
        <a class="btn" href="${esc(githubUrl)}" target="_blank" rel="noopener noreferrer" title="Open this on github.com">${icon("github", "sm")} GitHub</a>
      </div>
    </div>`;
}

function tabs(entry, target) {
  const folder = target.tab === "file" ? parentOf(target.path) : target.path;
  const on = target.tab === "file" ? "files" : target.tab;
  const tab = (id, label) => `<button type="button" class="${on === id ? "on" : ""}" data-repo-go="${esc(link(entry, id, folder))}" aria-pressed="${on === id}">${label}</button>`;
  return `<div class="segmented repo-tabs" role="group" aria-label="What to show">${tab("look", "Look")}${tab("pushes", "Pushes")}${tab("files", "Files")}</div>`;
}

// One picture or video as a tile that opens the full-screen viewer. `list` is the id of the list it belongs to.
function tile(entry, item, list, index, at = entry.ref) {
  const src = srcNow(entry, item, at);
  const name = baseName(item.path);
  const label = `${item.kind === "video" ? "Video" : "Picture"}: ${item.path}`;
  let inner;
  if (item.kind === "image") {
    inner = src
      ? `<img src="${esc(src)}" alt="" loading="lazy">`
      : `<img alt="" data-repo-src="${esc(item.path)}" data-repo-at="${esc(at)}">`;
  } else if (src || (item.size > 0 && item.size <= PREVIEW_VIDEO_BYTES)) {
    // "#t=0.1" makes a phone draw the first frame instead of a blank box.
    inner = `${src
      ? `<video src="${esc(src)}#t=0.1" muted playsinline preload="metadata" tabindex="-1"></video>`
      : `<video muted playsinline preload="metadata" tabindex="-1" data-repo-src="${esc(item.path)}" data-repo-at="${esc(at)}"></video>`}<span class="repo-play">${icon("play")}</span>`;
  } else {
    inner = `<span class="repo-play">${icon("play")}</span>`;
  }
  return `<button type="button" class="repo-tile ${item.kind}" data-repo-list="${esc(list)}" data-repo-index="${index}" title="${esc(item.path)}" aria-label="${esc(label)}">
    ${inner}<span class="repo-tile-name">${esc(name)}${item.kind === "video" && item.size ? ` · ${humanSize(item.size)}` : ""}</span>
  </button>`;
}

function mediaWord(count) {
  return [count.images ? plural(count.images, "picture") : "", count.videos ? plural(count.videos, "video") : ""].filter(Boolean).join(" · ");
}

function lookTab(entry, target) {
  const folder = target.path;
  const here = entry.index.dirs.get(folder);
  const items = gallery(entry, folder);
  const list = `folder:${folder}`;
  entry.lists.set(list, { items, at: entry.ref });
  const shown = Math.min(items.length, entry.shown.get(folder) || GALLERY_STEP);
  const count = entry.index.counts.get(folder) || { files: 0, images: 0, videos: 0 };

  const media = items.length
    ? `<section class="repo-sec">
        <h2>Pictures and videos <span class="muted">${esc(mediaWord(count))}</span></h2>
        <div class="repo-grid">${items.slice(0, shown).map((item, i) => tile(entry, item, list, i)).join("")}</div>
        ${shown < items.length ? `<button class="btn sm repo-more" data-repo-more="${esc(folder)}">Show ${Math.min(GALLERY_MORE, items.length - shown)} more of ${items.length}</button>` : ""}
      </section>`
    : "";

  const sub = here.dirs.map((name) => {
    const path = folder ? `${folder}/${name}` : name;
    return { name, path, count: entry.index.counts.get(path) || { files: 0, images: 0, videos: 0 } };
  });
  const withMedia = sub.filter((dir) => dir.count.images + dir.count.videos > 0);
  const plain = sub.filter((dir) => dir.count.images + dir.count.videos === 0);
  const card = (dir) => {
    const cover = gallery(entry, dir.path).find((item) => item.kind === "image");
    const src = cover ? srcNow(entry, cover) : "";
    const picture = cover
      ? src ? `<img src="${esc(src)}" alt="" loading="lazy">` : `<img alt="" data-repo-src="${esc(cover.path)}" data-repo-at="${esc(entry.ref)}">`
      : `<span class="repo-play">${icon("play")}</span>`;
    return `<a class="repo-folder" href="${esc(link(entry, "look", dir.path))}" data-repo-go>
      <span class="repo-folder-cover">${picture}</span>
      <strong>${esc(dir.name)}</strong>
      <span class="muted">${esc(mediaWord(dir.count))} · ${plural(dir.count.files, "file")}</span>
    </a>`;
  };
  const folders = sub.length
    ? `<section class="repo-sec">
        <h2>Folders <span class="muted">${sub.length}</span></h2>
        ${withMedia.length ? `<div class="repo-folders">${withMedia.map(card).join("")}</div>` : ""}
        ${plain.length ? `<p class="repo-plain">${withMedia.length ? `<span class="muted">No pictures inside:</span> ` : ""}${plain.map((dir) => `<a href="${esc(link(entry, "files", dir.path))}" data-repo-go>${icon("folder", "sm")} ${esc(dir.name)}</a>`).join("")}</p>` : ""}
      </section>`
    : "";

  const readme = here.files.filter((file) => README_RE.test(file.name)).sort((a, b) => Number(/\.md$/i.test(b.name)) - Number(/\.md$/i.test(a.name)))[0];
  let about = "";
  if (readme) {
    const doc = loadDoc(entry, readme.path, { at: folder });
    const body = doc.state === "loading"
      ? `<p class="muted">Loading ${esc(readme.name)}…</p>`
      : doc.state === "done"
        ? `<div class="prose repo-doc">${doc.html}</div>`
        : doc.state === "text"
          ? `<pre class="repo-code">${esc(doc.text)}</pre>`
          : `<p class="muted">Couldn't load ${esc(readme.name)}.</p>`;
    about = `<section class="repo-sec"><h2>${esc(readme.name)}</h2>${body}</section>`;
  }

  if (!media && !folders && !about) {
    return `<div class="empty-state">${icon("folder")}<h3>Nothing to look at here</h3><p>No pictures, videos or README in this folder. <a href="${esc(link(entry, "files", folder))}" data-repo-go>See its ${plural(here.files.length, "file")}</a>.</p></div>`;
  }
  return `${media}${folders}${about}`;
}

function filesTab(entry, target) {
  const folder = target.path;
  const here = entry.index.dirs.get(folder);
  const rows = [];
  if (folder) rows.push(`<li><a href="${esc(link(entry, "files", parentOf(folder)))}" data-repo-go>${icon("folder", "sm")}<span>..</span><span class="muted">up</span></a></li>`);
  for (const name of here.dirs) {
    const path = folder ? `${folder}/${name}` : name;
    const count = entry.index.counts.get(path);
    rows.push(`<li><a href="${esc(link(entry, "files", path))}" data-repo-go>${icon("folder", "sm")}<span>${esc(name)}</span><span class="muted">${plural(count?.files || 0, "file")}</span></a></li>`);
  }
  for (const file of here.files) {
    rows.push(`<li><a href="${esc(link(entry, "file", file.path))}" data-repo-go>${icon(mediaKind(file.path) ? "play" : "note", "sm")}<span>${esc(file.name)}</span><span class="muted">${humanSize(file.size)}</span></a></li>`);
  }
  return `<ul class="repo-files">${rows.join("")}</ul>`;
}

function findFile(entry, path) {
  return entry.index.dirs.get(parentOf(path))?.files.find((file) => file.path === path) || null;
}

function fileTab(entry, target) {
  const file = findFile(entry, target.path);
  if (!file) return `<div class="empty-state">${icon("note")}<h3>No such file</h3><p><code>${esc(target.path)}</code> is not in ${esc(entry.full)} on ${esc(entry.ref)}.</p></div>`;
  const kind = mediaKind(file.path);
  const bar = `<p class="repo-filebar"><span class="muted">${humanSize(file.size)}</span><button class="btn sm" data-repo-download="${esc(file.path)}">${icon("save", "sm")} Download</button></p>`;
  if (kind === "image" || kind === "video" || kind === "audio") {
    const src = srcNow(entry, { path: file.path, kind });
    const attrs = src ? `src="${esc(src)}"` : `data-repo-src="${esc(file.path)}" data-repo-at="${esc(entry.ref)}" data-repo-now`;
    const tag = kind === "image"
      ? `<img ${attrs} alt="${esc(file.name)}">`
      : kind === "video"
        ? `<video ${attrs} controls playsinline preload="metadata"></video>`
        : `<audio ${attrs} controls preload="metadata"></audio>`;
    return `${bar}<div class="repo-stage ${kind}">${tag}</div>`;
  }
  if (BINARY_RE.test(file.name)) return `${bar}<p class="muted">This file is not text or a picture, so Cairn does not show it. Download saves it to this device.</p>`;
  if (file.size > MAX_TEXT_BYTES) return `${bar}<p class="muted">Too long to show here (${humanSize(file.size)}). Download saves it to this device.</p>`;
  const doc = MARKUP_RE.test(file.name) ? loadDoc(entry, file.path, { tab: "file", at: file.path }) : loadFileText(entry, file.path);
  if (doc.state === "loading") return `${bar}<p class="muted">Loading ${esc(file.name)}…</p>`;
  if (doc.state === "done") return `${bar}<div class="prose repo-doc">${doc.html}</div>`;
  if (doc.state === "text") return `${bar}<pre class="repo-code">${esc(doc.text)}</pre>`;
  if (doc.state === "binary") return `${bar}<p class="muted">This file is not text, so Cairn does not show it. Download saves it to this device.</p>`;
  return `${bar}<p class="muted">${esc(doc.text || "Couldn't load this file.")}</p>`;
}

const STATUS = { added: "new", removed: "deleted", modified: "changed", renamed: "renamed", copied: "copied", changed: "changed" };

function patchHtml(patch) {
  const lines = String(patch).split("\n");
  const body = lines.slice(0, PATCH_LINES).map((line) => {
    const cls = line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "";
    return `<span${cls ? ` class="${cls}"` : ""}>${esc(line) || " "}</span>`;
  }).join("");
  const cut = lines.length > PATCH_LINES ? `<span class="hunk">… ${lines.length - PATCH_LINES} more lines</span>` : "";
  return `<pre class="repo-patch">${body}${cut}</pre>`;
}

function commitDetail(entry, sha) {
  const commit = loadCommit(entry, sha);
  if (commit.state === "loading") return `<div class="repo-push-detail"><p class="muted">Loading this push…</p></div>`;
  if (commit.state === "error") return `<div class="repo-push-detail"><p class="muted">${esc(commit.error)}</p></div>`;
  const data = commit.data;
  const files = data.files || [];
  const body = String(data.commit?.message || "").split("\n").slice(1).join("\n").trim();
  const media = files
    .filter((file) => file.status !== "removed" && ["image", "video"].includes(mediaKind(file.filename)))
    .map((file) => ({ path: file.filename, kind: mediaKind(file.filename), size: findFile(entry, file.filename)?.size || 0 }));
  const list = `commit:${sha}`;
  entry.lists.set(list, { items: media, at: sha });
  const rows = files.map((file) => {
    const label = `<span class="repo-status ${esc(file.status)}">${esc(STATUS[file.status] || file.status)}</span><span class="repo-file-name">${esc(file.filename)}</span><span class="repo-delta"><span class="add">+${file.additions || 0}</span> <span class="del">−${file.deletions || 0}</span></span>`;
    const open = file.status === "removed" ? "" : `<a class="repo-open-file" href="${esc(link(entry, "file", file.filename))}" data-repo-go>Open the file</a>`;
    return file.patch
      ? `<li><details><summary>${label}</summary>${open}${patchHtml(file.patch)}</details></li>`
      : `<li><div class="repo-nopatch">${label}</div></li>`;
  }).join("");
  const stats = data.stats ? `${plural(files.length, "file")} · <span class="add">+${data.stats.additions}</span> <span class="del">−${data.stats.deletions}</span>` : plural(files.length, "file");
  return `<div class="repo-push-detail">
    ${body ? `<p class="repo-push-body">${esc(body)}</p>` : ""}
    <p class="muted repo-push-stats">${stats}${files.length >= 300 ? " (GitHub lists the first 300)" : ""}</p>
    ${media.length ? `<div class="repo-grid small">${media.slice(0, 24).map((item, i) => tile(entry, item, list, i, sha)).join("")}</div>` : ""}
    <ul class="repo-changed">${rows}</ul>
  </div>`;
}

function pushesTab(entry, target) {
  const pushes = loadPushes(entry, target.path);
  if (pushes.state === "loading") return `<p class="muted">Loading the latest pushes…</p>`;
  if (pushes.state === "error") return `<p class="muted">${esc(pushes.error)}</p>`;
  if (!pushes.list.length) return `<div class="empty-state">${icon("git")}<h3>No pushes here yet</h3><p>Nothing has been committed ${target.path ? "to this folder" : "to this repo"}.</p></div>`;
  const rows = pushes.list.map((item) => {
    const message = String(item.commit?.message || "").split("\n")[0];
    const who = item.author?.login || item.commit?.author?.name || "";
    const when = Date.parse(item.commit?.author?.date || item.commit?.committer?.date || "");
    const open = entry.open.has(item.sha);
    return `<li class="repo-push${open ? " open" : ""}">
      <button type="button" class="repo-push-row" data-repo-commit="${esc(item.sha)}" aria-expanded="${open}">
        <span class="repo-push-msg">${esc(message)}</span>
        <span class="muted repo-push-meta">${esc(who)}${who ? " · " : ""}${esc(relativeTime(when))} · <code>${esc(item.sha.slice(0, 7))}</code></span>
      </button>
      ${open ? commitDetail(entry, item.sha) : ""}
    </li>`;
  }).join("");
  const more = pushes.list.length >= PUSHES ? `<p class="muted repo-note">The latest ${PUSHES} pushes${target.path ? " that touched this folder" : ""}.</p>` : "";
  return `<ul class="repo-pushes">${rows}</ul>${more}`;
}

// The page for the repo in the address (see parseRepoHash). Called by the Projects view.
export function repoView(target) {
  const entry = entryFor(target);
  load(entry);
  if (!entry.index) {
    const message = entry.error
      ? `<div class="empty-state">${icon("github")}<h3>Couldn't open ${esc(entry.full)}</h3><p>${esc(entry.error)} A private repo opens only when you are signed in as someone who can read it.</p></div>`
      : `<div class="empty-state">${icon("github")}<h3>Opening ${esc(entry.full)}…</h3><p>Reading its files from GitHub.</p></div>`;
    return `<div class="repo">${head(entry, target)}${message}</div>`;
  }
  // A folder that is really a file (or the other way round) is shown as what it is.
  let shown = target;
  if (target.tab !== "file" && target.path && !entry.index.dirs.has(target.path) && findFile(entry, target.path)) shown = { ...target, tab: "file" };
  if (target.tab === "file" && entry.index.dirs.has(target.path)) shown = { ...target, tab: "files" };
  let body;
  if (shown.tab !== "file" && !entry.index.dirs.has(shown.path)) {
    body = `<div class="empty-state">${icon("folder")}<h3>No such folder</h3><p><code>${esc(shown.path)}</code> is not in ${esc(entry.full)} on ${esc(entry.ref)}.</p></div>`;
  } else {
    body = { look: lookTab, pushes: pushesTab, files: filesTab, file: fileTab }[shown.tab](entry, shown);
  }
  const notes = [
    entry.error ? `<p class="muted repo-note">${esc(entry.error)} Showing what was loaded before.</p>` : "",
    entry.truncated ? `<p class="muted repo-note">This repo is so large that GitHub cut its file list short, so some files are missing here.</p>` : "",
  ].join("");
  return `<div class="repo" data-repo="${esc(entry.full)}" data-repo-tab="${esc(shown.tab)}">${head(entry, shown)}${tabs(entry, shown)}${notes}<div class="repo-body">${body}</div></div>`;
}

// ============================================================ moving around

let rankingScroll = null; // where the ranking was scrolled to when a repo was opened from it

function scroller() {
  return $("#view");
}

export function goTo(hash) {
  if (!parseRepoHash(location.hash) && parseRepoHash(hash)) rankingScroll = { view: scroller()?.scrollTop || 0, page: window.scrollY };
  if (location.hash !== hash) history.pushState(null, "", hash);
  renderView();
  if (parseRepoHash(hash)) {
    if (scroller()) scroller().scrollTop = 0;
    window.scrollTo(0, 0);
  }
}

export function openRepo(owner, name) {
  goTo(repoHash({ owner, name }));
}

// ============================================================ the full-screen viewer

let viewer = null;

function closeViewer() {
  if (!viewer) return;
  viewer.box.remove();
  viewer = null;
}

function showInViewer() {
  const { box, entry, items, at } = viewer;
  const item = items[viewer.index];
  const stage = $(".repo-lb-stage", box);
  const turn = (viewer.turn += 1);
  stage.innerHTML = item.kind === "video"
    ? `<video controls autoplay playsinline></video><p class="repo-lb-wait">Loading the video${item.size ? ` (${humanSize(item.size)})` : ""}…</p>`
    : `<img alt="${esc(baseName(item.path))}">`;
  const media = stage.firstElementChild;
  const ready = srcNow(entry, item, at);
  const put = (src) => {
    if (!viewer || viewer.turn !== turn) return;
    $(".repo-lb-wait", stage)?.remove();
    if (src) media.src = src;
    else stage.innerHTML = `<p class="repo-lb-wait">Couldn't load ${esc(baseName(item.path))}.</p>`;
  };
  if (ready) put(ready);
  else blobUrl(entry, item.path, at).then(put);
  $(".repo-lb-cap", box).innerHTML = `<a href="${esc(link(entry, "file", item.path))}" data-repo-go>${esc(item.path)}</a><span>${viewer.index + 1} of ${items.length}</span>`;
  for (const nav of box.querySelectorAll(".repo-lb-nav")) nav.hidden = items.length < 2;
}

function step(by) {
  if (!viewer) return;
  viewer.index = (viewer.index + by + viewer.items.length) % viewer.items.length;
  showInViewer();
}

function openViewer(entry, listId, index) {
  const list = entry.lists.get(listId);
  if (!list?.items.length) return;
  closeViewer();
  const box = document.createElement("div");
  box.className = "repo-lightbox";
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", "Pictures and videos");
  box.innerHTML = `
    <button type="button" class="repo-lb-close" data-lb="close" aria-label="Close">${icon("x")}</button>
    <button type="button" class="repo-lb-nav prev" data-lb="prev" aria-label="Previous">‹</button>
    <div class="repo-lb-stage"></div>
    <button type="button" class="repo-lb-nav next" data-lb="next" aria-label="Next">›</button>
    <div class="repo-lb-cap"></div>`;
  document.body.append(box);
  viewer = { box, entry, items: list.items, at: list.at, index: Math.max(0, Math.min(list.items.length - 1, index)), turn: 0 };
  let startX = null;
  box.addEventListener("touchstart", (event) => {
    startX = event.touches.length === 1 ? event.touches[0].clientX : null;
  }, { passive: true });
  box.addEventListener("touchend", (event) => {
    if (startX === null) return;
    const moved = event.changedTouches[0].clientX - startX;
    startX = null;
    if (Math.abs(moved) > 50) step(moved < 0 ? 1 : -1);
  });
  showInViewer();
  $(".repo-lb-close", box).focus();
}

// ============================================================ events

async function download(entry, path) {
  toast(`Getting ${baseName(path)}…`);
  try {
    const blob = await limited(() => fileBlob(entry, path, entry.ref));
    // Saved, never opened: the type says "a file", so no browser shows it as a page of this site.
    const url = URL.createObjectURL(new Blob([blob], { type: "application/octet-stream" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = baseName(path);
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (error) {
    toast(error.message || "Couldn't download that file.", { error: true });
  }
}

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;

  if (viewer && viewer.box.contains(target)) {
    const action = target.closest("[data-lb]")?.dataset.lb;
    const go = target.closest("[data-repo-go]");
    if (go) {
      event.preventDefault();
      closeViewer();
      return goTo(go.getAttribute("href"));
    }
    if (action === "prev") return step(-1);
    if (action === "next") return step(1);
    // A click on the dark surround closes; a click on the picture or the video's controls does not.
    if (action === "close" || target === viewer.box || target.classList.contains("repo-lb-stage")) return closeViewer();
    return undefined;
  }

  const go = target.closest("[data-repo-go]");
  if (go) {
    // Ctrl, Cmd, Shift or the middle button open a new tab, as with any link.
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button) return undefined;
    event.preventDefault();
    return goTo(go.getAttribute("href") || go.dataset.repoGo);
  }

  const here = current();
  if (!here) return undefined;
  const entry = entryFor(here);

  const anchor = target.closest("[data-repo-anchor]");
  if (anchor) {
    event.preventDefault();
    document.getElementById(`repo-anchor-${anchor.dataset.repoAnchor}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    return undefined;
  }
  const opener = target.closest("[data-repo-list]");
  if (opener) return openViewer(entry, opener.dataset.repoList, Number(opener.dataset.repoIndex));
  const more = target.closest("[data-repo-more]");
  if (more) {
    const folder = more.dataset.repoMore;
    entry.shown.set(folder, (entry.shown.get(folder) || GALLERY_STEP) + GALLERY_MORE);
    return renderView();
  }
  const commit = target.closest("[data-repo-commit]");
  if (commit) {
    const sha = commit.dataset.repoCommit;
    if (entry.open.has(sha)) entry.open.delete(sha);
    else entry.open.add(sha);
    return renderView();
  }
  const save = target.closest("[data-repo-download]");
  if (save) return download(entry, save.dataset.repoDownload);
  if (target.closest("[data-repo-refresh]")) {
    load(entry, { force: true });
    return renderView();
  }
  return undefined;
});

document.addEventListener("keydown", (event) => {
  if (!viewer) return;
  if (event.key === "Escape") closeViewer();
  else if (event.key === "ArrowLeft") step(-1);
  else if (event.key === "ArrowRight") step(1);
  else return;
  event.preventDefault();
  event.stopPropagation();
}, true);

// Back or Forward while the viewer is open closes it: the page under it has changed.
window.addEventListener("popstate", closeViewer);

let watcher = null;

function fill(el, entry) {
  const path = el.dataset.repoSrc;
  const at = el.dataset.repoAt || entry.ref;
  el.removeAttribute("data-repo-src");
  blobUrl(entry, path, at).then((url) => {
    if (!url || !el.isConnected) return;
    el.src = el.tagName === "VIDEO" && !el.controls ? `${url}#t=0.1` : url;
  });
}

// After every draw of the Projects view: pictures and videos that need a fetch get it when they come near
// the screen, and the ranking returns to where it was scrolled when a repo page is left.
export function wireRepo() {
  const here = current();
  if (!here) {
    if (rankingScroll) {
      const back = rankingScroll;
      rankingScroll = null;
      requestAnimationFrame(() => {
        if (scroller()) scroller().scrollTop = back.view;
        window.scrollTo(0, back.page);
      });
    }
    return;
  }
  const entry = entryFor(here);
  watcher?.disconnect();
  watcher = new IntersectionObserver((seen) => {
    for (const item of seen) {
      if (!item.isIntersecting) continue;
      watcher.unobserve(item.target);
      fill(item.target, entry);
    }
  }, { rootMargin: "300px" });
  for (const el of document.querySelectorAll("#view [data-repo-src]")) {
    if (el.hasAttribute("data-repo-now")) fill(el, entry);
    else watcher.observe(el);
  }
}
