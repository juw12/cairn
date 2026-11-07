// Pure functions shared (and parity-tested) with cairn/core.py.
// Nothing here touches storage or the DOM.

export const TYPES = ["idea", "task", "decision", "note", "journal", "session", "feedback"];

export const FOLDERS = {
  idea: "ideas",
  task: "tasks",
  decision: "decisions",
  note: "notes",
  journal: "journal",
  session: "sessions",
  feedback: "feedback",
};

export const STATUSES = {
  idea: ["raw", "exploring", "ready", "parked"],
  task: ["todo", "doing", "done"],
  decision: ["proposed", "accepted", "superseded"],
  note: [],
  journal: [],
  session: ["live", "shipped", "partial", "dropped"],
  feedback: ["new", "planned", "addressed", "wontfix"],
};

export const TEMPLATES = {
  idea: "## Why\n\n\n## What if\n\n",
  task: "",
  decision: "## Context\n\n\n## Options\n\n\n## Decision\n\n\n## Consequences\n\n",
  note: "",
  journal: "## Log\n\n- \n",
  session: "## Goal\n\n\n## Log\n\n",
  feedback: "",
};

const CAPTURE_PREFIXES = {
  idea: "idea",
  todo: "task",
  task: "task",
  decision: "decision",
  decide: "decision",
  note: "note",
  journal: "journal",
  log: "journal",
  feedback: "feedback",
  fb: "feedback",
};

export const SPECIAL_FILES = ["CONTEXT.md", "README.md"];
// Files a brain holds: Markdown notes and Obsidian canvases (JSON Canvas, jsoncanvas.org).
export const isNotePath = (path) => /\.md$/i.test(path);
export const isCanvasPath = (path) => /\.canvas$/i.test(path);
export const isBrainFile = (path) => isNotePath(path) || isCanvasPath(path);

