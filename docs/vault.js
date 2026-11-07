// Obsidian-style vault features shared by the views: search operators, bookmarks, templates,
// renaming with link updates, the folder tree, note rendering with ![[embeds]] and attachments.

import * as core from "./core.js";
import { escapeHtml as esc, inlineTags, renderMarkdown, sectionOf } from "./md.js";
import { linkTextFor, retargetLink } from "./graph.js";
import { fileByPath, noteByPath, scheduleGitRefresh, state, upsertFile } from "./state.js";

// ------------------------------------------------------------ link resolution (cached per note list)

let cache = { notes: null, index: null };

function index() {
  if (cache.notes !== state.notes) cache = { notes: state.notes, index: core.linkIndex(state.notes) };
  return cache.index;
}

export function resolveFrom(fromPath) {
  return (target) => {
    const path = core.resolveTarget(index(), target, fromPath);
    return path ? noteByPath(path) : null;
  };
}

// ------------------------------------------------------------ rendering a note (with embeds)

const MAX_EMBED_DEPTH = 2;

export function renderNote(note, { source = null, depth = 0, tasks } = {}) {
  const resolve = resolveFrom(note.path);
  return renderMarkdown(source ?? note.body, resolve, {
    depth,
    tasks,
    idPrefix: depth ? `e${depth}-` : "h-",
    embed: (target, heading, level) => {
      const embedded = resolve(target);
      if (!embedded) return null;
      const label = `${esc(embedded.title)}${heading ? ` › ${esc(heading.replace(/^\^/, ""))}` : ""}`;
      const head = `<div class="embed-head"><a class="wikilink" href="#" data-open="${esc(embedded.path)}">${label}</a></div>`;
      if (level >= MAX_EMBED_DEPTH || embedded.path === note.path) {
        return `<div class="embed embed-stop" data-embed="${esc(embedded.path)}">${head}</div>`;
      }
      const section = heading && !heading.startsWith("^") ? sectionOf(embedded.body, heading) : embedded.body;
      const body = section === null
        ? `<p class="muted">No heading “${esc(heading)}” in this note.</p>`
        : renderNote(embedded, { source: section, depth: level + 1, tasks: false });
      return `<div class="embed" data-embed="${esc(embedded.path)}">${head}<div class="embed-body">${body}</div></div>`;
    },
  });
}

// Images and attachments: ask the store where the file lives, try each candidate, else show a chip.
export function hydrateAssets(root, fromPath) {
  if (!root) return;
  const lookup = async (target, from) => {
    try {
      return (await state.store?.assetUrls?.(target, from)) || [];
    } catch {
      return [];
    }
  };
  for (const img of root.querySelectorAll("img[data-asset]:not([data-hydrated])")) {
    img.dataset.hydrated = "1";
    const from = img.closest("[data-embed]")?.dataset.embed || fromPath;
    lookup(img.dataset.asset, from).then((urls) => {
      const next = () => {
        const url = urls.shift();
        if (url) {
          img.src = url;
          return;
        }
        const chip = document.createElement("span");
        chip.className = "attachment missing";
        chip.title = "This attachment isn't available here";
        chip.textContent = img.dataset.asset;
        img.replaceWith(chip);
      };
      img.addEventListener("error", next);
      next();
    });
  }
  for (const link of root.querySelectorAll("a[data-asset-link]:not([data-hydrated])")) {
    link.dataset.hydrated = "1";
    const from = link.closest("[data-embed]")?.dataset.embed || fromPath;
    lookup(link.dataset.assetLink, from).then((urls) => {
      if (urls.length) {
        link.href = urls[0];
        link.target = "_blank";
        link.rel = "noopener";
      } else {
        link.classList.add("missing");
        link.title = "This attachment isn't available here";
      }
    });
  }
}

// ------------------------------------------------------------ search with operators

