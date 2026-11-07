// Canvas: an infinite board of cards, notes, links and groups joined by arrows, saved as an
// Obsidian-compatible .canvas file (the open JSON Canvas format, jsoncanvas.org). Boards made in
// Obsidian open here and boards made here open in Obsidian; fields Cairn doesn't know are kept.

import * as core from "./core.js";
import { escapeHtml as esc, sectionOf } from "./md.js";
import { $, ask, emptyState, icon, pageHead, plural, relativeTime, toast } from "./ui.js";
import { fileByPath, noteByPath, readOnly, scheduleGitRefresh, state, upsertFile } from "./state.js";
import { ConflictError } from "./store.js";
import { hydrateAssets, renderNote, resolveFrom } from "./vault.js";
import { openNote } from "./drawer.js";
import { pick } from "./palette.js";
import { renderView, setView } from "./app.js";

const FOLDER = "canvases";
const VIEW_KEY = "cairn.canvas.views";
const GRID = 10;
const MIN_W = 80;
const MIN_H = 40;
const SIDES = ["top", "right", "bottom", "left"];
const NORMAL = { top: [0, -1], right: [1, 0], bottom: [0, 1], left: [-1, 0] };
// JSON Canvas preset colours 1-6. Files keep the spec numbers; Cairn draws preset 6 (purple in Obsidian) as slate.
export const PRESET_COLORS = { 1: "#e5484d", 2: "#e9973f", 3: "#d6b82d", 4: "#3fae5f", 5: "#3aaeb6", 6: "#6b7f95" };

// ============================================================ the file format