const KEY_RE = /^([A-Za-z0-9_-]+):(.*)$/;
const TAG_RE = /(^|\s)#([A-Za-z0-9][A-Za-z0-9_/-]*)/g;
const LINK_RE = /\[\[([^\]\n]+)\]\]/g;
const TODO_RE = /(?:#|\/\/|\/\*|<!--|--|;|\*)\s*(TODO|FIXME|HACK|XXX)\b(?:\([^)]*\))?[\s:\-]*(.*)$/;
const CAPTURE_RE = /^([A-Za-z]+):\s*([\s\S]*)$/;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

// ------------------------------------------------------------ frontmatter

function parseScalar(raw) {
  const value = raw.trim();
  if (value.length >= 2 && value[0] === value[value.length - 1] && (value[0] === '"' || value[0] === "'")) {
    const inner = value.slice(1, -1);
    return value[0] === '"' ? inner.replaceAll('\\"', '"') : inner;
  }
  return value.replace(/\s+#.*$/, "").trim();
}

function splitList(inner) {
  const parts = [];
  let current = "";
  let quote = "";
  for (let index = 0; index < inner.length; index++) {
    const ch = inner[index];
    if (quote) {
      current += ch;
      if (ch === quote && inner[index - 1] !== "\\") quote = "";
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      current += ch;
    } else if (ch === ",") {
      parts.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  parts.push(current);
  return parts;
}

function parseList(inner) {
  return splitList(inner).map(parseScalar).filter((item) => item !== "");
}

export function parseNote(text) {
  text = text.replaceAll("\r\n", "\n");
  if (!text.startsWith("---\n")) return { meta: {}, body: text };
  let end = text.indexOf("\n---", 3);
  while (end !== -1 && !["\n", ""].includes(text.slice(end + 4, end + 5))) {
    end = text.indexOf("\n---", end + 4);
  }
  if (end === -1) return { meta: {}, body: text };
  const header = text.slice(4, end);
  let body = text.slice(end + 4);
  if (body.startsWith("\n")) body = body.slice(1);

  const meta = {};
  const lines = header ? header.split("\n") : [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const match = KEY_RE.exec(line);
    if (!match) {
      index += 1;
      continue;
    }
    const key = match[1];
    const rest = match[2];
    const block = [];
    while (index + 1 < lines.length && (/^[ \t]/.test(lines[index + 1]) || lines[index + 1] === "")) {
      index += 1;
      block.push(lines[index]);
    }
    while (block.length && block[block.length - 1].trim() === "") block.pop();
    const stripped = rest.trim();
    if (block.length) {
      const items = block.map((b) => b.trim());
      if (stripped === "" && items.every((item) => item.startsWith("- ") || item === "-")) {
        meta[key] = items.map((item) => parseScalar(item.slice(1))).filter((item) => item !== "");
      } else {
        meta[key] = { raw: [line, ...block].join("\n") };
      }
    } else if (/^\[.*\]\s*(#.*)?$/.test(stripped)) {
      meta[key] = parseList(stripped.slice(1, stripped.lastIndexOf("]")));
    } else {
      meta[key] = parseScalar(rest);
    }
    index += 1;
  }
  return { meta, body };
}

function formatScalar(value) {
  value = String(value);
  const needsQuotes =
    value !== value.trim() ||
    value.includes(": ") ||
    value.includes(" #") ||
    (value !== "" && "[{\"'#&*!|>%@`".includes(value[0]));
  return needsQuotes ? `"${value.replaceAll('"', '\\"')}"` : value;
}

function formatListItem(value) {
  value = String(value);
  if (value.includes(",") || (value !== "" && "[]\"'".includes(value[0])) || value !== value.trim()) {
    return `"${value.replaceAll('"', '\\"')}"`;
  }
  return value;
}

export function serializeNote(meta, body) {
  const keys = Object.keys(meta || {});
  if (!keys.length) return body;
  const lines = ["---"];
  for (const key of keys) {
    const value = meta[key];
    if (value && typeof value === "object" && !Array.isArray(value) && "raw" in value) {
      lines.push(value.raw);
    } else if (Array.isArray(value)) {
      lines.push(`${key}: [${value.map(formatListItem).join(", ")}]`);
    } else if (value === null || value === undefined) {
      lines.push(`${key}:`);
    } else {
      lines.push(`${key}: ${formatScalar(value)}`.trimEnd());
    }
  }
  lines.push("---");
  return lines.join("\n") + "\n" + body;
}

// ------------------------------------------------------------ names

export function slugify(title) {
  let text = String(title).normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
  text = text.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  text = text.slice(0, 60).replace(/^-+|-+$/g, "");
  return text || "untitled";
}

export function uniqueSlug(base, taken) {
  if (!taken.has(base)) return base;
  let number = 2;
  while (taken.has(`${base}-${number}`)) number += 1;
  return `${base}-${number}`;
}

export function notePath(type, title, existingPaths, today) {
  const folder = FOLDERS[type] || "notes";
  const taken = new Set(
    existingPaths
      .filter((p) => p.startsWith(folder + "/") && p.endsWith(".md"))
      .map((p) => p.slice(folder.length + 1, -3)),
  );
  if (type === "journal") return `${folder}/${uniqueSlug(today, taken)}.md`;
  let slug = slugify(title);
  if (type === "decision") {
    const numbers = [...taken].map((name) => /^(\d{4})-/.exec(name)).filter(Boolean).map((m) => Number(m[1]));
    const next = numbers.length ? Math.max(...numbers) + 1 : 1;
    slug = `${String(next).padStart(4, "0")}-${slug}`;
  } else if (type === "session") {
    slug = `${today}-${slug}`;
  }
  return `${folder}/${uniqueSlug(slug, taken)}.md`;
}

export function defaultStatus(type) {
  const statuses = STATUSES[type] || [];
  return statuses.length ? statuses[0] : "";
}

// ------------------------------------------------------------ capture

export function parseCapture(text) {
  text = text.replaceAll("\r\n", "\n").trim();
  const newline = text.indexOf("\n");
  let first = newline === -1 ? text : text.slice(0, newline);
  const rest = newline === -1 ? "" : text.slice(newline + 1);
  let type = "idea";
  const match = CAPTURE_RE.exec(first);
  if (match && CAPTURE_PREFIXES[match[1].toLowerCase()]) {
    type = CAPTURE_PREFIXES[match[1].toLowerCase()];
    first = match[2];
  }
  const tags = [];
  for (const found of first.matchAll(TAG_RE)) {
    const name = found[2].toLowerCase();
    if (!tags.includes(name)) tags.push(name);
  }
  const title = first.replace(TAG_RE, "$1").replace(/\s+/g, " ").trim();
  return { type, title, tags, status: defaultStatus(type), body: rest.trim() };
}

// ------------------------------------------------------------ notes

export function noteFromFile(path, text, mtime = 0) {
  const { meta, body } = parseNote(text);
  const folder = path.includes("/") ? path.split("/")[0] : "";
  let folderType = Object.keys(FOLDERS).find((t) => FOLDERS[t] === folder) || "note";
  const fileName = path.split("/").pop().slice(0, -3);
  // Obsidian-style daily notes (2026-11-05.md) anywhere in the brain
  if (folderType === "note" && DAY_RE.test(fileName)) folderType = "journal";
  const type = typeof meta.type === "string" && TYPES.includes(meta.type) ? meta.type : folderType;
  let title = typeof meta.title === "string" ? meta.title : "";
  if (!title) {
    const heading = /^#\s+(.+)$/m.exec(body);
    title = heading ? heading[1].trim() : fileName;
  }
  let tags = meta.tags;
  if (typeof tags === "string") tags = tags.split(",").map((t) => t.trim()).filter(Boolean);
  if (!Array.isArray(tags)) tags = [];
  let status = typeof meta.status === "string" ? meta.status : "";
  if (!status) status = defaultStatus(type);
  return { path, slug: fileName, type, title, status, tags, meta, body, mtime };
}

// ------------------------------------------------------------ links (Obsidian-compatible)

const WIKI_RE = /(!?)\[\[([^\]\n]+)\]\]/g;
const MD_LINK_RE = /(!?)\[[^\]\n]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
const FENCE_RE = /```[\s\S]*?```/g;
const INLINE_CODE_RE = /`[^`\n]*`/g;
const URL_RE = /^[a-z][a-z0-9+.-]*:/i;
const EXTENSION_RE = /\.[A-Za-z0-9]{1,5}$/;

function folderOf(path) {
  const index = path.lastIndexOf("/");
  return index === -1 ? "" : path.slice(0, index);
}

function joinPath(folder, target) {
  const parts = [];
  for (const part of (folder ? `${folder}/${target}` : target).split("/")) {
    if (part === "..") parts.pop();
    else if (part && part !== ".") parts.push(part);
  }
  return parts.join("/");
}

function safeDecode(text) {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

// Every link in a body, in order: [[wiki]], [[folder/note|alias]], [[note#heading]], ![[embed]], [text](path.md).
// Links inside code are ignored, like Obsidian does.
export function linkTargets(body) {
  const text = body.replace(FENCE_RE, "").replace(INLINE_CODE_RE, "");
  const out = [];
  for (const match of text.matchAll(WIKI_RE)) {
    const target = match[2].split("|")[0].split("#")[0].trim();
    if (target) out.push({ target, embed: match[1] === "!" });
  }
  for (const match of text.matchAll(MD_LINK_RE)) {
    const href = match[2];
    if (URL_RE.test(href) || href.startsWith("#")) continue;
    const target = safeDecode(href.split("#")[0]).trim();
    if (target) out.push({ target, embed: match[1] === "!" });
  }
  return out;
}

export function extractLinks(body) {
  const links = [];
  for (const { target, embed } of linkTargets(body)) {
    if (embed) continue;
    const name = slugify(target.replace(/\.md$/i, "").split("/").pop());
    if (!links.includes(name)) links.push(name);
  }
  return links;
}

function addTo(map, key, value) {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}

export function linkIndex(notes) {
  const byPath = new Map();
  const byName = new Map();
  const bySlug = new Map();
  const byTitle = new Map();
  for (const note of notes) {
    byPath.set(note.path.toLowerCase(), note.path);
    const fileName = note.path.split("/").pop().slice(0, -3);
    addTo(byName, fileName.toLowerCase(), note.path);
    addTo(bySlug, slugify(fileName), note.path);
    const titleSlug = slugify(note.title);
    if (!byTitle.has(titleSlug)) byTitle.set(titleSlug, note.path);
  }
  return { byPath, byName, bySlug, byTitle };
}

// Same folder wins, then the shallowest path, then alphabetical: how Obsidian picks between duplicate names.
function closest(paths, folder) {
  const same = paths.filter((p) => folderOf(p) === folder);
  if (same.length) return same[0];
  return [...paths].sort((a, b) => a.split("/").length - b.split("/").length || (a < b ? -1 : a > b ? 1 : 0))[0];
}

// Resolve a link target written in `from` to a note path, or null (attachment or missing note).
export function resolveTarget(index, target, from = "") {
  let name = target.trim().replaceAll("\\", "/");
  if (!name) return null;
  const explicitNote = /\.md$/i.test(name);
  if (explicitNote) name = name.slice(0, -3);
  const folder = folderOf(from);
  if (name.includes("/")) {
    for (const option of [joinPath(folder, name), joinPath("", name)]) {
      const hit = index.byPath.get(`${option.toLowerCase()}.md`);
      if (hit) return hit;
    }
    return null;
  }
  const named = index.byName.get(name.toLowerCase());
  if (named) return closest(named, folder);
  if (!explicitNote && EXTENSION_RE.test(name)) return null;
  // Loose matching ([[Offline Sync]] -> offline-sync.md) is Cairn's convention, so it only applies to
  // Cairn-style kebab-case file names. Obsidian-style names must match exactly, as they do in Obsidian.
  const loose = looseMatch(index, name, folder);
  return loose && isSlugName(loose) ? loose : null;
}

function isSlugName(path) {
  const fileName = path.split("/").pop().slice(0, -3);
  return slugify(fileName) === fileName;
}

function looseMatch(index, name, folder) {
  const slug = slugify(name);
  const slugged = index.bySlug.get(slug);
  if (slugged) return closest(slugged, folder);
  return index.byTitle.get(slug) || null;
}

// A link Obsidian cannot resolve but that almost certainly means an existing note (dash, case or spacing
// differences). The Map offers to rewrite these; they are never counted as real links.
export function nearMiss(index, target, from = "") {
  if (resolveTarget(index, target, from) || isAttachment(target)) return null;
  const name = target.trim().replaceAll("\\", "/").replace(/\.md$/i, "").split("/").pop();
  return looseMatch(index, name, folderOf(from));
}

// Links a note makes: its body plus wiki links written in its properties (Obsidian graphs both).
export function noteLinks(note) {
  const fromMeta = [];
  const walk = (value) => {
    if (typeof value === "string") {
      for (const match of value.matchAll(WIKI_RE)) {
        const target = match[2].split("|")[0].split("#")[0].trim();
        if (target) fromMeta.push({ target, embed: match[1] === "!" });
      }
    } else if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === "object" && typeof value.raw === "string") walk(value.raw);
  };
  Object.values(note.meta || {}).forEach(walk);
  return [...fromMeta, ...linkTargets(note.body)];
}

export function isAttachment(target) {
  const name = target.trim();
  return !/\.md$/i.test(name) && EXTENSION_RE.test(name);
}

// Nodes carry what the Map filters and colours by; edges are undirected pairs that remember direction.
export function buildGraph(notes) {
  const index = linkIndex(notes);
  const nodes = notes.map((n) => ({
    id: n.path,
    title: n.title,
    type: n.type,
    kind: typeof n.meta.type === "string" && n.meta.type ? n.meta.type : n.type,
    folder: folderOf(n.path),
    tags: n.tags,
    status: n.status,
  }));
  const edges = [];
  const byPair = new Map();
  const attachments = [];
  const unresolved = [];
  for (const note of notes) {
    for (const { target, embed } of noteLinks(note)) {
      const targetPath = resolveTarget(index, target, note.path);
      if (!targetPath) {
        if (isAttachment(target)) attachments.push({ from: note.path, target, embed });
        else unresolved.push({ from: note.path, target, embed, suggestion: nearMiss(index, target, note.path) });
        continue;
      }
      if (targetPath === note.path) continue;
      const key = [note.path, targetPath].sort().join("\u0000");
      const existing = byPair.get(key);
      if (existing) {
        if (existing.source !== note.path) existing.both = true;
        continue;
      }
      const edge = { source: note.path, target: targetPath, both: false };
      byPair.set(key, edge);
      edges.push(edge);
    }
  }
  return { nodes, edges, attachments, unresolved };
}

export function resolveLink(notes, target, from = "") {
  const path = resolveTarget(linkIndex(notes), target, from);
  return path ? notes.find((n) => n.path === path) || null : null;
}

// ------------------------------------------------------------ canvases as references
// A .canvas file points at notes two ways: file cards ({"type":"file","file":"notes/x.md"}) and [[links]]
// written inside text cards. Obsidian counts both, so the Map and a note's backlinks do too.
export function canvasTargets(text) {
  let data;
  try {
    data = JSON.parse(String(text ?? "") || "{}");
  } catch {
    return [];
  }
  const out = [];
  for (const node of Array.isArray(data?.nodes) ? data.nodes : []) {
    if (!node || typeof node !== "object") continue;
    if (node.type === "file" && typeof node.file === "string" && node.file.trim()) out.push({ target: node.file.trim(), kind: "file" });
    else if ((node.type === "text" || !node.type) && typeof node.text === "string") {
      for (const { target } of linkTargets(node.text)) out.push({ target, kind: "link" });
    }
  }
  return out;
}

// File cards hold a vault-relative path; Cairn may keep the vault inside a brain folder, so the canvas's
// own top folder is tried as a prefix too.
function resolveCanvasTarget(index, { target, kind }, canvasPath) {
  if (kind === "file") {
    const clean = target.replace(/^\/+/, "").replaceAll("\\", "/");
    if (!isNotePath(clean)) return null;
    const top = canvasPath.includes("/") ? canvasPath.split("/")[0] : "";
    for (const option of [clean, top ? `${top}/${clean}` : ""]) {
      const hit = option && index.byPath.get(option.toLowerCase());
      if (hit) return hit;
    }
  }
  return resolveTarget(index, target, canvasPath);
}

// [{ canvas, note }] for every note a canvas in `files` refers to (each pair once).
export function canvasLinks(files, notes) {
  const index = linkIndex(notes);
  const pairs = [];
  for (const file of files) {
    if (!isCanvasPath(file.path)) continue;
    const seen = new Set();
    for (const ref of canvasTargets(file.text)) {
      const note = resolveCanvasTarget(index, ref, file.path);
      if (note && !seen.has(note)) {
        seen.add(note);
        pairs.push({ canvas: file.path, note });
      }
    }
  }
  return pairs;
}

export function canvasBacklinks(files, notes, path) {
  return canvasLinks(files, notes).filter((pair) => pair.note === path).map((pair) => pair.canvas);
}

export function backlinks(notes, path) {
  if (!notes.some((n) => n.path === path)) return [];
  const index = linkIndex(notes);
  return notes
    .filter((n) => n.path !== path && noteLinks(n).some(({ target }) => resolveTarget(index, target, n.path) === path))
    .map((n) => n.path);
}

// ------------------------------------------------------------ folder sync (Obsidian)

// Two-way sync plan between a vault folder and a brain folder. Same rules as plan_sync in core.py:
// vault / brain: { path: { hash, mtime } }; manifest: { path: hash both sides had at the last sync }.
// Returns [{ path, op[, keep] }]; an edit always wins over a delete, so nothing written is lost.
export function planSync(vault, brain, manifest) {
  const plan = [];
  const paths = [...new Set([...Object.keys(vault), ...Object.keys(brain)])].sort();
  for (const path of paths) {
    const v = vault[path] || null;
    const b = brain[path] || null;
    const m = Object.prototype.hasOwnProperty.call(manifest, path) ? manifest[path] : null;
    const vh = v ? v.hash : null;
    const bh = b ? b.hash : null;
    if (vh === bh) continue;
    let op;
    if (m === null) {
      op = v && !b ? "pull" : b && !v ? "push" : "conflict";
    } else {
      const vaultChanged = vh !== m;
      const brainChanged = bh !== m;
      if (vaultChanged && !brainChanged) op = v ? "pull" : "delete-brain";
      else if (brainChanged && !vaultChanged) op = b ? "push" : "delete-vault";
      else op = !v ? "push" : !b ? "pull" : "conflict";
    }
    const step = { path, op };
    if (op === "conflict") step.keep = b.mtime > v.mtime ? "brain" : "vault";
    plan.push(step);
  }
  return plan;
}

// ------------------------------------------------------------ lossless edits

function splitLines(text) {
  return text.split(/(?<=\n)/).filter((line) => line !== "");
}

function stripEol(line) {
  return line.replace(/\r?\n$/, "");
}

function formatProperty(key, value, styleLines, eol) {
  if (Array.isArray(value)) {
    const blockItem = styleLines.slice(1).map(stripEol).find((line) => /^\s*-\s/.test(line));
    if (blockItem && value.length) {
      const indent = /^(\s*)-/.exec(blockItem)[1];
      return [`${key}:${eol}`, ...value.map((item) => `${indent}- ${formatListItem(item)}${eol}`)];
    }
    return [`${key}: [${value.map(formatListItem).join(", ")}]${eol}`];
  }
  if (value === null || value === undefined || value === "") return [`${key}:${eol}`];
  return [`${key}: ${formatScalar(value)}${eol}`];
}

// Apply property changes and/or a new body to a note's original text, touching nothing else:
// unchanged properties keep their exact bytes, a changed one keeps its list style, the body keeps its
// line endings when it is not edited, and an edited body is written with the file's own line ending.
// Pass REMOVE as a property's value to delete that property (JS only; the editor's Properties panel).
export const REMOVE = Symbol("remove");

export function updateNoteText(original, changes = {}, body = undefined) {
  const lines = splitLines(original);
  const eol = original.includes("\r\n") ? "\r\n" : "\n";
  const hasHeader = lines.length > 0 && stripEol(lines[0]) === "---";
  let closing = -1;
  if (hasHeader) {
    for (let index = 1; index < lines.length; index++) {
      if (stripEol(lines[index]) === "---") {
        closing = index;
        break;
      }
    }
  }
  const current = parseNote(original).meta;
  const wanted = Object.entries(changes).filter(([key, value]) =>
    value === REMOVE ? key in current : value !== undefined && JSON.stringify(current[key] ?? null) !== JSON.stringify(value));
  let rawBody = closing === -1 ? original : lines.slice(closing + 1).join("");
  if (body !== undefined && body !== parseNote(original).body) {
    rawBody = eol === "\r\n" ? body.replaceAll("\r\n", "\n").replaceAll("\n", "\r\n") : body.replaceAll("\r\n", "\n");
  }
  if (!wanted.length && (body === undefined || body === parseNote(original).body)) return original;

  if (closing === -1) {
    if (!wanted.length) return rawBody;
    const header = wanted.filter(([, value]) => value !== REMOVE).flatMap(([key, value]) => formatProperty(key, value, [], eol));
    if (!header.length) return rawBody;
    return `---${eol}${header.join("")}---${eol}${rawBody}`;
  }

  // Group header lines into property segments exactly the way parseNote reads them.
  const headerLines = lines.slice(1, closing);
  const segments = [];
  for (let index = 0; index < headerLines.length; index++) {
    const match = KEY_RE.exec(stripEol(headerLines[index]));
    if (!match) {
      segments.push({ key: null, lines: [headerLines[index]] });
      continue;
    }
    const segment = { key: match[1], lines: [headerLines[index]] };
    while (index + 1 < headerLines.length && (/^[ \t]/.test(headerLines[index + 1]) || stripEol(headerLines[index + 1]) === "")) {
      index += 1;
      segment.lines.push(headerLines[index]);
    }
    segments.push(segment);
  }
  for (const [key, value] of wanted) {
    const segment = segments.find((s) => s.key === key);
    if (value === REMOVE) {
      if (segment) segments.splice(segments.indexOf(segment), 1);
      continue;
    }
    if (segment) {
      const trailingBlank = [];
      while (segment.lines.length > 1 && stripEol(segment.lines[segment.lines.length - 1]) === "") trailingBlank.unshift(segment.lines.pop());
      segment.lines = [...formatProperty(key, value, segment.lines, eol), ...trailingBlank];
    } else {
      segments.push({ key, lines: formatProperty(key, value, [], eol) });
    }
  }
  const lastHeaderLine = segments.length ? segments[segments.length - 1].lines.at(-1) : null;
  if (lastHeaderLine && !lastHeaderLine.endsWith("\n")) segments[segments.length - 1].lines.push(eol);
  let closingLine = lines[closing];
  if (!closingLine.endsWith("\n") && rawBody) closingLine += eol;
  return lines[0] + segments.flatMap((s) => s.lines).join("") + closingLine + rawBody;
}

// ------------------------------------------------------------ code inbox

export function scanTodos(text, path) {
  const items = [];
  text.replaceAll("\r\n", "\n").split("\n").forEach((line, index) => {
    const match = TODO_RE.exec(line);
    if (!match) return;
    const message = match[2].replace(/\s*(\*\/|-->)\s*$/, "").trim();
    items.push({ path, line: index + 1, tag: match[1], text: message || "(no description)" });
  });
  return items;
}

// ------------------------------------------------------------ journal (daily log), mirrored in cairn/core.py

const LOG_LINE_RE = /^- (?:(\d{1,2}:\d{2})\s+)?(.*\S.*)$/;
const LOG_LINK_RE = /\[\[([^\]|\n]+)(?:\|([^\]\n]+))?\]\]/g;
const DAY_SLUG_RE = /^\d{4}-\d{2}-\d{2}$/;

export function logEntries(body) {
  const entries = [];
  for (const line of String(body || "").replaceAll("\r\n", "\n").split("\n")) {
    const match = LOG_LINE_RE.exec(line);
    if (!match) continue;
    const text = match[2].trim();
    if (/^\[[ xX]\]\s*$/.test(text)) continue;
    entries.push({ time: match[1] || "", text });
  }
  return entries;
}

export function plainLogText(text) {
  return String(text)
    .replace(LOG_LINK_RE, (_, target, alias) => alias || target.split("/").pop())
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function recentLog(notes, today, days = 3) {
  const pages = notes
    .filter((n) => n.type === "journal" && DAY_SLUG_RE.test(n.slug) && n.slug <= today)
    .sort((a, b) => (a.slug !== b.slug ? (a.slug < b.slug ? 1 : -1) : a.path < b.path ? 1 : a.path > b.path ? -1 : 0));
  const out = [];
  for (const note of pages) {
    const entries = logEntries(note.body);
    if (entries.length && !out.some((item) => item.day === note.slug)) out.push({ day: note.slug, path: note.path, entries });
    if (out.length >= days) break;
  }
  return out;
}

function clip(text, limit) {
  const chars = [...text];
  return chars.length <= limit ? text : chars.slice(0, limit - 3).join("").trimEnd() + "...";
}

// ------------------------------------------------------------ context pack

export function summary(body) {
  for (let line of body.split("\n")) {
    line = line.trim();
    if (line && !line.startsWith("#") && !line.startsWith("---") && !line.startsWith("```")) {
      line = line.replace(/^[-*>]\s*(\[[ xX]\]\s*)?/, "").trim();
      if (line) return line.length <= 120 ? line : line.slice(0, 117).trimEnd() + "...";
    }
  }
  return "";
}

