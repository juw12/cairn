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
import { renderView, setView } from "./app.js";
import { closeDrawer } from "./drawer.js";

const RAW = "https://raw.githubusercontent.com";
const TABS = ["look", "pushes", "files", "file", "releases", "talk"];
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
const LEAD_VIDEOS = 6;
const PREVIEW_VIDEO_BYTES = 12 * 1024 * 1024; // a video this small is fetched for a picture of its first moments
const POSTER_WIDTH = 640;
const POSTER_WAIT_MS = 20000;
const MAX_TEXT_BYTES = 600 * 1024; // a bigger text file is offered as a download, not printed
const PUSHES = 30;
const RELEASES = 20;
const TALK = 40;
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
// What follows a "?" is optional: ref (a branch or tag other than the default one), commit (one push to show on
// the Pushes tab) and state ("closed": the closed issues and pull requests).
export function parseRepoHash(hash) {
  const [before, query = ""] = String(hash || "").replace(/^#\/?/, "").split("?");
  const parts = before.split("/").filter(Boolean).map(safeDecode);
  if (parts[0] !== "projects" || parts.length < 3) return null;
  const [, owner, name, tab = "look", ...rest] = parts;
  if (!LOGIN_RE.test(owner) || !REPO_RE.test(name)) return null;
  const path = rest.flatMap((part) => part.split("/")).filter((part) => part && part !== "." && part !== "..").join("/");
  const target = { owner, name, tab: TABS.includes(tab) ? tab : "look", path };
  const asked = new URLSearchParams(query);
  const ref = (asked.get("ref") || "").trim();
  if (ref && !/[\s~^:?*[\\]|\.\.|^\/|\/$/.test(ref)) target.ref = ref;
  if (/^[0-9a-f]{7,40}$/i.test(asked.get("commit") || "")) target.commit = asked.get("commit").toLowerCase();
  if (asked.get("state") === "closed") target.state = "closed";
  return target;
}

export function repoHash({ owner, name, tab = "look", path = "", ref = "", commit = "", state = "" }) {
  const head = `#/projects/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`;
  const query = [ref ? `ref=${encodeURIComponent(ref)}` : "", commit ? `commit=${commit}` : "", state ? `state=${state}` : ""].filter(Boolean).join("&");
  const place = !path && tab === "look" ? head : `${head}/${tab}${path ? `/${encodePath(path)}` : ""}`;
  return query ? `${place}?${query}` : place;
}

// First parts of a github.com address that are GitHub's own pages, not a person or an organisation.
const NOT_OWNERS = new Set("about account apps codespaces collections contact copilot customer-stories dashboard discussions enterprise events explore features gist issues join login logout marketplace new notifications organizations orgs pricing pulls readme search security sessions settings signup site sponsors stars team topics trending user-attachments users watching".split(" "));

// A github.com address as a place in Cairn, or null when Cairn has no page for it:
//   github.com/juw12/cairn                      -> the repo
//   .../tree/main/docs, .../blob/main/README.md -> that folder or file (the branch is the part after tree/ or blob/)
//   .../pull/72, .../issues/5                   -> that pull request or issue
//   .../commit/<sha>                            -> that push
//   .../commits, .../releases, .../issues, .../pulls -> those lists
// A download (.../releases/download/...), "new issue" and everything else stay on GitHub.
export function githubTarget(address) {
  let url;
  try {
    url = new URL(address);
  } catch {
    return null;
  }
  if (!/^https?:$/.test(url.protocol) || !/^(www\.)?github\.com$/.test(url.hostname)) return null;
  const parts = url.pathname.split("/").filter(Boolean).map(safeDecode);
  if (parts.length < 2) return null;
  const [owner, repo, kind, ...rest] = parts;
  const name = repo.replace(/\.git$/i, "");
  if (!LOGIN_RE.test(owner) || NOT_OWNERS.has(owner.toLowerCase()) || !REPO_RE.test(name)) return null;
  const base = { owner, name };
  const branch = (ref) => (ref && ref !== "HEAD" ? ref : "");
  if (!kind) return repoHash(base);
  if (kind === "tree" && rest.length) return repoHash({ ...base, tab: "look", path: rest.slice(1).join("/"), ref: branch(rest[0]) });
  if (kind === "blob" && rest.length > 1) return repoHash({ ...base, tab: "file", path: rest.slice(1).join("/"), ref: branch(rest[0]) });
  if ((kind === "pull" || kind === "issues") && /^\d+$/.test(rest[0] || "")) return repoHash({ ...base, tab: "talk", path: rest[0] });
  if ((kind === "pulls" || kind === "issues") && !rest.length) return repoHash({ ...base, tab: "talk" });
  if (kind === "commit" && /^[0-9a-f]{7,40}$/i.test(rest[0] || "")) return repoHash({ ...base, tab: "pushes", commit: rest[0].toLowerCase() });
  if (kind === "commits" && rest.length <= 1) return repoHash({ ...base, tab: "pushes", ref: branch(rest[0]) });
  if (kind === "releases" && (!rest.length || rest[0] === "tag" || (rest[0] === "latest" && rest.length === 1))) return repoHash({ ...base, tab: "releases" });
  return null;
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

// The pictures and videos under one folder, the most telling first. Up to six videos lead, whatever their
// names: a folder of forty covers must not push every video off the first screen.
export function galleryOf(index, folder) {
  const prefix = folder ? `${folder}/` : "";
  const sorted = index.media
    .filter((item) => item.path.startsWith(prefix))
    .map((item) => ({ ...item, score: mediaScore(item.path.slice(prefix.length)) }))
    .sort((a, b) => b.score - a.score || a.path.localeCompare(b.path));
  const lead = sorted.filter((item) => item.kind === "video").slice(0, LEAD_VIDEOS);
  return [...lead, ...sorted.filter((item) => !lead.includes(item))];
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
//   ctx: { owner, name, ref, dir, isPrivate, isDir(path), branch (set when ref is not the default branch) }
// Pictures and videos that need a fetch (see the top of this file) carry data-repo-src and get their address later.
export function cleanHtml(html, ctx) {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  const out = doc.createElement("div");
  const here = repoHash({ owner: ctx.owner, name: ctx.name, tab: ctx.tab || "look", path: ctx.at ?? ctx.dir, ref: ctx.branch });

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
        el.setAttribute("href", repoHash({ owner: ctx.owner, name: ctx.name, tab: folder ? "look" : "file", path: target.path, ref: ctx.branch }));
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

const repos = new Map(); // "owner/name" in lower case + "@" + branch -> what is known about that branch of the repo
const shared = new Map(); // "owner/name" in lower case -> what does not depend on the branch

// Branches, releases, issues and pull requests belong to the repo, whichever branch is being looked at.
function sharedFor(entry) {
  const key = `${entry.owner}/${entry.name}`.toLowerCase();
  if (!shared.has(key)) {
    shared.set(key, {
      branches: null, // { state, list, error }
      branchesOpen: false,
      releases: null, // { state, list, error }
      talk: new Map(), // "open" | "closed" -> { state, list, error }
      threads: new Map(), // issue or pull request number -> { state, item, comments, pull, files, error }
    });
  }
  return shared.get(key);
}

function entryFor(target) {
  const key = `${target.owner}/${target.name}`.toLowerCase() + `@${target.ref || ""}`;
  if (!repos.has(key)) {
    repos.set(key, {
      owner: target.owner,
      name: target.name,
      full: `${target.owner}/${target.name}`,
      wanted: target.ref || "", // the branch or tag the address asks for; "" is the default branch
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
      only: new Map(), // folder -> "image" or "video", when the gallery shows one kind
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
  if (now && `${now.owner}/${now.name}`.toLowerCase() === `${entry.owner}/${entry.name}`.toLowerCase()) renderView();
}

function forget(entry) {
  for (const url of entry.ready.values()) URL.revokeObjectURL(url);
  for (const key of ["galleries", "shown", "only", "docs", "pushes", "commits", "urls", "ready", "lists"]) entry[key].clear();
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
    const ref = entry.wanted || meta.default_branch || "main";
    const changed = force || !entry.meta || meta.pushed_at !== entry.meta.pushed_at || ref !== entry.ref;
    entry.meta = meta;
    entry.owner = meta.owner?.login || entry.owner;
    entry.name = meta.name || entry.name;
    entry.full = meta.full_name || entry.full;
    entry.ref = ref;
    entry.isPrivate = meta.private === true || Boolean(meta.visibility && meta.visibility !== "public");
    if (changed) {
      let tree = { tree: [], truncated: false };
      try {
        tree = await (await gh.apiFresh(`/repos/${entry.full}/git/trees/${encodeURIComponent(entry.ref)}?recursive=1`)).json();
      } catch (error) {
        if (!/Not found|changed on GitHub/.test(error.message)) throw error;
        // A repo with nothing pushed has no tree: that is an empty repo, not a failure. A branch that was asked
        // for by name and is not there is one.
        if (entry.wanted) throw new gh.GitHubError(`${entry.full} has no branch or tag named ${entry.wanted}.`);
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

// A still picture from a video (a blob address), or null when the browser gives no frame in time.
// Browsers differ in what makes a video give up a frame, so both ways are asked for at once: a jump a little way
// in (enough on a computer) and playing it muted (a phone loads nothing of a video until it plays). The video sits
// in the page while this runs, two pixels large and all but invisible, because some browsers will not play a
// video that is not in the page. It is stopped and emptied afterwards.
function framePicture(url) {
  return new Promise((resolve) => {
    const holder = document.createElement("div");
    holder.setAttribute("aria-hidden", "true");
    holder.style.cssText = "position:fixed;left:0;top:0;width:2px;height:2px;overflow:hidden;opacity:0.01;pointer-events:none";
    const video = document.createElement("video");
    video.muted = true;
    video.defaultMuted = true;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.preload = "auto";
    video.tabIndex = -1;
    holder.append(video);
    let over = false;
    let jumped = false;
    const finish = (result) => {
      if (over) return;
      over = true;
      clearTimeout(timer);
      video.pause();
      video.removeAttribute("src");
      video.load();
      holder.remove();
      resolve(result);
    };
    const timer = setTimeout(() => finish(null), POSTER_WAIT_MS);
    // A little way in: the very first frame is often black.
    const moment = () => Math.min(0.3, (Number.isFinite(video.duration) ? video.duration : 1) / 2);
    const draw = () => {
      // Only a frame that is really there: before that, a canvas takes a black picture without complaint.
      if (over || video.readyState < 2 || !video.videoWidth) return;
      if (!jumped && video.currentTime < moment() * 0.9) return;
      const scale = Math.min(1, POSTER_WIDTH / video.videoWidth);
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      try {
        canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => finish(blob ? URL.createObjectURL(blob) : null), "image/jpeg", 0.8);
      } catch {
        finish(null);
      }
    };
    video.addEventListener("loadedmetadata", () => {
      if (video.currentTime < moment()) video.currentTime = moment();
    });
    video.addEventListener("seeked", () => {
      jumped = true;
      draw();
    });
    for (const name of ["loadeddata", "canplay", "timeupdate", "ended"]) video.addEventListener(name, draw);
    video.addEventListener("error", () => finish(null));
    document.body.append(holder);
    video.src = url;
    video.play().catch(() => {});
  });
}

// Pictures of videos are made one at a time: one player in use, never a pile of them.
let posterLine = Promise.resolve();

function posterUrl(entry, path, at = entry.ref) {
  const key = `poster:${at}:${path}`;
  if (!entry.urls.has(key)) {
    const job = posterLine.then(async () => {
      const url = await blobUrl(entry, path, at);
      const poster = url ? await framePicture(url) : null;
      if (poster) entry.ready.set(key, poster);
      return poster;
    }).catch(() => null);
    posterLine = job;
    entry.urls.set(key, job);
  }
  return entry.urls.get(key);
}

function loadBranches(entry) {
  const repo = sharedFor(entry);
  if (repo.branches) return repo.branches;
  const branches = { state: "loading", list: [], error: "" };
  repo.branches = branches;
  (async () => {
    try {
      const list = await (await gh.apiFresh(`/repos/${entry.full}/branches?per_page=100`)).json();
      branches.list = (Array.isArray(list) ? list : []).map((branch) => branch.name);
      branches.state = "done";
    } catch (error) {
      branches.state = "error";
      branches.error = error.message || "Couldn't load the branches.";
    }
    redraw(entry);
  })();
  return branches;
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
  isDir: (path) => Boolean(entry.index?.dirs.has(path)),
  branch: entry.wanted,
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

const HTML_JSON = "application/vnd.github.html+json"; // GitHub adds body_html: the text as it renders it

// A list from GitHub that may fail without taking the page with it.
async function listOr(path, fallback = []) {
  try {
    const list = await (await gh.apiFresh(path, { accept: HTML_JSON })).json();
    return Array.isArray(list) ? list : fallback;
  } catch {
    return fallback;
  }
}

function loadReleases(entry) {
  const repo = sharedFor(entry);
  if (repo.releases) return repo.releases;
  const releases = { state: "loading", list: [], error: "" };
  repo.releases = releases;
  (async () => {
    try {
      const list = await (await gh.apiFresh(`/repos/${entry.full}/releases?per_page=${RELEASES}`, { accept: HTML_JSON })).json();
      releases.list = Array.isArray(list) ? list : [];
      releases.state = "done";
    } catch (error) {
      releases.state = "error";
      releases.error = error.message || "Couldn't load the releases.";
    }
    redraw(entry);
  })();
  return releases;
}

// Issues and pull requests together, the ones that changed last first. `which` is "open" or "closed".
function loadTalk(entry, which) {
  const repo = sharedFor(entry);
  if (repo.talk.has(which)) return repo.talk.get(which);
  const talk = { state: "loading", list: [], error: "" };
  repo.talk.set(which, talk);
  (async () => {
    try {
      const list = await (await gh.apiFresh(`/repos/${entry.full}/issues?state=${which}&sort=updated&direction=desc&per_page=${TALK}`)).json();
      talk.list = Array.isArray(list) ? list : [];
      talk.state = "done";
    } catch (error) {
      talk.state = "error";
      talk.error = error.message || "Couldn't load the issues and pull requests.";
    }
    redraw(entry);
  })();
  return talk;
}

// One issue or pull request with everything said on it. A pull request also brings its facts (branches, merged
// or not), its reviews, the comments left on lines and the files it changes.
function loadThread(entry, number) {
  const repo = sharedFor(entry);
  if (repo.threads.has(number)) return repo.threads.get(number);
  const thread = { state: "loading", item: null, said: [], pull: null, files: [], error: "" };
  repo.threads.set(number, thread);
  (async () => {
    try {
      const base = `/repos/${entry.full}`;
      thread.item = await (await gh.apiFresh(`${base}/issues/${number}`, { accept: HTML_JSON })).json();
      const isPull = Boolean(thread.item.pull_request);
      const [comments, pull, reviews, onLines, files] = await Promise.all([
        listOr(`${base}/issues/${number}/comments?per_page=100`),
        isPull ? gh.apiFresh(`${base}/pulls/${number}`).then((response) => response.json()).catch(() => null) : null,
        isPull ? listOr(`${base}/pulls/${number}/reviews?per_page=100`) : [],
        isPull ? listOr(`${base}/pulls/${number}/comments?per_page=100`) : [],
        isPull ? listOr(`${base}/pulls/${number}/files?per_page=100`) : [],
      ]);
      thread.pull = pull;
      thread.files = files;
      const verdict = { APPROVED: "approved", CHANGES_REQUESTED: "asked for changes", DISMISSED: "review dismissed" };
      thread.said = [
        { who: thread.item.user?.login, when: thread.item.created_at, html: thread.item.body_html, note: "opened this", first: true },
        ...comments.map((c) => ({ who: c.user?.login, when: c.created_at, html: c.body_html })),
        // A review with nothing written and no verdict is only the wrapper of its line comments.
        ...reviews.filter((r) => r.body_html || verdict[r.state]).map((r) => ({ who: r.user?.login, when: r.submitted_at, html: r.body_html, note: verdict[r.state] || "reviewed" })),
        ...onLines.map((c) => ({ who: c.user?.login, when: c.created_at, html: c.body_html, note: `on ${c.path}${c.line ? ` line ${c.line}` : ""}` })),
      ].sort((a, b) => Number(Boolean(b.first)) - Number(Boolean(a.first)) || Date.parse(a.when || "") - Date.parse(b.when || ""));
      thread.state = "done";
    } catch (error) {
      thread.state = "error";
      thread.error = error.message || "Couldn't load this.";
    }
    redraw(entry);
  })();
  return thread;
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

const link = (entry, tab, path, extra = {}) => repoHash({ owner: entry.owner, name: entry.name, tab, path, ref: entry.wanted, ...extra });

// Releases, issues and pull requests belong to the whole repo, not to a folder in it.
const repoWide = (tab) => tab === "releases" || tab === "talk";

function crumbs(entry, target) {
  const parts = target.path && !repoWide(target.tab) ? target.path.split("/") : [];
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
  const wide = repoWide(target.tab);
  const place = wide ? "" : target.path;
  const folder = target.tab === "file" ? parentOf(place) : place;
  const title = place ? baseName(place) : entry.name;
  const onGithub = target.tab === "releases"
    ? "/releases"
    : target.tab === "talk"
      ? `/issues${/^\d+$/.test(target.path) ? `/${target.path}` : ""}`
      : place || entry.wanted
        ? `/${target.tab === "file" ? "blob" : "tree"}/${encodePath(entry.ref)}${place ? `/${encodePath(place)}` : ""}`
        : "";
  const githubUrl = `${meta ? meta.html_url : `https://github.com/${entry.full}`}${onGithub}`;
  const demo = meta ? String(meta.homepage || "").trim() || (meta.has_pages ? `https://${entry.owner}.github.io/${entry.name}/` : "") : "";
  const count = entry.index?.counts.get(folder);
  const branches = sharedFor(entry);
  const chips = meta
    ? [
        meta.private ? `<span>${icon("lock", "sm")} private</span>` : "",
        entry.ref && !wide ? `<button type="button" class="repo-branch" data-repo-branches aria-expanded="${branches.branchesOpen}" title="Look at another branch">${icon("branch", "sm")} ${esc(entry.ref)}</button>` : "",
        !target.path && meta.language ? `<span>${esc(meta.language)}</span>` : "",
        !target.path ? `<span>${icon("star", "sm")} ${meta.stargazers_count || 0}</span>` : "",
        // The push time is the repo's; a folder's own pushes are on its Pushes tab.
        !target.path ? `<span>pushed ${esc(relativeTime(Date.parse(meta.pushed_at || "")))}</span>` : "",
        count && target.tab !== "file" && !wide ? `<span>${plural(count.files, "file")}</span>` : "",
        /^https?:\/\//.test(demo) && !target.path ? `<a href="${esc(demo)}" target="_blank" rel="noopener noreferrer">${icon("external", "sm")} live demo</a>` : "",
      ].filter(Boolean).join("")
    : "";
  const about = !place && meta?.description ? `<p>${esc(meta.description)}</p>` : "";
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
        <a class="btn" href="${esc(githubUrl)}" data-github target="_blank" rel="noopener noreferrer" title="Open this on github.com">${icon("github", "sm")} GitHub</a>
      </div>
    </div>${branches.branchesOpen && !wide ? branchList(entry, target) : ""}`;
}

// The repo's branches as links. Asked of GitHub only when the branch chip is pressed.
function branchList(entry, target) {
  const known = loadBranches(entry);
  if (known.state === "loading") return `<p class="muted repo-branches">Loading the branches…</p>`;
  if (known.state === "error") return `<p class="muted repo-branches">${esc(known.error)}</p>`;
  const first = entry.meta?.default_branch || "";
  const names = [...known.list].sort((a, b) => Number(b === first) - Number(a === first) || a.localeCompare(b));
  const tab = target.tab === "file" ? "files" : target.tab;
  const items = names.map((name) => {
    const on = name === entry.ref;
    const href = repoHash({ owner: entry.owner, name: entry.name, tab, ref: name === first ? "" : name });
    return `<a class="${on ? "on" : ""}" href="${esc(href)}" data-repo-go ${on ? 'aria-current="true"' : ""}>${esc(name)}${name === first ? ` <span class="muted">default</span>` : ""}</a>`;
  }).join("");
  const more = known.list.length >= 100 ? `<span class="muted">the first 100</span>` : "";
  return `<nav class="repo-branches" aria-label="Branches">${items}${more}</nav>`;
}

function tabs(entry, target) {
  const folder = repoWide(target.tab) ? "" : target.tab === "file" ? parentOf(target.path) : target.path;
  const on = target.tab === "file" ? "files" : target.tab;
  const tab = (id, label) => `<button type="button" class="${on === id ? "on" : ""}" data-repo-go="${esc(link(entry, id, repoWide(id) ? "" : folder))}" aria-pressed="${on === id}">${label}</button>`;
  return `<div class="segmented repo-tabs" role="group" aria-label="What to show">${tab("look", "Look")}${tab("pushes", "Pushes")}${tab("files", "Files")}${tab("releases", "Releases")}${tab("talk", "Issues and pull requests")}</div>`;
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
  } else {
    // A picture of the video's first moments, never a live <video>: a phone gives a page only a few players,
    // and a grid of them would leave none for the video that is opened.
    const poster = entry.ready.get(`poster:${at}:${item.path}`);
    const picture = poster
      ? `<img src="${esc(poster)}" alt="">`
      : item.size > 0 && item.size <= PREVIEW_VIDEO_BYTES
        ? `<img alt="" data-repo-poster="${esc(item.path)}" data-repo-at="${esc(at)}">`
        : "";
    inner = `${picture}<span class="repo-play">${icon("play")}</span>`;
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
  const all = gallery(entry, folder);
  const count = entry.index.counts.get(folder) || { files: 0, images: 0, videos: 0 };
  const only = count.images && count.videos ? entry.only.get(folder) || "" : "";
  const items = only ? all.filter((item) => item.kind === only) : all;
  const list = `folder:${folder}:${only}`;
  entry.lists.set(list, { items, at: entry.ref });
  const shown = Math.min(items.length, entry.shown.get(folder) || GALLERY_STEP);
  const kind = (id, label) => `<button type="button" class="${only === id ? "on" : ""}" data-repo-only="${id}" data-repo-folder="${esc(folder)}" aria-pressed="${only === id}">${label}</button>`;
  const kinds = count.images && count.videos
    ? `<div class="segmented repo-kinds" role="group" aria-label="Which to show">${kind("", "All")}${kind("image", `Pictures ${count.images}`)}${kind("video", `Videos ${count.videos}`)}</div>`
    : "";

  const media = all.length
    ? `<section class="repo-sec">
        <h2>Pictures and videos <span class="muted">${esc(mediaWord(count))}</span></h2>
        ${kinds}
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

// The files a push or a pull request changed. Each opens to its changed lines.
function changedFiles(entry, files, { openable = false } = {}) {
  const rows = files.map((file) => {
    const label = `<span class="repo-status ${esc(file.status)}">${esc(STATUS[file.status] || file.status)}</span><span class="repo-file-name">${esc(file.filename)}</span><span class="repo-delta"><span class="add">+${file.additions || 0}</span> <span class="del">−${file.deletions || 0}</span></span>`;
    const open = openable && file.status !== "removed" ? `<a class="repo-open-file" href="${esc(link(entry, "file", file.filename))}" data-repo-go>Open the file</a>` : "";
    return file.patch
      ? `<li><details><summary>${label}</summary>${open}${patchHtml(file.patch)}</details></li>`
      : `<li><div class="repo-nopatch">${label}</div></li>`;
  }).join("");
  return `<ul class="repo-changed">${rows}</ul>`;
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
  const rows = changedFiles(entry, files, { openable: true });
  const stats = data.stats ? `${plural(files.length, "file")} · <span class="add">+${data.stats.additions}</span> <span class="del">−${data.stats.deletions}</span>` : plural(files.length, "file");
  return `<div class="repo-push-detail">
    ${body ? `<p class="repo-push-body">${esc(body)}</p>` : ""}
    <p class="muted repo-push-stats">${stats}${files.length >= 300 ? " (GitHub lists the first 300)" : ""}</p>
    ${media.length ? `<div class="repo-grid small">${media.slice(0, 24).map((item, i) => tile(entry, item, list, i, sha)).join("")}</div>` : ""}
    ${rows}
  </div>`;
}

// One push named in the address (a link to a commit lands here), shown above the list whatever its age.
function pickedPush(entry, sha) {
  const commit = loadCommit(entry, sha);
  const data = commit.data;
  const message = data ? String(data.commit?.message || "").split("\n")[0] : `Push ${sha.slice(0, 7)}`;
  const who = data?.author?.login || data?.commit?.author?.name || "";
  const when = data ? relativeTime(Date.parse(data.commit?.author?.date || "")) : "";
  return `<ul class="repo-pushes repo-picked"><li class="repo-push open">
    <div class="repo-push-row">
      <span class="repo-push-msg">${esc(message)}</span>
      <span class="muted repo-push-meta">${esc([who, when].filter(Boolean).join(" · "))}${who || when ? " · " : ""}<code>${esc(sha.slice(0, 7))}</code></span>
    </div>
    ${commitDetail(entry, sha)}
  </li></ul>`;
}

function pushesTab(entry, target) {
  const picked = target.commit ? pickedPush(entry, target.commit) : "";
  const pushes = loadPushes(entry, target.path);
  if (pushes.state === "loading") return `${picked}<p class="muted">Loading the latest pushes…</p>`;
  if (pushes.state === "error") return `${picked}<p class="muted">${esc(pushes.error)}</p>`;
  if (!pushes.list.length) return picked || `<div class="empty-state">${icon("git")}<h3>No pushes here yet</h3><p>Nothing has been committed ${target.path ? "to this folder" : "to this repo"}.</p></div>`;
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
  return `${picked}${picked ? `<h2 class="repo-sub">Latest pushes</h2>` : ""}<ul class="repo-pushes">${rows}</ul>${more}`;
}

// ---- releases

function releasesTab(entry) {
  const releases = loadReleases(entry);
  if (releases.state === "loading") return `<p class="muted">Loading the releases…</p>`;
  if (releases.state === "error") return `<p class="muted">${esc(releases.error)}</p>`;
  if (!releases.list.length) return `<div class="empty-state">${icon("save")}<h3>No releases yet</h3><p>${esc(entry.full)} has published no release.</p></div>`;
  const latest = releases.list.find((release) => !release.draft && !release.prerelease);
  const rows = releases.list.map((release) => {
    release.clean ??= release.body_html ? cleanHtml(release.body_html, cleanContext(entry, "", { tab: "releases", at: "" })) : "";
    const pills = [
      release === latest ? `<span class="pill repo-pill latest">latest</span>` : "",
      release.prerelease ? `<span class="pill repo-pill">pre-release</span>` : "",
      release.draft ? `<span class="pill repo-pill">draft</span>` : "",
    ].join("");
    const when = relativeTime(Date.parse(release.published_at || release.created_at || ""));
    const who = release.author?.login || "";
    // A file is saved by the browser straight from GitHub's file store; no GitHub page opens.
    const files = (release.assets || []).map((asset) => `<li><a href="${esc(asset.browser_download_url)}" data-github download rel="noopener noreferrer">${icon("save", "sm")}<span>${esc(asset.name)}</span></a><span class="muted">${humanSize(asset.size)} · ${plural(asset.download_count || 0, "download")}</span></li>`);
    if (release.tag_name && !release.draft) {
      files.push(`<li><a href="https://github.com/${esc(entry.full)}/archive/refs/tags/${esc(encodePath(release.tag_name))}.zip" data-github download rel="noopener noreferrer">${icon("save", "sm")}<span>Source code (zip)</span></a><span class="muted">${esc(release.tag_name)}</span></li>`);
    }
    return `<li class="repo-release">
      <h2>${esc(release.name || release.tag_name || "Release")} ${pills}</h2>
      <p class="muted repo-release-meta">${esc([release.tag_name, who, when].filter(Boolean).join(" · "))}</p>
      ${release.clean ? `<div class="prose repo-doc">${release.clean}</div>` : ""}
      ${files.length ? `<ul class="repo-assets">${files.join("")}</ul>` : ""}
    </li>`;
  }).join("");
  const notes = [
    entry.isPrivate ? `<p class="muted repo-note">A private repo's files download through github.com, so this browser must be signed in there too.</p>` : "",
    releases.list.length >= RELEASES ? `<p class="muted repo-note">The latest ${RELEASES} releases.</p>` : "",
  ].join("");
  return `<ul class="repo-releases">${rows}</ul>${notes}`;
}

// ---- issues and pull requests

function talkState(item) {
  if (item.pull_request?.merged_at) return "merged";
  return item.state === "closed" ? "closed" : "open";
}

function talkPills(item) {
  const state = talkState(item);
  return `<span class="pill repo-pill ${state}">${state}</span><span class="pill repo-pill">${item.pull_request ? "pull request" : "issue"}</span>`;
}

function talkTab(entry, target) {
  if (/^\d+$/.test(target.path)) return threadView(entry, Number(target.path));
  const which = target.state === "closed" ? "closed" : "open";
  const state = (id, label) => `<button type="button" class="${which === id ? "on" : ""}" data-repo-go="${esc(link(entry, "talk", "", { ref: "", state: id === "closed" ? "closed" : "" }))}" aria-pressed="${which === id}">${label}</button>`;
  const chips = `<div class="segmented repo-kinds" role="group" aria-label="Which to show">${state("open", "Open")}${state("closed", "Closed")}</div>`;
  const talk = loadTalk(entry, which);
  if (talk.state === "loading") return `${chips}<p class="muted">Loading the issues and pull requests…</p>`;
  if (talk.state === "error") return `${chips}<p class="muted">${esc(talk.error)}</p>`;
  if (!talk.list.length) return `${chips}<div class="empty-state">${icon("chat")}<h3>Nothing ${which} here</h3><p>${esc(entry.full)} has no ${which} issue or pull request.</p></div>`;
  const rows = talk.list.map((item) => {
    const labels = (item.labels || []).map((label) => `<span class="repo-label">${esc(typeof label === "string" ? label : label.name)}</span>`).join("");
    const facts = [`#${item.number}`, item.user?.login, `updated ${relativeTime(Date.parse(item.updated_at || ""))}`, item.comments ? plural(item.comments, "comment") : ""].filter(Boolean).join(" · ");
    return `<li><a class="repo-talk-row" href="${esc(link(entry, "talk", String(item.number), { ref: "" }))}" data-repo-go>
      <span class="repo-talk-title">${esc(item.title)} ${labels}</span>
      <span class="repo-talk-facts">${talkPills(item)}<span class="muted">${esc(facts)}</span></span>
    </a></li>`;
  }).join("");
  const more = talk.list.length >= TALK ? `<p class="muted repo-note">The ${TALK} that changed last.</p>` : "";
  return `${chips}<ul class="repo-talk">${rows}</ul>${more}`;
}

function threadView(entry, number) {
  const back = `<p class="repo-filebar"><a href="${esc(link(entry, "talk", "", { ref: "" }))}" data-repo-go>All issues and pull requests</a></p>`;
  const thread = loadThread(entry, number);
  if (thread.state === "loading") return `${back}<p class="muted">Loading #${number}…</p>`;
  if (thread.state === "error") return `${back}<p class="muted">${esc(thread.error)}</p>`;
  const { item, pull } = thread;
  const context = cleanContext(entry, "", { tab: "talk", at: String(number), branch: "" });
  const said = thread.said.map((entryOf) => {
    entryOf.clean ??= entryOf.html ? cleanHtml(entryOf.html, context) : "";
    const facts = [entryOf.note, relativeTime(Date.parse(entryOf.when || ""))].filter(Boolean).join(" · ");
    return `<li class="repo-say">
      <p class="repo-say-head"><strong>${esc(entryOf.who || "someone")}</strong> <span class="muted">${esc(facts)}</span></p>
      ${entryOf.clean ? `<div class="prose repo-doc">${entryOf.clean}</div>` : entryOf.first ? `<p class="muted">No description.</p>` : ""}
    </li>`;
  }).join("");
  const branches = pull ? `${pull.head?.ref || "?"} into ${pull.base?.ref || "?"}` : "";
  const size = pull ? `${plural(pull.changed_files || thread.files.length, "file")} · <span class="add">+${pull.additions || 0}</span> <span class="del">−${pull.deletions || 0}</span>` : "";
  const merged = pull?.merged_at ? `merged ${relativeTime(Date.parse(pull.merged_at))}${pull.merged_by?.login ? ` by ${pull.merged_by.login}` : ""}` : "";
  const facts = [esc(branches), size, esc(merged)].filter(Boolean).join(" · ");
  const files = thread.files.length
    ? `<section class="repo-sec"><h2 class="repo-sub">Files changed <span class="muted">${thread.files.length}${thread.files.length >= 100 ? " (the first 100)" : ""}</span></h2>${changedFiles(entry, thread.files)}</section>`
    : "";
  return `${back}
    <h2 class="repo-thread-title">${esc(item.title)} <span class="muted">#${item.number}</span></h2>
    <p class="repo-talk-facts repo-thread-facts">${talkPills(item)}${facts ? `<span class="muted">${facts}</span>` : ""}</p>
    <ol class="repo-thread">${said}</ol>
    ${files}`;
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
  if (!repoWide(target.tab)) {
    if (target.tab !== "file" && target.path && !entry.index.dirs.has(target.path) && findFile(entry, target.path)) shown = { ...target, tab: "file" };
    if (target.tab === "file" && entry.index.dirs.has(target.path)) shown = { ...target, tab: "files" };
  }
  let body;
  if (repoWide(shown.tab)) {
    body = (shown.tab === "releases" ? releasesTab : talkTab)(entry, shown);
  } else if (shown.tab !== "file" && !entry.index.dirs.has(shown.path)) {
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
  if (state.view === "projects" && !parseRepoHash(location.hash) && parseRepoHash(hash)) rankingScroll = { view: scroller()?.scrollTop || 0, page: window.scrollY };
  if (location.hash !== hash) history.pushState(null, "", hash);
  // From another page of Cairn (a link in a note, on Vibe, on Home), Projects is opened first.
  if (state.view !== "projects") setView("projects", { push: false });
  else renderView();
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

// A video that is only taken off the page keeps its player; Safari gives a page a few, and then the next video
// never loads. So a video is stopped and emptied before it goes.
function release(root) {
  for (const video of root.querySelectorAll("video")) {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
}

function closeViewer() {
  if (!viewer) return;
  release(viewer.box);
  viewer.box.remove();
  viewer = null;
}

function showInViewer() {
  const { box, entry, items, at } = viewer;
  const item = items[viewer.index];
  const stage = $(".repo-lb-stage", box);
  const turn = (viewer.turn += 1);
  release(stage);
  stage.innerHTML = item.kind === "video"
    ? `<video controls autoplay playsinline></video><p class="repo-lb-wait">Loading the video${item.size ? ` (${humanSize(item.size)})` : ""}…</p>`
    : `<img alt="${esc(baseName(item.path))}">`;
  const media = stage.firstElementChild;
  const ready = srcNow(entry, item, at);
  const put = (src) => {
    if (!viewer || viewer.turn !== turn) return;
    $(".repo-lb-wait", stage)?.remove();
    if (!src) {
      stage.innerHTML = `<p class="repo-lb-wait">Couldn't load ${esc(baseName(item.path))}.</p>`;
      return;
    }
    media.src = src;
    // Safari sometimes leaves an autoplay video waiting, with nothing loaded. Asking it to play starts it; if the
    // browser refuses (no tap came first), the video's own play button is there.
    if (item.kind === "video") media.play().catch(() => {});
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

  // A link to a repo on github.com, anywhere in Cairn (a note, Vibe, Home, a README), opens that place here.
  // Ctrl, Cmd, Shift or the middle button still open GitHub, and so does a link marked data-github (the GitHub
  // buttons and file downloads). In a dialog it only happens when the dialog has a button that closes it.
  const outside = target.closest("a[href]");
  const plain = !event.defaultPrevented && !event.button && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
  if (outside && plain && !outside.hasAttribute("data-github") && !outside.hasAttribute("download") && !$("#app")?.hidden) {
    const place = githubTarget(outside.href);
    const dialog = outside.closest(".modal-wrap");
    const done = dialog?.querySelector("[data-cancel]");
    if (place && (!dialog || done)) {
      event.preventDefault();
      done?.click();
      if (state.open) closeDrawer();
      return goTo(place);
    }
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
  if (target.closest("[data-repo-branches]")) {
    const repo = sharedFor(entry);
    repo.branchesOpen = !repo.branchesOpen;
    return renderView();
  }
  const only = target.closest("[data-repo-only]");
  if (only) {
    entry.only.set(only.dataset.repoFolder, only.dataset.repoOnly);
    entry.shown.delete(only.dataset.repoFolder);
    return renderView();
  }
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
  const at = el.dataset.repoAt || entry.ref;
  const poster = el.dataset.repoPoster;
  const path = poster || el.dataset.repoSrc;
  el.removeAttribute("data-repo-src");
  el.removeAttribute("data-repo-poster");
  (poster ? posterUrl(entry, path, at) : blobUrl(entry, path, at)).then((url) => {
    if (url && el.isConnected) el.src = url;
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
  for (const el of document.querySelectorAll("#view [data-repo-src], #view [data-repo-poster]")) {
    if (el.hasAttribute("data-repo-now")) fill(el, entry);
    else watcher.observe(el);
  }
}