export function parseCanvas(text) {
  let data;
  try {
    data = JSON.parse(String(text ?? "").trim() || "{}");
  } catch {
    return null;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const number = (value, fallback) => (Number.isFinite(Number(value)) ? Number(value) : fallback);
  data.nodes = (Array.isArray(data.nodes) ? data.nodes : []).filter((n) => n && typeof n === "object" && typeof n.id === "string");
  for (const node of data.nodes) {
    node.x = number(node.x, 0);
    node.y = number(node.y, 0);
    node.width = Math.max(MIN_W / 2, number(node.width, 250));
    node.height = Math.max(MIN_H / 2, number(node.height, 60));
  }
  const ids = new Set(data.nodes.map((n) => n.id));
  data.edges = (Array.isArray(data.edges) ? data.edges : [])
    .filter((e) => e && typeof e === "object" && typeof e.id === "string" && ids.has(e.fromNode) && ids.has(e.toNode));
  return data;
}

// Obsidian writes canvases as tab-indented JSON; doing the same keeps git diffs small both ways.
export const serializeCanvas = (data) => JSON.stringify(data, null, "\t");

export function colorOf(value) {
  if (value === undefined || value === null || value === "") return null;
  const preset = PRESET_COLORS[String(value)];
  if (preset) return preset;
  return /^#[0-9a-f]{3,8}$/i.test(String(value)) ? String(value) : null;
}

const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join("");
const snap = (value) => Math.round(value / GRID) * GRID;
const canvasName = (path) => path.split("/").pop().replace(/\.canvas$/i, "");

export function canvasFiles() {
  return state.files.filter((f) => core.isCanvasPath(f.path));
}

// ============================================================ geometry

function sidePoint(node, side) {
  if (side === "top") return [node.x + node.width / 2, node.y];
  if (side === "bottom") return [node.x + node.width / 2, node.y + node.height];
  if (side === "left") return [node.x, node.y + node.height / 2];
  return [node.x + node.width, node.y + node.height / 2];
}

function autoSides(a, b) {
  const dx = b.x + b.width / 2 - (a.x + a.width / 2);
  const dy = b.y + b.height / 2 - (a.y + a.height / 2);
  if (Math.abs(dx) * (a.height + b.height) >= Math.abs(dy) * (a.width + b.width)) return dx >= 0 ? ["right", "left"] : ["left", "right"];
  return dy >= 0 ? ["bottom", "top"] : ["top", "bottom"];
}

export function nearestSide(node, x, y) {
  let best = "top";
  let bestDistance = Infinity;
  for (const side of SIDES) {
    const [px, py] = sidePoint(node, side);
    const distance = Math.hypot(px - x, py - y);
    if (distance < bestDistance) {
      best = side;
      bestDistance = distance;
    }
  }
  return best;
}

export function edgeGeometry(edge, from, to) {
  const [autoFrom, autoTo] = autoSides(from, to);
  const fromSide = SIDES.includes(edge.fromSide) ? edge.fromSide : autoFrom;
  const toSide = SIDES.includes(edge.toSide) ? edge.toSide : autoTo;
  return curve(sidePoint(from, fromSide), fromSide, sidePoint(to, toSide), toSide);
}

function curve(p1, fromSide, p2, toSide) {
  const distance = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
  const pull = Math.max(30, Math.min(180, distance * 0.45));
  const c1 = [p1[0] + NORMAL[fromSide][0] * pull, p1[1] + NORMAL[fromSide][1] * pull];
  const c2 = toSide ? [p2[0] + NORMAL[toSide][0] * pull, p2[1] + NORMAL[toSide][1] * pull] : p2;
  const at = (t) => {
    const u = 1 - t;
    return [0, 1].map((i) => u * u * u * p1[i] + 3 * u * u * t * c1[i] + 3 * u * t * t * c2[i] + t * t * t * p2[i]);
  };
  const r = (n) => Math.round(n * 10) / 10;
  return { d: `M${r(p1[0])},${r(p1[1])} C${r(c1[0])},${r(c1[1])} ${r(c2[0])},${r(c2[1])} ${r(p2[0])},${r(p2[1])}`, mid: at(0.5) };
}

function bounds(nodes) {
  if (!nodes.length) return { x: 0, y: 0, width: 0, height: 0 };
  const minX = Math.min(...nodes.map((n) => n.x));
  const minY = Math.min(...nodes.map((n) => n.y));
  const maxX = Math.max(...nodes.map((n) => n.x + n.width));
  const maxY = Math.max(...nodes.map((n) => n.y + n.height));
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

const inside = (node, group) =>
  node !== group && node.x >= group.x && node.y >= group.y && node.x + node.width <= group.x + group.width && node.y + node.height <= group.y + group.height;

// ============================================================ the open board

const board = {
  path: null,
  data: null,
  text: null,
  view: { x: 0, y: 0, zoom: 1 },
  selected: new Set(),
  edge: null,
  editing: null,
  panel: false,
  history: [],
  future: [],
  dirty: false,
};
let saveTimer = null;
let saving = Promise.resolve();
let wired = null;

const snapshot = () => JSON.stringify(board.data);

function savedViews() {
  try {
    return JSON.parse(localStorage.getItem(VIEW_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

function rememberView() {
  if (!board.path) return;
  const views = savedViews();
  views[board.path] = { x: Math.round(board.view.x), y: Math.round(board.view.y), zoom: Math.round(board.view.zoom * 1000) / 1000 };
  const keys = Object.keys(views);
  if (keys.length > 60) delete views[keys[0]];
  localStorage.setItem(VIEW_KEY, JSON.stringify(views));
}

function loadBoard(path) {
  const file = fileByPath(path);
  const data = file ? parseCanvas(file.text) : null;
  const samePath = board.path === path;
  board.path = path;
  board.text = file?.text ?? null;
  board.data = data;
  board.edge = null;
  board.editing = null;
  if (!samePath) {
    board.selected = new Set();
    board.history = [];
    board.future = [];
    board.view = savedViews()[path] || null;
  } else if (data) {
    const ids = new Set(data.nodes.map((n) => n.id));
    board.selected = new Set([...board.selected].filter((id) => ids.has(id)));
  }
}

// ============================================================ view (list + board shell)

export function canvasView() {
  const path = state.canvas;
  if (path && fileByPath(path)) return boardShell(path);
  if (path) state.canvas = null;
  const files = canvasFiles().sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
  const action = readOnly() ? "" : `<button class="btn primary" data-cv-new>${icon("plus", "sm")} New canvas</button>`;
  const head = pageHead("Canvas", "Lay notes, cards and links out on an open board and draw the connections. Saved as .canvas files that Obsidian opens too.", action, "Remember");
  if (!files.length) {
    return `${head}${emptyState("canvas", "No canvases yet", "A canvas is a whiteboard for thinking: drop in notes, write cards, group them and draw arrows between them.", action)}`;
  }
  const cards = files.map((file) => {
    const data = parseCanvas(file.text);
    const counts = data
      ? `${plural(data.nodes.filter((n) => n.type !== "group").length, "card")} · ${plural(data.edges.length, "connection")}`
      : "Can't read this file";
    const folder = file.path.includes("/") ? file.path.slice(0, file.path.lastIndexOf("/")) : "";
    return `<button class="cv-card" data-cv-open="${esc(file.path)}">
      ${icon("canvas")}
      <span class="cv-card-name">${esc(canvasName(file.path))}</span>
      <span class="cv-card-meta">${esc(counts)}</span>
      <span class="cv-card-meta">${folder ? `${esc(folder)}/ · ` : ""}${file.mtime ? relativeTime(file.mtime) : ""}</span>
    </button>`;
  }).join("");
  return `${head}<div class="cv-grid">${cards}</div>`;
}

function boardShell(path) {
  const editable = !readOnly();
  const tool = (name, iconName, label, title) =>
    `<button class="btn sm" data-cv="${name}" title="${title}">${icon(iconName, "sm")}<span>${label}</span></button>`;
  return `
    <div class="cv-page">
      <div class="cv-bar">
        <button class="btn sm ghost" data-cv="back" title="All canvases">${icon("arrow", "sm flip")}<span class="sr-only">All canvases</span></button>
        <div class="cv-heading"><strong class="cv-title">${esc(canvasName(path))}</strong><span class="cv-path">brain/${esc(path)}</span></div>
        <span class="grow"></span>
        ${editable ? `<div class="cv-tools">
          ${tool("add-text", "pencil", "Card", "Add a text card (or double-click the board)")}
          ${tool("add-note", "note", "Notes", "Show your notes: drag one onto the board, or click to place it")}
          ${tool("add-link", "link", "Link", "Add a web link")}
          ${tool("add-group", "columns", "Group", "Add a group to hold cards")}
        </div>` : ""}
        <div class="cv-zoom">
          <button class="btn sm ghost icon-only" data-cv="zoom-out" aria-label="Zoom out" title="Zoom out (-)">−</button>
          <button class="btn sm ghost cv-zoom-level" data-cv="zoom-reset" id="cv-zoom" title="Reset zoom">100%</button>
          <button class="btn sm ghost icon-only" data-cv="zoom-in" aria-label="Zoom in" title="Zoom in (+)">+</button>
          <button class="btn sm ghost icon-only" data-cv="fit" aria-label="Zoom to fit" title="Zoom to fit (Shift+1)">${icon("expand", "sm")}</button>
        </div>
        <span class="save-state" id="cv-save">${editable ? "Saved" : "Read-only"}</span>
      </div>
      <div class="cv-wrap" id="cv-wrap" tabindex="0" role="application" aria-roledescription="canvas" aria-label="Canvas ${esc(canvasName(path))}. Tab to move between cards, arrow keys move the selection, Enter edits or opens.">
        <div class="cv-world" id="cv-world"></div>
        <div class="cv-select-bar" id="cv-select-bar" hidden></div>
        <p class="cv-hint">${editable ? "Double-click to add a card · drag a dot on a card's edge to connect · " : ""}drag the board to pan · Shift + drag to select · Ctrl + scroll to zoom · <kbd>?</kbd> for all shortcuts</p>
        ${editable ? notesPanel() : ""}
      </div>
    </div>`;
}

// ============================================================ the notes panel (drag notes onto the board)

function notesPanel() {
  return `<aside class="cv-panel" id="cv-panel" aria-label="Notes to place on the canvas"${board.panel ? "" : " hidden"}>
    <div class="cv-panel-head"><strong>Notes</strong><button class="btn sm ghost icon-only" data-cv="add-note" aria-label="Close notes">${icon("x", "sm")}</button></div>
    <input class="input" id="cv-panel-filter" type="search" placeholder="Filter notes…" aria-label="Filter notes" autocomplete="off">
    <div class="cv-panel-list" id="cv-panel-list">${panelItems("")}</div>
    <p class="cv-panel-tip">Drag a note onto the board, or click it to drop it in the middle.</p>
  </aside>`;
}

function panelItems(filter) {
  const query = filter.trim().toLowerCase();
  const notes = [...state.notes]
    .filter((n) => !query || n.title.toLowerCase().includes(query) || n.path.toLowerCase().includes(query))
    .sort((a, b) => (b.mtime || 0) - (a.mtime || 0))
    .slice(0, 200);
  if (!notes.length) return `<p class="cv-panel-empty muted">${query ? "No notes match." : "No notes yet."}</p>`;
  return notes.map((n) => `<button class="cv-panel-item" draggable="true" data-cv-place="${esc(n.path)}" title="${esc(n.path)}">${icon("note", "sm")}<span>${esc(n.title)}</span></button>`).join("");
}

function togglePanel(open = !board.panel) {
  board.panel = open;
  const panel = $("#cv-panel");
  if (!panel) return;
  panel.hidden = !open;
  if (open) $("#cv-panel-filter")?.focus({ preventScroll: true });
  else $("#cv-wrap")?.focus({ preventScroll: true });
}

function placeNote(path, at = null) {
  const note = noteByPath(path);
  if (!note) return null;
  const size = { width: 400, height: 300 };
  const spot = at ? { x: at[0] - size.width / 2, y: at[1] - 40 } : {};
  return addNode({ type: "file", file: vaultRelative(note.path), ...size, ...spot });
}

// ============================================================ drawing

function nodeLabel(node) {
  if (node.type === "file") return `Note ${node.file || ""}`;
  if (node.type === "link") return `Link ${node.url || ""}`;
  if (node.type === "group") return `Group ${node.label || ""}`;
  return `Card ${(node.text || "").split("\n")[0].slice(0, 80)}`;
}

// A file node's path is relative to the vault. When the canvas lives in a synced Obsidian folder,
// that vault is the folder, so try the path as written, then inside the canvas's top folder, then by name.
export function resolveCanvasFile(file, canvasPath) {
  if (!file) return null;
  const clean = String(file).replace(/^\/+/, "");
  if (fileByPath(clean)) return clean;
  const top = canvasPath.includes("/") ? canvasPath.split("/")[0] : "";
  if (top && fileByPath(`${top}/${clean}`)) return `${top}/${clean}`;
  if (/\.md$/i.test(clean)) {
    const found = resolveFrom(canvasPath)(clean.replace(/\.md$/i, ""));
    if (found) return found.path;
  }
  return null;
}

function vaultRelative(path) {
  const folder = state.obsidian?.status?.linked ? state.obsidian.status.folder : "";
  return folder && board.path.startsWith(`${folder}/`) && path.startsWith(`${folder}/`) ? path.slice(folder.length + 1) : path;
}

function nodeBody(node) {
  if (node.type === "group") return `<div class="cv-group-label" data-cv-label>${esc(node.label || "")}</div>`;
  if (node.type === "link") {
    const url = String(node.url || "");
    const safe = /^https?:\/\//i.test(url);
    let host = url;
    try {
      host = new URL(url).hostname.replace(/^www\./, "");
    } catch {
      /* not a full URL: show as written */
    }
    return `<div class="cv-content cv-link">${icon("external", "sm")}${safe
      ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(host)}</a>`
      : `<strong>${esc(host || "No address")}</strong>`}<div class="cv-url">${esc(url)}</div></div>`;
  }
  if (node.type === "file") {
    const path = resolveCanvasFile(node.file, board.path);
    const note = path ? noteByPath(path) : null;
    if (note) {
      const source = node.subpath && node.subpath.startsWith("#") ? sectionOf(note.body, node.subpath.slice(1)) ?? note.body : note.body;
      return `<div class="cv-file-head">${icon("note", "sm")}<a class="wikilink" href="#" data-open="${esc(note.path)}">${esc(note.title)}</a></div>
        <div class="cv-content prose">${source.trim() ? renderNote(note, { source, tasks: false }) : '<p class="muted">Empty note</p>'}</div>`;
    }
    if (core.isCanvasPath(node.file || "") && path) {
      return `<div class="cv-file-head">${icon("canvas", "sm")}<button class="link-button" data-cv-open="${esc(path)}">${esc(canvasName(path))}</button></div>`;
    }
    if (node.file && core.isAttachment(node.file)) {
      return `<div class="cv-content prose cv-media">${renderNote({ path: board.path, body: "" }, { source: `![[${node.file}]]`, tasks: false })}</div>`;
    }
    return `<div class="cv-content cv-missing">${icon("note", "sm")} <span>Not found: ${esc(node.file || "no file")}</span></div>`;
  }
  const text = String(node.text ?? "");
  return `<div class="cv-content prose">${text.trim() ? renderNote({ path: board.path, body: text }, { source: text, tasks: false }) : `<p class="muted">${readOnly() ? "" : "Double-click to write"}</p>`}</div>`;
}

function nodeHtml(node) {
  const color = colorOf(node.color);
  const classes = ["cv-node", `cv-${node.type === "file" || node.type === "link" || node.type === "group" ? node.type : "text"}`];
  if (board.selected.has(node.id)) classes.push("selected");
  if (color) classes.push("colored");
  const style = `left:${node.x}px;top:${node.y}px;width:${node.width}px;height:${node.height}px;${color ? `--cv-color:${color};` : ""}`;
  const handles = readOnly() ? "" : SIDES.map((side) => `<span class="cv-handle" data-side="${side}" title="Drag to connect"></span>`).join("") + `<span class="cv-resize" title="Drag to resize"></span>`;
  return `<div class="${classes.join(" ")}" data-node="${esc(node.id)}" style="${style}" tabindex="0" role="group" aria-label="${esc(nodeLabel(node))}">${nodeBody(node)}${handles}</div>`;
}

function markerId(color) {
  return `cv-arrow-${(color || "default").replace(/[^a-z0-9]/gi, "")}`;
}

function edgesSvg() {
  const nodes = new Map(board.data.nodes.map((n) => [n.id, n]));
  const box = bounds(board.data.nodes);
  const pad = 400;
  const left = box.x - pad;
  const top = box.y - pad;
  const colors = new Set();
  const paths = [];
  for (const edge of board.data.edges) {
    const from = nodes.get(edge.fromNode);
    const to = nodes.get(edge.toNode);
    if (!from || !to) continue;
    const geometry = edgeGeometry(edge, from, to);
    const color = colorOf(edge.color);
    colors.add(color);
    const marker = markerId(color);
    const end = edge.toEnd === "none" ? "" : `marker-end="url(#${marker})"`;
    const start = edge.fromEnd === "arrow" ? `marker-start="url(#${marker})"` : "";
    const selected = board.edge === edge.id ? " selected" : "";
    paths.push(`<g class="cv-edge${selected}" data-edge="${esc(edge.id)}"${color ? ` style="--cv-color:${color}"` : ""}>
      <path class="cv-edge-hit" d="${geometry.d}"></path>
      <path class="cv-edge-line" d="${geometry.d}" ${end} ${start}></path>
      ${edge.label ? `<text class="cv-edge-label" x="${geometry.mid[0]}" y="${geometry.mid[1]}">${esc(edge.label)}</text>` : ""}
    </g>`);
  }
  const defs = [...colors].map((color) => `<marker id="${markerId(color)}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" style="fill:${color || "var(--cv-edge)"}"></path></marker>`).join("");
  return `<svg class="cv-edges" id="cv-edges" style="left:${left}px;top:${top}px;width:${box.width + pad * 2}px;height:${box.height + pad * 2}px" aria-hidden="true">
    <defs>${defs}</defs><g transform="translate(${-left},${-top})">${paths.join("")}<path class="cv-edge-draft" id="cv-draft" d=""></path></g></svg>`;
}

function draw() {
  const world = $("#cv-world");
  if (!world || !board.data) return;
  const groups = board.data.nodes.filter((n) => n.type === "group");
  const others = board.data.nodes.filter((n) => n.type !== "group");
  // Rebuilding the cards removes the focused one; keep keyboard focus on the board so the next
  // shortcut (paste again, Delete, undo) still reaches it.
  const hadFocus = world.contains(document.activeElement);
  const focusedId = hadFocus ? document.activeElement.closest("[data-node]")?.dataset.node : null;
  world.innerHTML = groups.map(nodeHtml).join("") + edgesSvg() + others.map(nodeHtml).join("");
  hydrateAssets(world, board.path);
  applyTransform();
  drawSelectBar();
  if (hadFocus) {
    const again = focusedId ? world.querySelector(`[data-node="${CSS.escape(focusedId)}"]`) : null;
    (again || $("#cv-wrap"))?.focus({ preventScroll: true });
  }
}

function redrawEdges() {
  const svg = $("#cv-edges");
  if (!svg) return;
  const holder = document.createElement("div");
  holder.innerHTML = edgesSvg();
  svg.replaceWith(holder.firstElementChild);
}

function moveNodeElement(node) {
  const el = $(`[data-node="${CSS.escape(node.id)}"]`);
  if (!el) return;
  el.style.left = `${node.x}px`;
  el.style.top = `${node.y}px`;
  el.style.width = `${node.width}px`;
  el.style.height = `${node.height}px`;
}

function applyTransform() {
  const world = $("#cv-world");
  if (!world) return;
  const { x, y, zoom } = board.view;
  world.style.transform = `translate(${x}px, ${y}px) scale(${zoom})`;
  const wrap = $("#cv-wrap");
  if (wrap) {
    wrap.style.backgroundSize = `${22 * zoom}px ${22 * zoom}px`;
    wrap.style.backgroundPosition = `${x}px ${y}px`;
  }
  const level = $("#cv-zoom");
  if (level) level.textContent = `${Math.round(zoom * 100)}%`;
}

function drawSelectBar() {
  const bar = $("#cv-select-bar");
  if (!bar) return;
  const count = board.selected.size + (board.edge ? 1 : 0);
  if (!count || readOnly()) {
    bar.hidden = true;
    return;
  }
  const swatches = Object.entries(PRESET_COLORS)
    .map(([key, value]) => `<button class="cv-swatch" data-cv-color="${key}" style="--swatch:${value}" aria-label="Colour ${key}" title="Colour"></button>`)
    .join("");
  const single = board.selected.size === 1 ? board.data.nodes.find((n) => board.selected.has(n.id)) : null;
  bar.innerHTML = `
    <span class="muted">${board.edge && !board.selected.size ? "Connection" : plural(board.selected.size, "card")}</span>
    <button class="cv-swatch none" data-cv-color="" aria-label="No colour" title="No colour"></button>${swatches}
    ${board.edge && !board.selected.size ? `<button class="btn sm ghost" data-cv="edge-label">${icon("pencil", "sm")} Label</button><button class="btn sm ghost" data-cv="edge-flip" title="Swap direction">${icon("refresh", "sm")}</button>` : ""}
    ${single?.type === "file" ? `<button class="btn sm ghost" data-cv="open-note">${icon("external", "sm")} Open</button>` : ""}
    ${single && (single.type === "text" || !single.type || single.type === "group" || single.type === "link") ? `<button class="btn sm ghost" data-cv="edit">${icon("pencil", "sm")} Edit</button>` : ""}
    ${board.selected.size ? `<button class="btn sm ghost icon-only" data-cv="duplicate" aria-label="Duplicate" title="Duplicate (Ctrl+D) · copy and paste with Ctrl+C / Ctrl+V">${icon("copy", "sm")}</button>` : ""}
    <button class="btn sm ghost danger" data-cv="delete" title="Delete (Del)">${icon("trash", "sm")}</button>`;
  bar.hidden = false;
}

// ============================================================ changes, history, saving

function mutate(change) {
  if (readOnly() || !board.data) return;
  const before = snapshot();
  change();
  if (snapshot() === before) return;
  board.history.push(before);
  if (board.history.length > 100) board.history.shift();
  board.future = [];
  draw();
  scheduleSave();
}

function finishGesture(before) {
  if (snapshot() === before) return;
  board.history.push(before);
  if (board.history.length > 100) board.history.shift();
  board.future = [];
  scheduleSave();
}

function undo(redo = false) {
  if (readOnly()) return;
  const from = redo ? board.future : board.history;
  const to = redo ? board.history : board.future;
  if (!from.length) return;
  to.push(snapshot());
  board.data = parseCanvas(from.pop()) || { nodes: [], edges: [] };
  const ids = new Set(board.data.nodes.map((n) => n.id));
  board.selected = new Set([...board.selected].filter((id) => ids.has(id)));
  board.edge = null;
  draw();
  scheduleSave();
}

function setSaveState(text, busy = false) {
  const el = $("#cv-save");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("saving", busy);
}

function scheduleSave() {
  board.dirty = true;
  setSaveState("Saving…", true);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saving = saving.then(saveNow, saveNow);
  }, state.info?.saveDelay || 500);
}

export function flushCanvas() {
  clearTimeout(saveTimer);
  if (board.dirty) saving = saving.then(saveNow, saveNow);
  return saving;
}

async function saveNow() {
  if (!board.dirty || !board.path || !board.data || readOnly()) return;
  board.dirty = false;
  const path = board.path;
  const text = serializeCanvas(board.data);
  const file = fileByPath(path);
  if (file && file.text === text) {
    setSaveState(state.info?.savedLabel || "Saved");
    return;
  }
  try {
    const { mtime } = await state.store.write(path, text, file ? file.mtime : null);
    upsertFile(path, text, mtime);
    if (board.path === path) board.text = text;
    scheduleGitRefresh();
    setSaveState(state.info?.savedLabel || "Saved");
  } catch (error) {
    if (error instanceof ConflictError && error.text !== null) {
      upsertFile(path, error.text, error.mtime);
      if (board.path !== path) return;
      board.history.push(text);
      board.text = error.text;
      board.data = parseCanvas(error.text) || { nodes: [], edges: [] };
      draw();
      setSaveState("Saved");
      toast("This canvas changed somewhere else, so you're seeing the newer version. Undo brings yours back.", {
        action: { label: "Use mine", run: () => mutate(() => { board.data = parseCanvas(text); }) },
      });
      return;
    }
    board.dirty = true;
    setSaveState("Not saved");
    toast(`${error.message || "Could not save the canvas."} Your changes are kept; Cairn tries again on the next change.`, { error: true });
  }
}

// ============================================================ creating things

function viewCenter() {
  const wrap = $("#cv-wrap");
  const rect = wrap ? wrap.getBoundingClientRect() : { width: 800, height: 600 };
  return toWorld(rect.width / 2, rect.height / 2);
}

function toWorld(screenX, screenY) {
  return [(screenX - board.view.x) / board.view.zoom, (screenY - board.view.y) / board.view.zoom];
}

function addNode(node, { edit = false } = {}) {
  const [cx, cy] = node.x === undefined ? viewCenter() : [node.x + node.width / 2, node.y + node.height / 2];
  const full = { id: newId(), ...node, x: snap(cx - node.width / 2), y: snap(cy - node.height / 2) };
  mutate(() => {
    if (full.type === "group") board.data.nodes.unshift(full);
    else board.data.nodes.push(full);
    board.selected = new Set([full.id]);
    board.edge = null;
  });
  if (edit) startEditing(full.id);
  return full;
}

async function addLinkCard() {
  const url = await ask({ title: "Add a link", text: "A web address to keep on the board.", placeholder: "https://…", confirm: "Add" });
  if (!url) return;
  addNode({ type: "link", url: /^[a-z]+:\/\//i.test(url) ? url : `https://${url}`, width: 400, height: 120 });
}

export async function newCanvas() {
  if (readOnly()) return toast("This brain is read-only. Ask to join to contribute.");
  const name = await ask({ title: "New canvas", text: "Give the board a name. It's saved as brain/canvases/<name>.canvas.", placeholder: "e.g. Launch plan", confirm: "Create" });
  const clean = (name || "").replace(/[\\/:*?"<>|#^[\]]/g, " ").replace(/\s+/g, " ").trim();
  if (!clean) return;
  let path = `${FOLDER}/${clean}.canvas`;
  for (let n = 2; state.files.some((f) => f.path.toLowerCase() === path.toLowerCase()); n++) path = `${FOLDER}/${clean} ${n}.canvas`;
  const text = serializeCanvas({ nodes: [], edges: [] });
  const { mtime } = await state.store.write(path, text, null);
  upsertFile(path, text, mtime);
  scheduleGitRefresh();
  openCanvas(path);
  toast(`Created brain/${path}. Double-click the board to add a card.`);
}

export async function openCanvas(path) {
  await flushCanvas();
  state.canvas = path;
  if (state.view !== "canvas") setView("canvas");
  else renderView();
}

async function closeCanvas() {
  await flushCanvas();
  rememberView();
  state.canvas = null;
  board.path = null;
  renderView();
}

// ============================================================ editing a card in place

function startEditing(id) {
  if (readOnly()) return;
  const node = board.data.nodes.find((n) => n.id === id);
  if (!node) return;
  if (node.type === "file") {
    const path = resolveCanvasFile(node.file, board.path);
    if (path && noteByPath(path)) openNote(path);
    else if (path && core.isCanvasPath(path)) openCanvas(path);
    return;
  }
  if (node.type === "link") {
    ask({ title: "Edit link", placeholder: "https://…", value: node.url || "", confirm: "Save" }).then((url) => {
      if (url) mutate(() => { node.url = /^[a-z]+:\/\//i.test(url) ? url : `https://${url}`; });
    });
    return;
  }
  if (node.type === "group") {
    ask({ title: "Name this group", placeholder: "Group name", value: node.label || "", confirm: "Save" }).then((label) => {
      if (label !== null) mutate(() => { node.label = label; });
    });
    return;
  }
  finishEditing();
  const el = $(`[data-node="${CSS.escape(id)}"]`);
  if (!el) return;
  board.editing = id;
  el.classList.add("editing");
  const content = el.querySelector(".cv-content");
  const textarea = document.createElement("textarea");
  textarea.className = "cv-editor";
  textarea.value = node.text || "";
  textarea.setAttribute("aria-label", "Card text (Markdown)");
  textarea.placeholder = "Write in Markdown. [[links]] and #tags work.";
  content.replaceWith(textarea);
  textarea.focus();
  textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  textarea.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Escape" || (event.key === "Enter" && (event.ctrlKey || event.metaKey))) {
      event.preventDefault();
      finishEditing();
      $(`[data-node="${CSS.escape(id)}"]`)?.focus();
    }
  });
  textarea.addEventListener("blur", () => finishEditing());
}

function finishEditing() {
  const id = board.editing;
  if (!id) return;
  board.editing = null;
  const textarea = $(".cv-editor");
  const node = board.data?.nodes.find((n) => n.id === id);
  if (!node || !textarea) return draw();
  const value = textarea.value;
  if (value === (node.text || "")) return draw();
  mutate(() => { node.text = value; });
}

// ============================================================ wiring the board

export function wireCanvas() {
  const page = $(".cv-page");
  if (!page) {
    for (const button of document.querySelectorAll("[data-cv-new]")) button.addEventListener("click", newCanvas);
    for (const card of document.querySelectorAll("[data-cv-open]")) card.addEventListener("click", () => openCanvas(card.dataset.cvOpen));
    return;
  }
  const path = state.canvas;
  const file = fileByPath(path);
  if (board.path !== path || (!board.dirty && file && file.text !== board.text)) loadBoard(path);
  const wrap = $("#cv-wrap");
  if (!board.data) {
    $("#cv-world").innerHTML = `<div class="cv-broken">${emptyState("canvas", "This canvas can't be read", "The file isn't valid JSON Canvas. Open it in a text editor to fix it; Cairn won't overwrite it.")}</div>`;
    return;
  }
  if (!board.view) {
    board.view = { x: 0, y: 0, zoom: 1 };
    requestAnimationFrame(() => fit(false, { firstOpen: true }));
  }
  draw();
  wired?.abort();
  wired = new AbortController();
  const signal = wired.signal;
  page.addEventListener("click", onToolbar, { signal });
  wrap.addEventListener("pointerdown", onPointerDown, { signal });
  wrap.addEventListener("dblclick", onDoubleClick, { signal });
  wrap.addEventListener("wheel", onWheel, { passive: false, signal });
  wrap.addEventListener("keydown", onKey, { signal });
  wrap.addEventListener("pointermove", (event) => {
    const rect = wrap.getBoundingClientRect();
    pointerWorld = toWorld(event.clientX - rect.left, event.clientY - rect.top);
  }, { signal });
  wrap.addEventListener("pointerleave", () => {
    pointerWorld = null;
  }, { signal });
  wrap.addEventListener("dragover", onDragOver, { signal });
  wrap.addEventListener("dragleave", () => wrap.classList.remove("drop-target"), { signal });
  wrap.addEventListener("drop", onDrop, { signal });
  document.addEventListener("copy", onCopy, { signal });
  document.addEventListener("cut", onCopy, { signal });
  document.addEventListener("paste", onPaste, { signal });
  wirePanel(signal);
  window.addEventListener("beforeunload", () => flushCanvas(), { signal });
}

// On a phone, fitting a wide board shrinks every card past reading size, so the first open
// starts at a readable zoom on the board's top-left corner instead. Fit (Shift+1) still fits all.
const READABLE_ZOOM = 0.6;
const NARROW_WRAP = 640;

function fit(animate = true, { firstOpen = false } = {}) {
  const wrap = $("#cv-wrap");
  if (!wrap || !board.data) return;
  const rect = wrap.getBoundingClientRect();
  const box = bounds(board.data.nodes);
  if (!box.width || !rect.width) {
    board.view = { x: rect.width / 2, y: rect.height / 2, zoom: 1 };
  } else {
    const zoom = Math.max(0.1, Math.min(1, (rect.width - 80) / box.width, (rect.height - 80) / box.height));
    if (firstOpen && rect.width < NARROW_WRAP && zoom < READABLE_ZOOM) {
      board.view = { zoom: READABLE_ZOOM, x: 16 - box.x * READABLE_ZOOM, y: 16 - box.y * READABLE_ZOOM };
    } else {
      board.view = { zoom, x: rect.width / 2 - (box.x + box.width / 2) * zoom, y: rect.height / 2 - (box.y + box.height / 2) * zoom };
    }
  }
  const world = $("#cv-world");
  if (world && animate) {
    world.classList.add("animate");
    setTimeout(() => world.classList.remove("animate"), 260);
  }
  applyTransform();
  rememberView();
}

function zoomAt(factor, screenX, screenY) {
  const zoom = Math.max(0.1, Math.min(3, board.view.zoom * factor));
  const [wx, wy] = toWorld(screenX, screenY);
  board.view = { zoom, x: screenX - wx * zoom, y: screenY - wy * zoom };
  applyTransform();
  rememberView();
}

function zoomCenter(factor) {
  const rect = $("#cv-wrap").getBoundingClientRect();
  zoomAt(factor, rect.width / 2, rect.height / 2);
}

function selectedNodes() {
  return board.data.nodes.filter((n) => board.selected.has(n.id));
}

function deleteSelection() {
  if (!board.selected.size && !board.edge) return;
  mutate(() => {
    const gone = new Set(board.selected);
    board.data.nodes = board.data.nodes.filter((n) => !gone.has(n.id));
    board.data.edges = board.data.edges.filter((e) => !gone.has(e.fromNode) && !gone.has(e.toNode) && e.id !== board.edge);
    board.selected = new Set();
    board.edge = null;
  });
}

async function onToolbar(event) {
  const color = event.target.closest("[data-cv-color]");
  if (color) {
    const value = color.dataset.cvColor;
    mutate(() => {
      const targets = board.selected.size ? selectedNodes() : board.data.edges.filter((e) => e.id === board.edge);
      for (const item of targets) {
        if (value) item.color = value;
        else delete item.color;
      }
    });
    $("#cv-wrap")?.focus({ preventScroll: true }); // the bar was redrawn; keep Delete, arrows and undo working
    return;
  }
  const opener = event.target.closest("[data-cv-open]");
  if (opener) return openCanvas(opener.dataset.cvOpen);
  const button = event.target.closest("[data-cv]");
  if (!button) return;
  const action = button.dataset.cv;
  const single = board.selected.size === 1 ? selectedNodes()[0] : null;
  const edge = board.data?.edges.find((e) => e.id === board.edge);
  const actions = {
    back: closeCanvas,
    "add-text": () => addNode({ type: "text", text: "", width: 260, height: 140 }, { edit: true }),
    "add-note": () => togglePanel(),
    "add-link": addLinkCard,
    "add-group": () => addNode({ type: "group", label: "Group", width: 560, height: 380 }),
    "zoom-in": () => zoomCenter(1.25),
    "zoom-out": () => zoomCenter(0.8),
    "zoom-reset": () => zoomCenter(1 / board.view.zoom),
    fit: () => fit(),
    delete: deleteSelection,
    duplicate: duplicateSelection,
    edit: () => single && startEditing(single.id),
    "open-note": () => single && startEditing(single.id),
    "edge-label": async () => {
      if (!edge) return;
      const label = await ask({ title: "Label this connection", placeholder: "e.g. depends on", value: edge.label || "", confirm: "Save" });
      if (label === null) return;
      mutate(() => {
        if (label) edge.label = label;
        else delete edge.label;
      });
    },
    "edge-flip": () => mutate(() => {
      [edge.fromNode, edge.toNode] = [edge.toNode, edge.fromNode];
      [edge.fromSide, edge.toSide] = [edge.toSide, edge.fromSide];
      for (const key of ["fromSide", "toSide"]) if (edge[key] === undefined) delete edge[key];
    }),
  };
  if (actions[action]) {
    event.preventDefault();
    await actions[action]();
  }
}

// ============================================================ copy, paste, duplicate, drop

// The selection as a self-contained JSON Canvas fragment: selected cards, anything inside a selected
// group, and the arrows between them. It goes to the system clipboard as plain JSON Canvas text, so a
// copy can be pasted into another canvas (here or in Obsidian) or into a text editor.
function selectionFragment() {
  const ids = new Set(board.selected);
  for (const node of selectedNodes()) {
    if (node.type === "group") for (const other of board.data.nodes) if (inside(other, node)) ids.add(other.id);
  }
  const nodes = board.data.nodes.filter((n) => ids.has(n.id));
  if (!nodes.length) return null;
  const edges = board.data.edges.filter((e) => ids.has(e.fromNode) && ids.has(e.toNode));
  return JSON.parse(JSON.stringify({ nodes, edges }));
}

let clip = null;
let pointerWorld = null;

// Paste a fragment with fresh ids so its top-left corner lands at `at` (world coordinates).
function pasteFragment(data, at) {
  if (readOnly() || !data?.nodes?.length) return;
  const box = bounds(data.nodes);
  const dx = snap(at[0] - box.x);
  const dy = snap(at[1] - box.y);
  const ids = new Map(data.nodes.map((n) => [n.id, newId()]));
  const nodes = data.nodes.map((n) => ({ ...n, id: ids.get(n.id), x: n.x + dx, y: n.y + dy }));
  const edges = (data.edges || [])
    .filter((e) => ids.has(e.fromNode) && ids.has(e.toNode))
    .map((e) => ({ ...e, id: newId(), fromNode: ids.get(e.fromNode), toNode: ids.get(e.toNode) }));
  mutate(() => {
    board.data.nodes = [...nodes.filter((n) => n.type === "group"), ...board.data.nodes, ...nodes.filter((n) => n.type !== "group")];
    board.data.edges.push(...edges);
    board.selected = new Set(nodes.map((n) => n.id));
    board.edge = null;
  });
}

function duplicateSelection() {
  const data = selectionFragment();
  if (!data || readOnly()) return;
  const box = bounds(data.nodes);
  pasteFragment(data, [box.x + 40, box.y + 40]);
}

// Where pasted or dropped things go: under the pointer if it's on the board, else the middle of the view.
function landingSpot() {
  return pointerWorld || viewCenter();
}

// Turn pasted or dropped text into cards: a JSON Canvas fragment keeps its layout, a web address
// becomes a link card, a [[link]] or brain path becomes a note card, anything else a text card.
function placeText(text, at) {
  const value = String(text || "").trim();
  if (!value || readOnly()) return false;
  if (value.startsWith("{")) {
    const data = parseCanvas(value);
    if (data?.nodes?.length) {
      const box = bounds(data.nodes);
      pasteFragment(data, [at[0] - box.width / 2, at[1] - box.height / 2]);
      return true;
    }
  }
  if (/^https?:\/\/\S+$/i.test(value)) {
    addNode({ type: "link", url: value, width: 400, height: 120, x: at[0] - 200, y: at[1] - 60 });
    return true;
  }
  const wiki = value.match(/^!?\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]$/);
  const target = wiki ? wiki[1].trim() : value;
  const note = noteByPath(target) || (/^[^\n]{1,200}$/.test(target) ? resolveFrom(board.path)(target.replace(/\.md$/i, "")) : null);
  if (note && (wiki || noteByPath(target))) {
    placeNote(note.path, at);
    return true;
  }
  const lines = value.split("\n").length;
  addNode({ type: "text", text: value, width: 300, height: Math.min(400, 60 + lines * 22), x: at[0] - 150, y: at[1] - 40 });
  return true;
}

const typingIn = (target) => Boolean(target?.closest?.(".cv-editor, input, textarea, [contenteditable]"));

function boardHasFocus() {
  const wrap = $("#cv-wrap");
  return Boolean(wrap && (wrap === document.activeElement || wrap.contains(document.activeElement)));
}

function onCopy(event) {
  if (!boardHasFocus() || typingIn(event.target) || board.editing) return;
  const selection = window.getSelection();
  if (selection && !selection.isCollapsed && selection.toString().trim()) return; // copying text from a card
  const data = selectionFragment();
  if (!data) return;
  clip = data;
  event.clipboardData?.setData("text/plain", serializeCanvas(data));
  event.preventDefault();
  if (event.type === "cut" && !readOnly()) deleteSelection();
  else toast(`Copied ${plural(data.nodes.length, "card")}.`);
}

function onPaste(event) {
  if (!boardHasFocus() || typingIn(event.target) || board.editing || readOnly()) return;
  const text = event.clipboardData?.getData("text/plain") || "";
  event.preventDefault();
  if (text.trim()) placeText(text, landingSpot());
  else if (clip) pasteFragment(clip, landingSpot());
}

const DROP_TYPES = ["application/x-cairn-note", "text/uri-list", "text/plain"];

function onDragOver(event) {
  if (readOnly() || !DROP_TYPES.some((t) => event.dataTransfer?.types?.includes(t))) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
  $("#cv-wrap")?.classList.add("drop-target");
}

function onDrop(event) {
  $("#cv-wrap")?.classList.remove("drop-target");
  if (readOnly() || !event.dataTransfer) return;
  event.preventDefault();
  const rect = $("#cv-wrap").getBoundingClientRect();
  const at = toWorld(event.clientX - rect.left, event.clientY - rect.top);
  const notePath = event.dataTransfer.getData("application/x-cairn-note");
  if (notePath && placeNote(notePath, at)) return;
  const uri = (event.dataTransfer.getData("text/uri-list") || "").split("\n").find((line) => line && !line.startsWith("#"));
  placeText(uri || event.dataTransfer.getData("text/plain"), at);
}

function wirePanel(signal) {
  const panel = $("#cv-panel");
  if (!panel) return;
  const filter = $("#cv-panel-filter");
  filter.addEventListener("input", () => {
    $("#cv-panel-list").innerHTML = panelItems(filter.value);
  }, { signal });
  filter.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      togglePanel(false);
    } else if (event.key === "Enter") {
      const first = $("#cv-panel-list [data-cv-place]");
      if (first) placeNote(first.dataset.cvPlace);
    }
  }, { signal });
  panel.addEventListener("click", (event) => {
    const item = event.target.closest("[data-cv-place]");
    if (item) placeNote(item.dataset.cvPlace);
  }, { signal });
  panel.addEventListener("dragstart", (event) => {
    const item = event.target.closest("[data-cv-place]");
    if (!item) return;
    const note = noteByPath(item.dataset.cvPlace);
    event.dataTransfer.setData("application/x-cairn-note", item.dataset.cvPlace);
    event.dataTransfer.setData("text/plain", `[[${note ? note.slug : item.dataset.cvPlace}]]`);
    event.dataTransfer.effectAllowed = "copy";
  }, { signal });
}

function onDoubleClick(event) {
  if (event.target.closest(".cv-editor, .cv-panel, a, button")) return;
  const nodeEl = event.target.closest("[data-node]");
  const onGroupBody = nodeEl?.classList.contains("cv-group") && !event.target.closest("[data-cv-label]");
  if (nodeEl && !onGroupBody) {
    startEditing(nodeEl.dataset.node);
    return;
  }
  const edgeEl = event.target.closest("[data-edge]");
  if (edgeEl) {
    board.edge = edgeEl.dataset.edge;
    $('[data-cv="edge-label"]')?.click();
    return;
  }
  if (readOnly() || !event.target.closest("#cv-wrap")) return;
  const rect = $("#cv-wrap").getBoundingClientRect();
  const [x, y] = toWorld(event.clientX - rect.left, event.clientY - rect.top);
  addNode({ type: "text", text: "", x: x - 130, y: y - 70, width: 260, height: 140 }, { edit: true });
}

function onWheel(event) {
  if (event.target.closest(".cv-editor, .cv-panel")) return;
  const scrollable = event.target.closest(".cv-content");
  if (scrollable && !event.ctrlKey && !event.metaKey && scrollable.scrollHeight > scrollable.clientHeight + 1) return;
  event.preventDefault();
  const rect = $("#cv-wrap").getBoundingClientRect();
  if (event.ctrlKey || event.metaKey) {
    zoomAt(Math.exp(-event.deltaY * 0.0022), event.clientX - rect.left, event.clientY - rect.top);
  } else {
    board.view.x -= event.deltaX;
    board.view.y -= event.deltaY;
    applyTransform();
    rememberView();
  }
}

const pointers = new Map();

function onPointerDown(event) {
  if (event.button === 2) return;
  if (event.target.closest(".cv-editor, .cv-select-bar, .cv-panel")) return;
  if (board.editing) finishEditing();
  const wrap = $("#cv-wrap");
  const rect = wrap.getBoundingClientRect();
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (pointers.size === 2) return startPinch(wrap, rect);
  const handle = event.target.closest(".cv-handle");
  const resize = event.target.closest(".cv-resize");
  const nodeEl = event.target.closest("[data-node]");
  const edgeEl = event.target.closest("[data-edge]");
  const start = { x: event.clientX, y: event.clientY };
  const worldAt = (e) => toWorld(e.clientX - rect.left, e.clientY - rect.top);
  let move = null;
  let up = null;

  if (handle && nodeEl && !readOnly()) {
    event.preventDefault();
    const from = board.data.nodes.find((n) => n.id === nodeEl.dataset.node);
    const fromSide = handle.dataset.side;
    const origin = sidePoint(from, fromSide);
    move = (e) => {
      const [x, y] = worldAt(e);
      const target = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-node]");
      const to = target && target.dataset.node !== from.id ? board.data.nodes.find((n) => n.id === target.dataset.node) : null;
      const toSide = to ? nearestSide(to, x, y) : null;
      const end = to ? sidePoint(to, toSide) : [x, y];
      const draft = $("#cv-draft");
      if (draft) draft.setAttribute("d", curve(origin, fromSide, end, toSide).d);
    };
    up = (e) => {
      const [x, y] = worldAt(e);
      const target = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-node]");
      $("#cv-draft")?.setAttribute("d", "");
      if (Math.hypot(e.clientX - start.x, e.clientY - start.y) < 6) return;
      if (target && target.dataset.node !== from.id) {
        const to = board.data.nodes.find((n) => n.id === target.dataset.node);
        mutate(() => {
          board.data.edges.push({ id: newId(), fromNode: from.id, fromSide, toNode: to.id, toSide: nearestSide(to, x, y) });
        });
      } else if (!target) {
        // Dropped on empty board: make a card there, connected, ready to type (like Obsidian).
        const card = { id: newId(), type: "text", text: "", width: 260, height: 140 };
        const toSide = { top: "bottom", bottom: "top", left: "right", right: "left" }[fromSide];
        card.x = snap(toSide === "left" ? x : toSide === "right" ? x - card.width : x - card.width / 2);
        card.y = snap(toSide === "top" ? y : toSide === "bottom" ? y - card.height : y - card.height / 2);
        mutate(() => {
          board.data.nodes.push(card);
          board.data.edges.push({ id: newId(), fromNode: from.id, fromSide, toNode: card.id, toSide });
          board.selected = new Set([card.id]);
        });
        startEditing(card.id);
      }
    };
  } else if (resize && nodeEl && !readOnly()) {
    event.preventDefault();
    const node = board.data.nodes.find((n) => n.id === nodeEl.dataset.node);
    const before = snapshot();
    const origin = { width: node.width, height: node.height };
    move = (e) => {
      node.width = Math.max(MIN_W, snap(origin.width + (e.clientX - start.x) / board.view.zoom));
      node.height = Math.max(MIN_H, snap(origin.height + (e.clientY - start.y) / board.view.zoom));
      moveNodeElement(node);
      redrawEdges();
    };
    up = () => finishGesture(before);
  } else if (nodeEl) {
    const id = nodeEl.dataset.node;
    const interactive = event.target.closest("a, button, input, textarea");
    if (event.shiftKey) {
      if (board.selected.has(id)) board.selected.delete(id);
      else board.selected.add(id);
    } else if (!board.selected.has(id)) {
      board.selected = new Set([id]);
    }
    board.edge = null;
    for (const el of document.querySelectorAll("#cv-world [data-node]")) el.classList.toggle("selected", board.selected.has(el.dataset.node));
    redrawEdges();
    drawSelectBar();
    if (interactive || readOnly()) return;
    const before = snapshot();
    const moving = new Set(board.selected);
    for (const node of selectedNodes()) {
      if (node.type === "group") for (const other of board.data.nodes) if (inside(other, node)) moving.add(other.id);
    }
    const origins = board.data.nodes.filter((n) => moving.has(n.id)).map((n) => ({ node: n, x: n.x, y: n.y }));
    let dragging = false;
    move = (e) => {
      const dx = (e.clientX - start.x) / board.view.zoom;
      const dy = (e.clientY - start.y) / board.view.zoom;
      if (!dragging && Math.hypot(e.clientX - start.x, e.clientY - start.y) < 4) return;
      dragging = true;
      wrap.classList.add("dragging");
      for (const o of origins) {
        o.node.x = e.altKey ? Math.round(o.x + dx) : snap(o.x + dx);
        o.node.y = e.altKey ? Math.round(o.y + dy) : snap(o.y + dy);
        moveNodeElement(o.node);
      }
      redrawEdges();
    };
    up = () => {
      wrap.classList.remove("dragging");
      if (dragging) finishGesture(before);
    };
  } else if (edgeEl) {
    board.edge = edgeEl.dataset.edge;
    board.selected = new Set();
    for (const el of document.querySelectorAll("#cv-world [data-node].selected")) el.classList.remove("selected");
    redrawEdges();
    drawSelectBar();
    return;
  } else if (event.shiftKey && event.pointerType !== "touch") {
    // Shift + drag on the empty board: draw a box; every card it touches joins the selection
    // (a group only when the box holds all of it, so sweeping across a group doesn't grab it).
    event.preventDefault();
    const base = new Set(board.selected);
    const box = document.createElement("div");
    box.className = "cv-marquee";
    wrap.appendChild(box);
    const sx = start.x - rect.left;
    const sy = start.y - rect.top;
    move = (e) => {
      const ex = e.clientX - rect.left;
      const ey = e.clientY - rect.top;
      const left = Math.min(sx, ex);
      const top = Math.min(sy, ey);
      const width = Math.abs(ex - sx);
      const height = Math.abs(ey - sy);
      Object.assign(box.style, { left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px` });
      const [x1, y1] = toWorld(left, top);
      const [x2, y2] = toWorld(left + width, top + height);
      const hits = board.data.nodes.filter((n) => {
        if (n.type === "group") return n.x >= x1 && n.y >= y1 && n.x + n.width <= x2 && n.y + n.height <= y2;
        return n.x < x2 && n.x + n.width > x1 && n.y < y2 && n.y + n.height > y1;
      });
      board.selected = new Set([...base, ...hits.map((n) => n.id)]);
      board.edge = null;
      for (const el of document.querySelectorAll("#cv-world [data-node]")) el.classList.toggle("selected", board.selected.has(el.dataset.node));
    };
    up = () => {
      box.remove();
      redrawEdges();
      drawSelectBar();
    };
  } else {
    // The empty board: clear the selection and pan.
    if (!event.shiftKey && (board.selected.size || board.edge)) {
      board.selected = new Set();
      board.edge = null;
      for (const el of document.querySelectorAll("#cv-world [data-node].selected")) el.classList.remove("selected");
      redrawEdges();
      drawSelectBar();
    }
    const origin = { ...board.view };
    wrap.classList.add("panning");
    move = (e) => {
      board.view.x = origin.x + (e.clientX - start.x);
      board.view.y = origin.y + (e.clientY - start.y);
      applyTransform();
    };
    up = () => {
      wrap.classList.remove("panning");
      rememberView();
    };
  }

  wrap.focus({ preventScroll: true });
  const onMove = (e) => {
    if (e.pointerId !== event.pointerId) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size < 2) move?.(e);
  };
  const onUp = (e) => {
    if (e.pointerId !== event.pointerId) return;
    pointers.delete(e.pointerId);
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    window.removeEventListener("pointercancel", onUp);
    up?.(e);
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  window.addEventListener("pointercancel", onUp);
}

function startPinch(wrap, rect) {
  const [a, b] = [...pointers.values()];
  const startDistance = Math.hypot(a.x - b.x, a.y - b.y) || 1;
  const startZoom = board.view.zoom;
  const center = [(a.x + b.x) / 2 - rect.left, (a.y + b.y) / 2 - rect.top];
  const [wx, wy] = toWorld(center[0], center[1]);
  const onMove = (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const [p, q] = [...pointers.values()];
    if (!p || !q) return;
    const zoom = Math.max(0.1, Math.min(3, startZoom * (Math.hypot(p.x - q.x, p.y - q.y) / startDistance)));
    const mid = [(p.x + q.x) / 2 - rect.left, (p.y + q.y) / 2 - rect.top];
    board.view = { zoom, x: mid[0] - wx * zoom, y: mid[1] - wy * zoom };
    applyTransform();
  };
  const onUp = (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      rememberView();
    }
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  window.addEventListener("pointercancel", onUp);
}

function onKey(event) {
  if (event.target.closest(".cv-editor, .cv-panel") || board.editing) return;
  const mod = event.ctrlKey || event.metaKey;
  const key = event.key;
  if (mod && key.toLowerCase() === "z") {
    event.preventDefault();
    return undo(event.shiftKey);
  }
  if (mod && key.toLowerCase() === "y") {
    event.preventDefault();
    return undo(true);
  }
  if (mod && key.toLowerCase() === "d") {
    event.preventDefault();
    return duplicateSelection();
  }
  if (mod && key.toLowerCase() === "a") {
    event.preventDefault();
    board.selected = new Set(board.data.nodes.map((n) => n.id));
    return draw();
  }
  if ((key === "Delete" || key === "Backspace") && !readOnly()) {
    event.preventDefault();
    return deleteSelection();
  }
  if (key === "Escape") {
    if (!board.selected.size && !board.edge) return;
    event.stopPropagation();
    board.selected = new Set();
    board.edge = null;
    return draw();
  }
  if (key === "+" || key === "=") return zoomCenter(1.2);
  if (key === "-" || key === "_") return zoomCenter(1 / 1.2);
  if (key === "!" || (event.shiftKey && key === "1")) return fit();
  if (key === "Enter") {
    const focused = event.target.closest("[data-node]");
    if (focused) {
      event.preventDefault();
      board.selected = new Set([focused.dataset.node]);
      return startEditing(focused.dataset.node);
    }
  }
  const arrows = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] };
  if (arrows[key]) {
    event.preventDefault();
    const step = event.shiftKey ? 50 : GRID;
    const [dx, dy] = arrows[key];
    const focused = event.target.closest("[data-node]");
    if (focused && !board.selected.has(focused.dataset.node)) board.selected = new Set([focused.dataset.node]);
    if (board.selected.size && !readOnly()) {
      const focusId = focused?.dataset.node;
      mutate(() => {
        for (const node of selectedNodes()) {
          node.x += dx * step;
          node.y += dy * step;
        }
      });
      if (focusId) $(`[data-node="${CSS.escape(focusId)}"]`)?.focus();
    } else {
      board.view.x -= dx * step * 4;
      board.view.y -= dy * step * 4;
      applyTransform();
      rememberView();
    }
  }
}

// Palette helpers.
export async function chooseCanvas() {
  const files = canvasFiles();
  if (!files.length) return toast("No canvases yet. Use “New canvas” to make one.");
  const path = await pick(files.map((f) => ({ label: canvasName(f.path), kind: f.path, icon: "canvas", value: f.path })), "Open a canvas…");
  if (path) openCanvas(path);
}

export function canvasCount() {
  return canvasFiles().length;
}

