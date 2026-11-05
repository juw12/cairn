// Pure functions shared (and parity-tested) with cairn/core.py.
// Nothing here touches storage or the DOM.

export const TYPES = ["idea", "task", "decision", "note", "journal"];

export const FOLDERS = {
  idea: "ideas",
  task: "tasks",
  decision: "decisions",
  note: "notes",
  journal: "journal",
};

export const STATUSES = {
  idea: ["raw", "exploring", "ready", "parked"],
  task: ["todo", "doing", "done"],
  decision: ["proposed", "accepted", "superseded"],
  note: [],
  journal: [],
};

export const TEMPLATES = {
  idea: "## Why\n\n\n## What if\n\n",
  task: "",
  decision: "## Context\n\n\n## Options\n\n\n## Decision\n\n\n## Consequences\n\n",
  note: "",
  journal: "## Log\n\n- \n",
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
};

export const SPECIAL_FILES = ["CONTEXT.md", "README.md"];

const KEY_RE = /^([A-Za-z0-9_-]+):(.*)$/;
const TAG_RE = /(^|\s)#([A-Za-z0-9][A-Za-z0-9_/-]*)/g;
const LINK_RE = /\[\[([^\]\n]+)\]\]/g;
const TODO_RE = /(?:#|\/\/|\/\*|<!--|--|;|\*)\s*(TODO|FIXME|HACK|XXX)\b(?:\([^)]*\))?[\s:\-]*(.*)$/;
const CAPTURE_RE = /^([A-Za-z]+):\s*([\s\S]*)$/;

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
  const folderType = Object.keys(FOLDERS).find((t) => FOLDERS[t] === folder) || "note";
  const type = typeof meta.type === "string" && TYPES.includes(meta.type) ? meta.type : folderType;
  let title = typeof meta.title === "string" ? meta.title : "";
  const fileName = path.split("/").pop().slice(0, -3);
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

export function extractLinks(body) {
  const links = [];
  for (const match of body.matchAll(LINK_RE)) {
    const target = slugify(match[1].split("|")[0].split("#")[0]);
    if (!links.includes(target)) links.push(target);
  }
  return links;
}

export function buildGraph(notes) {
  const bySlug = new Map();
  for (const note of notes) {
    if (!bySlug.has(note.slug)) bySlug.set(note.slug, note.path);
    const titleSlug = slugify(note.title);
    if (!bySlug.has(titleSlug)) bySlug.set(titleSlug, note.path);
  }
  const nodes = notes.map((n) => ({ id: n.path, title: n.title, type: n.type }));
  const edges = [];
  const seen = new Set();
  for (const note of notes) {
    for (const target of extractLinks(note.body)) {
      const targetPath = bySlug.get(target);
      if (!targetPath || targetPath === note.path) continue;
      const key = [note.path, targetPath].sort().join("\u0000");
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({ source: note.path, target: targetPath });
    }
  }
  return { nodes, edges };
}

export function resolveLink(notes, target) {
  const slug = slugify(target);
  return notes.find((n) => n.slug === slug) || notes.find((n) => slugify(n.title) === slug) || null;
}

export function backlinks(notes, path) {
  const target = notes.find((n) => n.path === path);
  if (!target) return [];
  const names = new Set([target.slug, slugify(target.title)]);
  return notes
    .filter((n) => n.path !== path && extractLinks(n.body).some((link) => names.has(link)))
    .map((n) => n.path);
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
  out.push("## How to use this file", "");
  out.push(
    "Read this before working on the project. Details live in the linked files; " +
      "decisions in force are binding unless a newer decision supersedes them.",
  );
  return out.join("\n") + "\n";
}
