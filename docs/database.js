// Databases: Notion-style live views of your notes, written as a ```database block inside any note.
//
//   ```database
//   title: Release work
//   from: tasks                 folder (optional)
//   type: task                  note types, comma separated (optional)
//   tag: release                every tag must match (optional)
//   where: status != done       one condition per line: = != > < >= <= contains, or "has x" / "no x"
//   sort: due asc               any property, or title / created / updated
//   view: table                 table | board | gallery | list | calendar
//   group: status               the board's columns (default: status)
//   columns: status, tags, due  properties shown next to the title
//   date: due                   the calendar's date property (default: due, then date, then created)
//   limit: 100
//   ```
//
// Unlike Notion, every row is still a plain Markdown file: edits in the table write the note's front matter,
// so Obsidian, GitHub and AI assistants see exactly the same data. Switching the view rewrites the block's
// `view:` line, so the choice is saved in the note itself.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { STATUS_LABEL, ask, icon, toast, today } from "./ui.js";
import { noteByPath, readOnly, saveNote, state, upsertFile } from "./state.js";
import { flushPending, openNote } from "./drawer.js";

export const VIEWS = ["table", "board", "gallery", "list", "calendar"];
const VIEW_ICONS = { table: "columns", board: "board", gallery: "canvas", list: "list", calendar: "journal" };
const OPERATORS = [">=", "<=", "!=", "=", ">", "<", " contains "];

// ============================================================ parsing and querying (pure, tested)