function compareNotes(a, b) {
  const left = a.title.toLowerCase();
  const right = b.title.toLowerCase();
  if (left !== right) return left < right ? -1 : 1;
  return a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
}

export function contextPack(notes, project, today) {
  const bullet = (note) => {
    const text = summary(note.body);
    const tags = note.tags.length ? ` \`${note.tags.map((t) => "#" + t).join(" ")}\`` : "";
    const line = `- **${note.title}**${tags} (${note.path})`;
    return line + (text ? `: ${text}` : "");
  };
  const pick = (type, statuses) =>
    notes.filter((n) => n.type === type && statuses.includes(n.status)).sort(compareNotes);
  const sections = [
    ["Doing now", pick("task", ["doing"])],
    ["Up next", pick("task", ["todo"])],
    ["Decisions in force", pick("decision", ["accepted"])],
    ["Open questions (proposed decisions)", pick("decision", ["proposed"])],
    ["Ideas being explored", pick("idea", ["exploring", "ready"])],
  ];
  const count = (type) => notes.filter((n) => n.type === type).length;
  const out = [
    `# ${project}: project context`,
    "",
    `_Generated by Cairn on ${today} from the \`brain/\` folder. Regenerate instead of editing._`,
    "",
    `${count("task")} tasks · ${count("idea")} ideas · ${count("decision")} decisions · ` +
      `${count("note")} notes · ${count("journal")} journal pages`,
    "",
  ];
  for (const [heading, items] of sections) {
    out.push(`## ${heading}`, "");
    if (items.length) out.push(...items.map(bullet));
    else out.push("_Nothing yet._");
    out.push("");
  }
  const lately = recentLog(notes, today);
  if (lately.length) {
    out.push("## Lately (from the journal)", "");
    for (const item of lately) {
      const shown = item.entries.slice(0, 4).map((e) => clip(plainLogText(e.text), 80));
      const more = item.entries.length - shown.length;
      out.push(`- **${item.day}**: ${shown.join("; ")}` + (more ? ` (+${more} more)` : ""));
    }
    out.push("");
  }
  out.push("## How to use this file", "");
  out.push(
    "Read this before working on the project. Details live in the linked files; " +
      "decisions in force are binding unless a newer decision supersedes them.",
  );
  return out.join("\n") + "\n";
}