// Words must all match (title, body, tags or path). Operators, like Obsidian's search:
//   tag:release   path:ideas/   file:gpx   type:task   status:doing   "exact phrase"   -exclude
export function parseQuery(query) {
  const terms = [];
  const pattern = /(-?)(?:(tag|path|file|type|status|line):)?(?:"([^"]*)"|(\S+))/gi;
  for (const match of String(query).matchAll(pattern)) {
    const value = (match[3] ?? match[4] ?? "").toLowerCase();
    if (!value && match[3] === undefined) continue;
    const field = (match[2] || "").toLowerCase();
    terms.push({ negate: match[1] === "-", field, value: field ? value.replace(/^#/, "") : value });
  }
  return terms;
}

function matchesTerm(note, term) {
  const value = term.value;
  switch (term.field) {
    case "tag": {
      const wanted = value.replace(/^#/, "");
      const tags = [...note.tags.map((t) => String(t).toLowerCase()), ...inlineTags(note.body)];
      return tags.some((t) => t === wanted || t.startsWith(`${wanted}/`));
    }
    case "path":
      return note.path.toLowerCase().includes(value);
    case "file":
      return note.path.split("/").pop().toLowerCase().includes(value);
    case "type":
      return note.type === value || String(note.meta?.type || "").toLowerCase() === value;
    case "status":
      return String(note.status || "").toLowerCase() === value;
    case "line":
      return note.body.toLowerCase().split("\n").some((line) => line.includes(value));
    default: {
      if (value.startsWith("#") && value.length > 1) return matchesTerm(note, { field: "tag", value: value.slice(1) });
      const haystack = `${note.title}\n${note.body}\n${note.path}\n${note.tags.join(" ")}`.toLowerCase();
      return haystack.includes(value);
    }
  }
}

export function searchNotes(notes, query) {
  const terms = parseQuery(query);
  if (!terms.length) return notes;
  return notes.filter((note) => terms.every((term) => matchesTerm(note, term) !== term.negate));
}

export function searchScore(note, query) {
  const words = parseQuery(query).filter((t) => !t.field && !t.negate).map((t) => t.value);
  const title = note.title.toLowerCase();
  return words.reduce((score, word) => score + (title.includes(word) ? 2 : 0), 0);
}

// ------------------------------------------------------------ bookmarks (per brain, in this browser)

function bookmarkKey() {
  const info = state.info || {};
  return `cairn.bookmarks.${info.mode || "x"}.${info.owner ? `${info.owner}/` : ""}${info.project || ""}`;
}

export function bookmarks() {
  try {
    const saved = JSON.parse(localStorage.getItem(bookmarkKey()) || "[]");
    return Array.isArray(saved) ? saved.filter((path) => noteByPath(path)) : [];
  } catch {
    return [];
  }
}

function saveBookmarks(list) {
  localStorage.setItem(bookmarkKey(), JSON.stringify([...new Set(list)]));
}

export const isBookmarked = (path) => bookmarks().includes(path);

export function toggleBookmark(path) {
  const list = bookmarks();
  const on = !list.includes(path);
  saveBookmarks(on ? [...list, path] : list.filter((p) => p !== path));
  return on;
}

// ------------------------------------------------------------ templates (Templates/ folder)

export function templates() {
  return state.notes
    .filter((n) => /^(_?templates)\//i.test(n.path))
    .sort((a, b) => (a.title.toLowerCase() < b.title.toLowerCase() ? -1 : 1));
}

const pad = (n) => String(n).padStart(2, "0");

export function formatDate(date, format = "YYYY-MM-DD") {
  const days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const tokens = {
    YYYY: String(date.getFullYear()),
    MMMM: months[date.getMonth()],
    MMM: months[date.getMonth()].slice(0, 3),
    MM: pad(date.getMonth() + 1),
    DD: pad(date.getDate()),
    dddd: days[date.getDay()],
    ddd: days[date.getDay()].slice(0, 3),
    HH: pad(date.getHours()),
    mm: pad(date.getMinutes()),
    ss: pad(date.getSeconds()),
  };
  return format.replace(/YYYY|MMMM|MMM|MM|DD|dddd|ddd|HH|mm|ss/g, (token) => tokens[token]);
}

// {{title}} {{date}} {{time}} {{date:YYYY-MM-DD}} {{time:HH:mm}}, like Obsidian's core Templates plugin.
export function fillTemplate(text, { title = "", now = new Date() } = {}) {
  return String(text).replace(/\{\{\s*(title|date|time)(?::([^}]*))?\s*\}\}/gi, (_, name, format) => {
    const key = name.toLowerCase();
    if (key === "title") return title;
    if (key === "date") return formatDate(now, format?.trim() || "YYYY-MM-DD");
    return formatDate(now, format?.trim() || "HH:mm");
  });
}

// ------------------------------------------------------------ folder tree

export function folderTree(notes) {
  const root = { name: "", path: "", folders: new Map(), notes: [] };
  for (const note of notes) {
    const parts = note.path.split("/");
    parts.pop();
    let node = root;
    let path = "";
    for (const part of parts) {
      path = path ? `${path}/${part}` : part;
      if (!node.folders.has(part)) node.folders.set(part, { name: part, path, folders: new Map(), notes: [] });
      node = node.folders.get(part);
    }
    node.notes.push(note);
  }
  return root;
}

export function countNotes(folder) {
  let total = folder.notes.length;
  for (const child of folder.folders.values()) total += countNotes(child);
  return total;
}

// ------------------------------------------------------------ rename / move with link updates

export function normalizeNotePath(input, currentPath) {
  let path = String(input || "").trim().replaceAll("\\", "/").replace(/^\/+/, "").replace(/\/+/g, "/");
  if (!path) throw new Error("Give the note a name.");
  if (!/\.md$/i.test(path)) path += ".md";
  const parts = path.split("/");
  if (parts.some((part) => part === ".." || part === "." || part === "" || part.startsWith("."))) {
    throw new Error("Use a plain name, optionally inside folders, like ideas/my-note.");
  }
  if (/[:*?"<>|#^[\]]/.test(path)) throw new Error('A note name can\'t contain : * ? " < > | # ^ [ or ].');
  if (!path.includes("/") && currentPath?.includes("/") && !String(input).includes("/")) {
    // Renaming keeps the note in its folder unless a folder is typed.
    path = `${currentPath.slice(0, currentPath.lastIndexOf("/"))}/${path}`;
  }
  return path;
}

// Every note whose links point at `oldPath`, with the exact targets they wrote.
function linkersOf(oldPath) {
  const idx = core.linkIndex(state.notes);
  const out = [];
  for (const note of state.notes) {
    const targets = new Set();
    for (const { target } of core.noteLinks(note)) {
      if (core.resolveTarget(idx, target, note.path) === oldPath) targets.add(target);
    }
    if (targets.size) out.push({ note, targets: [...targets] });
  }
  return out;
}

export async function renameNote(oldPath, newPath) {
  if (newPath === oldPath) return { path: oldPath, updated: 0 };
  if (fileByPath(newPath) || (newPath.toLowerCase() !== oldPath.toLowerCase() && state.files.some((f) => f.path.toLowerCase() === newPath.toLowerCase()))) {
    throw new Error(`brain/${newPath} already exists.`);
  }
  const file = fileByPath(oldPath);
  if (!file) throw new Error("That note is gone.");
  const wasBookmarked = isBookmarked(oldPath);
  const linkers = linkersOf(oldPath);
  const renamedNotes = state.notes.map((n) => (n.path === oldPath ? { ...n, path: newPath } : n));
  const rewrite = (text, from, targets) => {
    const linkText = linkTextFor(renamedNotes, from, newPath);
    let out = text;
    for (const target of targets) out = retargetLink(out, target, linkText) ?? out;
    return out;
  };
  const self = linkers.find((l) => l.note.path === oldPath);
  const newText = self ? rewrite(file.text, newPath, self.targets) : file.text;
  const created = await state.store.write(newPath, newText, null);
  upsertFile(newPath, newText, created.mtime);
  let updated = 0;
  for (const { note, targets } of linkers) {
    if (note.path === oldPath) continue;
    const current = fileByPath(note.path);
    const text = rewrite(current.text, note.path, targets);
    if (text === current.text) continue;
    const { mtime } = await state.store.write(note.path, text, current.mtime);
    upsertFile(note.path, text, mtime);
    updated += 1;
  }
  await state.store.remove(oldPath);
  upsertFile(oldPath, null);
  if (wasBookmarked) saveBookmarks([...bookmarks(), newPath]);
  scheduleGitRefresh();
  return { path: newPath, updated };
}