export function parseDatabase(source) {
  const spec = { title: "", from: "", type: [], tag: [], where: [], sort: [], view: "table", group: "status", columns: null, date: "", limit: 200 };
  for (const raw of String(source).split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || line.startsWith("//") || !line.includes(":")) continue;
    const key = line.slice(0, line.indexOf(":")).trim().toLowerCase();
    const value = line.slice(line.indexOf(":") + 1).trim();
    const list = value.split(",").map((v) => v.trim()).filter(Boolean);
    if (key === "title" || key === "name") spec.title = value;
    else if (key === "from" || key === "folder") spec.from = value.replace(/^\/+|\/+$/g, "");
    else if (key === "type" || key === "types") spec.type = list.map((v) => v.toLowerCase());
    else if (key === "tag" || key === "tags") spec.tag = list.map((v) => v.replace(/^#/, "").toLowerCase());
    else if (key === "where" || key === "filter") {
      const condition = parseCondition(value);
      if (condition) spec.where.push(condition);
    } else if (key === "sort") {
      for (const part of list) {
        const [field, order] = part.split(/\s+/);
        if (field) spec.sort.push({ key: field.toLowerCase(), desc: /^desc/i.test(order || "") });
      }
    } else if (key === "view") spec.view = VIEWS.includes(value.toLowerCase()) ? value.toLowerCase() : "table";
    else if (key === "group" || key === "group by") spec.group = value.toLowerCase() || "status";
    else if (key === "columns" || key === "properties" || key === "show") spec.columns = list.map((v) => v.toLowerCase());
    else if (key === "date") spec.date = value.toLowerCase();
    else if (key === "limit") spec.limit = Math.max(1, Math.min(1000, Number(value) || 200));
  }
  return spec;
}

export function parseCondition(text) {
  const value = String(text || "").trim();
  const has = /^(has|no)\s+([\w-]+)$/i.exec(value);
  if (has) return { key: has[2].toLowerCase(), op: has[1].toLowerCase() === "has" ? "has" : "no", value: "" };
  for (const op of OPERATORS) {
    const index = value.toLowerCase().indexOf(op);
    if (index > 0) {
      return {
        key: value.slice(0, index).trim().toLowerCase(),
        op: op.trim(),
        value: value.slice(index + op.length).trim().replace(/^["']|["']$/g, ""),
      };
    }
  }
  return null;
}

// A note's value for a property, with a few names that always work.
export function propValue(note, key) {
  switch (key) {
    case "title": return note.title;
    case "type": return note.type;
    case "status": return note.status || "";
    case "tags": return note.tags || [];
    case "path": return note.path;
    case "folder": return note.path.includes("/") ? note.path.slice(0, note.path.lastIndexOf("/")) : "";
    case "created": return String(note.meta?.created || "");
    case "updated": return note.mtime ? new Date(note.mtime).toISOString().slice(0, 10) : "";
    default: {
      const meta = note.meta || {};
      const found = Object.keys(meta).find((k) => k.toLowerCase() === key);
      return found === undefined ? undefined : meta[found];
    }
  }
}

const isEmpty = (value) => value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length);
const asNumber = (value) => (typeof value === "number" ? value : /^-?\d+(\.\d+)?$/.test(String(value).trim()) ? Number(value) : null);

function compare(a, b) {
  const x = asNumber(a);
  const y = asNumber(b);
  if (x !== null && y !== null) return x - y;
  return String(a ?? "").localeCompare(String(b ?? ""), undefined, { numeric: true, sensitivity: "base" });
}

export function matches(note, condition) {
  const value = propValue(note, condition.key);
  if (condition.op === "has") return !isEmpty(value);
  if (condition.op === "no") return isEmpty(value);
  const wanted = condition.value.toLowerCase();
  const values = (Array.isArray(value) ? value : [value]).map((v) => String(v ?? "").toLowerCase());
  switch (condition.op) {
    case "=": return wanted === "" ? isEmpty(value) : values.includes(wanted);
    case "!=": return wanted === "" ? !isEmpty(value) : !values.includes(wanted);
    case "contains": return values.some((v) => v.includes(wanted));
    case ">": return !isEmpty(value) && compare(value, condition.value) > 0;
    case "<": return !isEmpty(value) && compare(value, condition.value) < 0;
    case ">=": return !isEmpty(value) && compare(value, condition.value) >= 0;
    case "<=": return !isEmpty(value) && compare(value, condition.value) <= 0;
    default: return true;
  }
}

export function runDatabase(notes, spec, selfPath = "") {
  const from = spec.from.toLowerCase();
  const rows = notes.filter((note) => {
    if (note.path === selfPath) return false;
    if (from && !note.path.toLowerCase().startsWith(`${from}/`)) return false;
    if (spec.type.length && !spec.type.includes(note.type)) return false;
    const tags = (note.tags || []).map((t) => String(t).toLowerCase());
    if (spec.tag.length && !spec.tag.every((tag) => tags.includes(tag))) return false;
    return spec.where.every((condition) => matches(note, condition));
  });
  const sorts = spec.sort.length ? spec.sort : [{ key: "updated", desc: true }];
  rows.sort((a, b) => {
    for (const { key, desc } of sorts) {
      const x = propValue(a, key);
      const y = propValue(b, key);
      // Empty values sort last whichever way you sort.
      if (isEmpty(x) !== isEmpty(y)) return isEmpty(x) ? 1 : -1;
      const order = key === "updated" ? (a.mtime || 0) - (b.mtime || 0) : compare(Array.isArray(x) ? x.join(",") : x, Array.isArray(y) ? y.join(",") : y);
      if (order) return desc ? -order : order;
    }
    return a.title.localeCompare(b.title);
  });
  return { total: rows.length, rows: rows.slice(0, spec.limit) };
}

// Columns to show: the ones asked for, else the properties the rows actually use (most common first).
export function columnsFor(spec, rows) {
  if (spec.columns) return spec.columns.filter((c) => c !== "title");
  const counts = new Map();
  for (const note of rows) {
    for (const key of Object.keys(note.meta || {})) {
      const name = key.toLowerCase();
      if (["title", "type", "created", "cairn"].includes(name)) continue;
      counts.set(name, (counts.get(name) || 0) + 1);
    }
  }
  const order = ["status", "tags"];
  return [...counts.entries()]
    .sort((a, b) => (order.indexOf(a[0]) + 1 || 99) - (order.indexOf(b[0]) + 1 || 99) || b[1] - a[1])
    .slice(0, 6)
    .map(([name]) => name);
}

// Board columns: a type's statuses in their natural order, else every value in use; empty values last.
export function groupRows(rows, key, spec = {}) {
  const groups = new Map();
  const type = spec.type?.length === 1 ? spec.type[0] : rows[0]?.type;
  if (key === "status" && core.STATUSES[type] && rows.every((n) => n.type === type)) {
    for (const status of core.STATUSES[type]) groups.set(status, []);
  }
  for (const note of rows) {
    const value = propValue(note, key);
    const keys = Array.isArray(value) ? (value.length ? value.map(String) : [""]) : [isEmpty(value) ? "" : String(value)];
    for (const k of keys) {
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(note);
    }
  }
  const entries = [...groups.entries()];
  const none = entries.filter(([k]) => k === "");
  return [...entries.filter(([k]) => k !== ""), ...none].map(([value, list]) => ({ value, rows: list }));
}

// Weeks of a month (Monday first) with the rows that fall on each day.
export function calendarWeeks(rows, key, year, month) {
  const first = new Date(Date.UTC(year, month, 1));
  const start = new Date(first);
  start.setUTCDate(1 - ((first.getUTCDay() + 6) % 7));
  const byDay = new Map();
  for (const note of rows) {
    const day = String(propValue(note, key) ?? "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) continue;
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push(note);
  }
  const weeks = [];
  const cursor = new Date(start);
  do {
    const week = [];
    for (let i = 0; i < 7; i += 1) {
      const iso = cursor.toISOString().slice(0, 10);
      week.push({ date: iso, inMonth: cursor.getUTCMonth() === month, rows: byDay.get(iso) || [] });
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    weeks.push(week);
  } while (cursor.getUTCMonth() === month);
  return weeks;
}

// Properties a new row needs so it shows up in this database: its type, tags and any "key = value" filters.
export function defaultsFor(spec) {
  const meta = {};
  if (spec.type.length === 1) meta.type = spec.type[0];
  if (spec.tag.length) meta.tags = [...spec.tag];
  for (const condition of spec.where) {
    if (condition.op === "=" && condition.value && !["title", "path", "folder", "updated"].includes(condition.key)) {
      meta[condition.key] = condition.key === "tags" ? [...new Set([...(meta.tags || []), condition.value])] : condition.value;
    }
  }
  return meta;
}

// A block's source with its `key:` line set (added at the end if missing).
export function setOption(source, key, value) {
  const line = new RegExp(`^([ \\t]*${key}[ \\t]*:).*$`, "im");
  return line.test(source) ? source.replace(line, `$1 ${value}`) : `${source.replace(/\n+$/, "")}\n${key}: ${value}`;
}

// The body with one ```database block's `key:` line set. Returns null if that block isn't in the body.
export function setBlockOption(body, source, key, value) {
  const fence = /(^|\n)(```+|~~~+)[ \t]*database[ \t]*\n([\s\S]*?)\n\2[ \t]*(?=\n|$)/g;
  let match;
  while ((match = fence.exec(body))) {
    const inner = match[3];
    if (inner.trim() !== String(source).trim()) continue;
    const innerStart = match.index + match[0].indexOf("\n", match[1].length) + 1;
    return body.slice(0, innerStart) + setOption(inner, key, value) + body.slice(innerStart + inner.length);
  }
  return null;
}

export function displayValue(value) {
  if (isEmpty(value)) return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// ============================================================ rendering

const blocks = new WeakMap();

function chip(key, value) {
  if (isEmpty(value)) return "";
  if (key === "tags") return (Array.isArray(value) ? value : [value]).map((t) => `<span class="tag" data-tag-link="${esc(t)}">#${esc(t)}</span>`).join(" ");
  if (key === "status") return `<span class="pill status-${esc(value)}">${esc(STATUS_LABEL[value] || value)}</span>`;
  if (value === true || value === false) return value ? "✓" : "";
  const text = displayValue(value);
  if (/^https?:\/\//.test(text)) return `<a href="${esc(text)}" target="_blank" rel="noopener">${esc(text.replace(/^https?:\/\//, "").slice(0, 40))}</a>`;
  return esc(text);
}

function titleLink(note) {
  return `<a class="wikilink db-title" href="#" data-open="${esc(note.path)}">${esc(note.title)}</a>`;
}

function cell(note, key, editable) {
  const value = propValue(note, key);
  const attrs = editable && !["type", "path", "folder", "updated"].includes(key) ? ` data-db-edit="${esc(key)}" tabindex="0" title="Click to edit"` : "";
  return `<td data-path="${esc(note.path)}"${attrs}>${chip(key, value) || `<span class="db-empty">${attrs ? "+" : ""}</span>`}</td>`;
}

function snippet(note) {
  return esc(note.body.replace(/```[\s\S]*?```/g, " ").replace(/[#>*_`[\]|-]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 140));
}

function tableHtml(rows, columns, editable) {
  return `<div class="db-scroll"><table class="db-table"><thead><tr><th>Name</th>${columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((note) => `<tr><td class="db-name">${titleLink(note)}</td>${columns.map((c) => cell(note, c, editable)).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function boardHtml(rows, spec, columns, editable) {
  const groups = groupRows(rows, spec.group, spec);
  const label = (value) => (value === "" ? `No ${spec.group}` : spec.group === "status" ? STATUS_LABEL[value] || value : value);
  return `<div class="db-board">${groups.map((group) => `
    <section class="db-col" data-db-group="${esc(group.value)}">
      <header><span>${esc(label(group.value))}</span><span class="count">${group.rows.length}</span></header>
      <div class="db-cards">${group.rows.map((note) => `
        <article class="db-card" ${editable ? `draggable="true"` : ""} data-path="${esc(note.path)}">
          ${titleLink(note)}
          <div class="db-chips">${columns.filter((c) => c !== spec.group).map((c) => chip(c, propValue(note, c))).filter(Boolean).join(" ")}</div>
        </article>`).join("")}</div>
      ${editable ? `<button type="button" class="db-add-in" data-db-new="${esc(group.value)}">${icon("plus", "sm")} New</button>` : ""}
    </section>`).join("")}</div>`;
}

function galleryHtml(rows, columns) {
  return `<div class="db-gallery">${rows.map((note) => `
    <article class="db-tile">
      ${titleLink(note)}
      <p>${snippet(note)}</p>
      <div class="db-chips">${columns.map((c) => chip(c, propValue(note, c))).filter(Boolean).join(" ")}</div>
    </article>`).join("")}</div>`;
}

function listHtml(rows, columns) {
  return `<ul class="db-list">${rows.map((note) => `<li>${titleLink(note)}<span class="db-chips">${columns.slice(0, 3).map((c) => chip(c, propValue(note, c))).filter(Boolean).join(" ")}</span></li>`).join("")}</ul>`;
}

function calendarHtml(rows, spec, cursor) {
  const key = spec.date || ["due", "date", "created"].find((k) => rows.some((n) => !isEmpty(propValue(n, k)))) || "created";
  const weeks = calendarWeeks(rows, key, cursor.year, cursor.month);
  const name = new Date(Date.UTC(cursor.year, cursor.month, 1)).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
  const now = today();
  return `<div class="db-cal">
    <div class="db-cal-head"><button type="button" class="btn sm ghost" data-db-month="-1" aria-label="Previous month">‹</button><strong>${esc(name)}</strong><button type="button" class="btn sm ghost" data-db-month="1" aria-label="Next month">›</button><span class="muted">by ${esc(key)}</span></div>
    <div class="db-cal-grid">${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => `<div class="db-cal-dow">${d}</div>`).join("")}
      ${weeks.flat().map((day) => `<div class="db-cal-day${day.inMonth ? "" : " out"}${day.date === now ? " today" : ""}"><span>${Number(day.date.slice(8))}</span>${day.rows.map(titleLink).join("")}</div>`).join("")}
    </div></div>`;
}

function draw(el) {
  const block = blocks.get(el);
  if (!block) return;
  const spec = parseDatabase(block.source);
  if (block.view) spec.view = block.view;
  const { total, rows } = runDatabase(state.notes, spec, block.notePath);
  const columns = columnsFor(spec, rows);
  const editable = !readOnly() && Boolean(state.store);
  const tabs = VIEWS.map((v) => `<button type="button" class="${spec.view === v ? "on" : ""}" data-db-view="${v}" aria-pressed="${spec.view === v}">${icon(VIEW_ICONS[v], "sm")}<span>${v[0].toUpperCase()}${v.slice(1)}</span></button>`).join("");
  let content;
  if (!rows.length && spec.view !== "calendar") content = `<p class="db-none">No notes match yet.${editable ? " Press <strong>New</strong> to add the first one." : ""}</p>`;
  else if (spec.view === "board") content = boardHtml(rows, spec, columns, editable);
  else if (spec.view === "gallery") content = galleryHtml(rows, columns);
  else if (spec.view === "list") content = listHtml(rows, columns);
  else if (spec.view === "calendar") content = calendarHtml(rows, spec, block.cursor);
  else content = tableHtml(rows, columns, editable);
  el.innerHTML = `
    <div class="db-head">
      <span class="db-name-label">${icon("columns", "sm")} ${esc(spec.title || "Database")}</span>
      <span class="count" title="${total} matching notes">${total}</span>
      <div class="segmented db-views" role="group" aria-label="View">${tabs}</div>
      ${editable ? `<button type="button" class="btn sm" data-db-new="">${icon("plus", "sm")} New</button>` : ""}
    </div>
    ${content}
    ${total > rows.length ? `<p class="db-more muted">Showing ${rows.length} of ${total}. Raise <code>limit:</code> to see more.</p>` : ""}`;
}

// ============================================================ editing

async function setProperty(path, key, raw) {
  const note = noteByPath(path);
  if (!note) return;
  const text = String(raw).trim();
  if (key === "title") return saveNote(path, { title: text || note.title });
  if (key === "status") return saveNote(path, { status: text });
  if (key === "tags") return saveNote(path, { tags: text.split(",").map((t) => t.trim().replace(/^#/, "").toLowerCase()).filter(Boolean) });
  let value = text;
  if (!text) value = core.REMOVE;
  else if (/^(true|false)$/i.test(text)) value = text.toLowerCase() === "true";
  else if (/^-?\d+(\.\d+)?$/.test(text)) value = Number(text);
  else if (Array.isArray(propValue(note, key))) value = text.split(",").map((t) => t.trim()).filter(Boolean);
  return saveNote(path, { properties: { [key]: value } });
}

function editCell(el, td) {
  const path = td.dataset.path;
  const key = td.dataset.dbEdit;
  const note = noteByPath(path);
  if (!note || td.querySelector("input, select")) return;
  const current = propValue(note, key);
  const statuses = key === "status" ? core.STATUSES[note.type] : null;
  td.innerHTML = statuses
    ? `<select class="db-input">${statuses.map((s) => `<option value="${s}" ${s === current ? "selected" : ""}>${esc(STATUS_LABEL[s] || s)}</option>`).join("")}</select>`
    : `<input class="db-input" value="${esc(displayValue(current))}" ${/date|due|when|deadline/.test(key) ? 'placeholder="YYYY-MM-DD"' : ""}>`;
  const input = td.querySelector(".db-input");
  input.focus();
  if (input.select) input.select();
  let done = false;
  const commit = async (save) => {
    if (done) return;
    done = true;
    if (save && input.value !== displayValue(current)) await setProperty(path, key, input.value);
    draw(el);
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commit(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      commit(false);
    }
  });
  input.addEventListener("change", () => statuses && commit(true));
  input.addEventListener("blur", () => commit(true));
}

// A new row: a note in the database's folder with the properties that make it match.
async function newRow(el, groupValue) {
  const block = blocks.get(el);
  const spec = parseDatabase(block.source);
  const title = await ask({ title: `New in ${spec.title || "this database"}`, placeholder: "Name", confirm: "Create" });
  if (!title) return;
  const meta = { title, ...defaultsFor(spec) };
  const type = core.TYPES.includes(meta.type) ? meta.type : "note";
  meta.type = type;
  if (groupValue !== null && groupValue !== undefined && groupValue !== "") {
    if (spec.group === "tags") meta.tags = [...new Set([...(meta.tags || []), groupValue])];
    else meta[spec.group] = groupValue;
  }
  if (!meta.status && core.defaultStatus(type)) meta.status = core.defaultStatus(type);
  if (!meta.tags) meta.tags = [];
  meta.created = today();
  const folder = spec.from || core.FOLDERS[type] || "";
  const taken = new Set(state.files.map((f) => f.path.toLowerCase()));
  const base = core.slugify(title) || "untitled";
  let path = `${folder ? `${folder}/` : ""}${base}.md`;
  for (let n = 2; taken.has(path.toLowerCase()); n += 1) path = `${folder ? `${folder}/` : ""}${base}-${n}.md`;
  const text = core.serializeNote(meta, core.TEMPLATES[type] ?? "");
  try {
    const { mtime } = await state.store.write(path, text, null);
    upsertFile(path, text, mtime);
  } catch (error) {
    return toast(error.message || "Couldn't create that note.", { error: true });
  }
  toast(`Added ${title}`);
  draw(el);
}

// Save the chosen view into the block, so the note opens the same way next time (and on other devices).
async function rememberView(el, viewName) {
  const block = blocks.get(el);
  block.view = viewName;
  draw(el);
  const note = noteByPath(block.notePath);
  if (!note || readOnly()) return;
  await flushPending();
  const fresh = noteByPath(block.notePath);
  const body = setBlockOption(fresh.body, block.source, "view", viewName);
  if (!body || body === fresh.body) return;
  block.source = setOption(block.source, "view", viewName);
  await saveNote(block.notePath, { body });
  const editor = document.getElementById("ed-body");
  if (editor && state.open === block.notePath) editor.value = body;
}

function wire(el) {
  el.addEventListener("click", async (e) => {
    const viewButton = e.target.closest("[data-db-view]");
    if (viewButton) return rememberView(el, viewButton.dataset.dbView);
    const add = e.target.closest("[data-db-new]");
    if (add) return newRow(el, add.dataset.dbNew);
    const month = e.target.closest("[data-db-month]");
    if (month) {
      const block = blocks.get(el);
      const next = new Date(Date.UTC(block.cursor.year, block.cursor.month + Number(month.dataset.dbMonth), 1));
      block.cursor = { year: next.getUTCFullYear(), month: next.getUTCMonth() };
      return draw(el);
    }
    const editable = e.target.closest("[data-db-edit]");
    if (editable && !e.target.closest("a, [data-tag-link]")) return editCell(el, editable);
    const open = e.target.closest(".db-title[data-open]");
    if (open && !e.altKey) {
      // Opening a row from a database inside the open note keeps that note as a tab to come back to.
      e.preventDefault();
      e.stopPropagation();
      openNote(open.dataset.open, { newTab: Boolean(state.open) });
    }
  });
  el.addEventListener("keydown", (e) => {
    const editable = e.target.closest?.("[data-db-edit]");
    if (editable && (e.key === "Enter" || e.key === " ") && e.target === editable) {
      e.preventDefault();
      editCell(el, editable);
    }
  });
  // Board: drag a card to another column to change that property.
  el.addEventListener("dragstart", (e) => {
    const card = e.target.closest?.(".db-card");
    if (!card) return;
    e.dataTransfer.setData("text/cairn-db", card.dataset.path);
    e.dataTransfer.setData("text/cairn-db-from", card.closest("[data-db-group]")?.dataset.dbGroup || "");
    e.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
  });
  el.addEventListener("dragend", (e) => e.target.closest?.(".db-card")?.classList.remove("dragging"));
  el.addEventListener("dragover", (e) => {
    const column = e.target.closest?.("[data-db-group]");
    if (!column || !e.dataTransfer.types.includes("text/cairn-db")) return;
    e.preventDefault();
    column.classList.add("over");
  });
  el.addEventListener("dragleave", (e) => e.target.closest?.("[data-db-group]")?.classList.remove("over"));
  el.addEventListener("drop", async (e) => {
    const column = e.target.closest?.("[data-db-group]");
    const path = e.dataTransfer.getData("text/cairn-db");
    if (!column || !path) return;
    e.preventDefault();
    const block = blocks.get(el);
    const spec = parseDatabase(block.source);
    const value = column.dataset.dbGroup;
    const from = e.dataTransfer.getData("text/cairn-db-from");
    const note = noteByPath(path);
    if (!note || from === value) return draw(el);
    if (spec.group === "tags") {
      await saveNote(path, { tags: [...new Set([...(note.tags || []).filter((t) => t !== from), value].filter(Boolean))] });
    } else {
      await setProperty(path, spec.group, value);
    }
    draw(el);
  });
}

// Turn every ```database code block on the page into a live database.
export function enhanceDatabases(root = document) {
  for (const code of root.querySelectorAll("pre > code.language-database:not([data-db])")) {
    code.dataset.db = "1";
    const pre = code.parentElement;
    const el = document.createElement("div");
    el.className = "db";
    const notePath = pre.closest("[data-embed]")?.dataset.embed || pre.closest("[data-split-path]")?.dataset.splitPath || (pre.closest("#ed-preview, .drawer") ? state.open : null) || "";
    const now = new Date();
    blocks.set(el, { source: code.textContent.replace(/\n$/, ""), notePath, view: null, cursor: { year: now.getFullYear(), month: now.getMonth() } });
    pre.replaceWith(el);
    wire(el);
    draw(el);
  }
}

export function initDatabases() {
  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      enhanceDatabases(document);
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
  enhanceDatabases(document);
}

// A starter block for the slash menu and the palette: a board of open tasks.
export const DATABASE_EXAMPLE = "```database\ntitle: Open tasks\ntype: task\nwhere: status != done\nview: table\ncolumns: status, tags, due\nsort: due asc\n```\n";