// ------------------------------------------------------------ vibe sessions and feedback

// A session log line: `- 14:02 pass: pytest (12s)`. Anything else in the body is free text.
export const LOG_KINDS = ["start", "note", "prompt", "pass", "fail", "commit", "skill", "end"];
const VIBE_LOG_LINE_RE = /^\s*[-*]\s+(\d{1,2}:\d{2})\s+([a-z]+):\s*(.*)$/;
const STAMP_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/;
export const FINISHED = ["shipped", "partial", "dropped"];

export function sessionLog(body) {
  const entries = [];
  for (const line of body.replaceAll("\r\n", "\n").split("\n")) {
    const match = VIBE_LOG_LINE_RE.exec(line);
    if (match && LOG_KINDS.includes(match[2])) {
      entries.push({ time: match[1].padStart(5, "0"), kind: match[2], text: match[3].trim() });
    }
  }
  return entries;
}

const SKILL_RE = /^\/([A-Za-z0-9][A-Za-z0-9_-]*)/;

// `/refine app cairn · 6.5 -> 8.7` -> `/refine` (slash commands like /refine, /council, /ambitious-founder).
export function skillName(text) {
  const match = SKILL_RE.exec(String(text).trim());
  return match ? `/${match[1].toLowerCase()}` : "";
}

// A plain note that starts with a slash command is a skill run.
export function logKind(text, kind = "note") {
  return kind === "note" && skillName(text) ? "skill" : kind;
}

// `2026-11-07T14:02` -> minutes on a common clock (only differences are meaningful).
export function stampMinutes(stamp) {
  const match = typeof stamp === "string" ? STAMP_RE.exec(stamp) : null;
  if (!match) return null;
  const [year, month, day, hour, minute] = match.slice(1).map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return Math.round(date.getTime() / 60000) + hour * 60 + minute;
}

export function sessionMinutes(note) {
  const start = stampMinutes(note.meta.started);
  const end = stampMinutes(note.meta.ended);
  if (start === null || end === null || end < start) return 0;
  return end - start;
}

function count(value) {
  return typeof value === "string" && /^[0-9]+$/.test(value.trim()) ? Number(value.trim()) : 0;
}

export function feedbackRating(note) {
  const rating = count(note.meta.rating);
  return rating >= 1 && rating <= 5 ? rating : null;
}

function sessionDay(note) {
  for (const key of ["started", "created"]) {
    const value = note.meta[key];
    if (typeof value === "string" && DAY_RE.test(value.slice(0, 10))) return value.slice(0, 10);
  }
  return "";
}

function percent(part, whole) {
  return whole ? Math.floor((100 * part) / whole + 0.5) : null;
}

// Totals for the Vibe view: sessions, results, checks, code churn and feedback.
export function vibeStats(notes, today, days = 14) {
  const sessions = notes.filter((n) => n.type === "session");
  const feedback = notes.filter((n) => n.type === "feedback");
  const finished = sessions.filter((s) => FINISHED.includes(s.status));
  const logs = sessions.flatMap((s) => sessionLog(s.body));
  const passed = logs.filter((e) => e.kind === "pass").length;
  const failed = logs.filter((e) => e.kind === "fail").length;
  const ratings = feedback.map(feedbackRating).filter((r) => r !== null);
  const sum = (list, fn) => list.reduce((total, item) => total + fn(item), 0);
  const skills = {};
  for (const entry of logs) {
    const name = entry.kind === "skill" ? skillName(entry.text) : "";
    if (name) skills[name] = (skills[name] || 0) + 1;
  }
  const [year, month, day] = today.split("-").map(Number);
  const end = Date.UTC(year, month - 1, day);
  const daily = [];
  for (let offset = days - 1; offset >= 0; offset--) {
    const date = new Date(end - offset * 86400000).toISOString().slice(0, 10);
    const onDay = sessions.filter((s) => sessionDay(s) === date);
    daily.push({
      day: date,
      sessions: onDay.length,
      minutes: sum(onDay, sessionMinutes),
      shipped: onDay.filter((s) => s.status === "shipped").length,
    });
  }
  return {
    sessions: sessions.length,
    live: sessions.filter((s) => s.status === "live").length,
    shipped: sessions.filter((s) => s.status === "shipped").length,
    partial: sessions.filter((s) => s.status === "partial").length,
    dropped: sessions.filter((s) => s.status === "dropped").length,
    ship_rate: percent(finished.filter((s) => s.status === "shipped").length, finished.length),
    minutes: sum(finished, sessionMinutes),
    prompts: logs.filter((e) => e.kind === "prompt").length,
    passed,
    failed,
    pass_rate: percent(passed, passed + failed),
    commits: sum(sessions, (s) => count(s.meta.commits)),
    added: sum(sessions, (s) => count(s.meta.added)),
    removed: sum(sessions, (s) => count(s.meta.removed)),
    feedback: feedback.length,
    feedback_open: feedback.filter((f) => ["new", "planned"].includes(f.status)).length,
    feedback_addressed: feedback.filter((f) => f.status === "addressed").length,
    rating: ratings.length ? Math.floor((10 * ratings.reduce((a, b) => a + b, 0)) / ratings.length + 0.5) / 10 : null,
    ratings: [1, 2, 3, 4, 5].map((stars) => ratings.filter((r) => r === stars).length),
    skills,
    daily,
  };
}
