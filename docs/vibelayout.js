// Pages as blocks you arrange: fold any block to one line, move it up or down (arrows or drag), and get the
// same layout on every device. Every page with content of its own is built this way (see PAGES); each one
// has a Dashboard block (its numbers) and a Recap block (what changed lately). The
// layout lives in brain/CAIRN-LAYOUT.md, so the desktop app, the web app and the phone read the same file; a
// copy in localStorage covers a read-only brain.

import { escapeHtml as esc } from "./md.js";
import { $, $$, icon, relativeTime } from "./ui.js";
import { fileByPath, readOnly, state, upsertFile } from "./state.js";
import { renderView } from "./app.js";

export const LAYOUT_FILE = "CAIRN-LAYOUT.md";
const SAVE_DELAY_MS = 1200;

// Default order per page. `half` blocks sit two to a row on a wide screen when two of them follow each other.
const PAGES = {
  vibe: {
    label: "Vibe",
    panels: [
      { id: "recap", label: "Live recap" },
      { id: "machine", label: "This computer", half: true },
      { id: "github", label: "GitHub minutes", half: true },
      { id: "overview", label: "Overview" },
      { id: "figures", label: "Numbers" },
      { id: "activity", label: "Activity" },
      { id: "sessions", label: "Sessions", half: true },
      { id: "feedback", label: "Feedback", half: true },
      { id: "skills", label: "Skills", half: true },
      { id: "models", label: "Models", half: true },
      { id: "hook", label: "Agent instructions" },
    ],
    folded: ["hook"],
  },
  projects: {
    label: "Projects",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "ranking", label: "Ranking" },
      { id: "scoring", label: "How the score works" },
    ],
    folded: ["scoring"],
  },
  people: {
    label: "People",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "profile", label: "Looked up" },
      { id: "mine", label: "Your brains" },
      { id: "leaderboard", label: "Friends and leaderboard" },
      { id: "discover", label: "Recently published by others" },
      { id: "publish", label: "Publish your brain" },
    ],
    folded: ["publish"],
  },
  proof: {
    label: "Proof",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "claims", label: "Claims" },
      { id: "publish", label: "Publish" },
    ],
    folded: [],
  },
  home: {
    label: "Today",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "log", label: "Today's log" },
      { id: "focus", label: "In focus" },
    ],
    folded: [],
  },
  board: {
    label: "Board",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "cards", label: "Tasks" },
    ],
    folded: [],
  },
  ideas: {
    label: "Ideas",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "cards", label: "Ideas" },
    ],
    folded: [],
  },
  decisions: {
    label: "Decisions",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "log", label: "Decision log" },
    ],
    folded: [],
  },
  journal: {
    label: "Journal",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap, day by day" },
      { id: "calendar", label: "Every day and mood", half: true },
      { id: "mind", label: "On your mind", half: true },
      { id: "search", label: "Search every day" },
      { id: "pages", label: "All journal pages" },
    ],
    folded: ["pages"],
  },
  // Chat and Map are tools you work in, so the tool comes first and its numbers sit under it.
  chat: {
    label: "Chat",
    panels: [
      { id: "talk", label: "Conversation" },
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
    ],
    folded: [],
  },
  autopilot: {
    label: "Autopilot",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "goal", label: "Hand over a goal" },
      { id: "goals", label: "Goals" },
      { id: "log", label: "Log" },
      { id: "queue", label: "Queue", half: true },
      { id: "settings", label: "Settings", half: true },
    ],
    folded: ["settings"],
  },
  map: {
    label: "Map",
    panels: [
      { id: "graph", label: "Map" },
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
    ],
    folded: [],
  },
  canvas: {
    label: "Canvas",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "boards", label: "Canvases" },
    ],
    folded: [],
  },
  notes: {
    label: "Notes",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "all", label: "All notes" },
    ],
    folded: [],
  },
  code: {
    label: "Code inbox",
    panels: [
      { id: "dashboard", label: "Dashboard" },
      { id: "recap", label: "Recap" },
      { id: "inbox", label: "Inbox" },
    ],
    folded: [],
  },
};
export const PANELS = PAGES.vibe.panels;

const panelsOf = (page) => PAGES[page]?.panels || [];
const localKey = (page) => (page === "vibe" ? "cairn.vibeLayout" : `cairn.layout.${page}`);

// "vibe-order: a, b, c" and "vibe-folded: x, y" lines (one pair per page), anywhere in the file.
export function parseLayout(text, page = "vibe") {
  const known = new Set(panelsOf(page).map((panel) => panel.id));
  const list = (key) => {
    const match = String(text || "").match(new RegExp(`^${page}-${key}:[ \\t]*(.*)$`, "m"));
    return match ? match[1].split(",").map((id) => id.trim()).filter((id) => known.has(id)) : null;
  };
  const order = list("order");
  const folded = list("folded");
  return order || folded ? { order: order || [], folded: folded || [] } : null;
}

// Blocks the saved order does not know (added in a later version) keep their default place.
function complete(order, page) {
  const panels = panelsOf(page);
  const known = new Set(panels.map((panel) => panel.id));
  const out = [...new Set(order.filter((id) => known.has(id)))];
  panels.forEach((panel, index) => {
    if (out.includes(panel.id)) return;
    const before = panels.slice(0, index).map((p) => p.id).filter((id) => out.includes(id)).at(-1);
    out.splice(before ? out.indexOf(before) + 1 : 0, 0, panel.id);
  });
  return out;
}

function localLayout(page) {
  try {
    return JSON.parse(localStorage.getItem(localKey(page))) || null;
  } catch {
    return null;
  }
}

// The layouts just chosen on this device, by page, until the brain file has them.
const pending = {};

export function pageLayout(page = "vibe") {
  const fromFile = parseLayout(fileByPath(LAYOUT_FILE)?.text, page);
  // A brain you can only read still lets you arrange the page for yourself on this device.
  const saved = pending[page] || (canWriteLayout() ? fromFile || localLayout(page) : localLayout(page) || fromFile);
  return {
    order: complete(saved?.order || [], page),
    folded: new Set(saved ? saved.folded : PAGES[page]?.folded || []),
  };
}
export const vibeLayout = () => pageLayout("vibe");

// The whole file: one pair of lines for every page arranged so far. `layouts` maps a page to { order, folded }.
// Lines of a page this version does not know (written by a newer Cairn) are carried over untouched.
export function layoutText(layouts, previous = "") {
  const single = layouts && Array.isArray(layouts.order) ? { vibe: layouts } : layouts;
  const pairs = Object.keys(PAGES)
    .filter((page) => single[page])
    .map((page) => `${page}-order: ${single[page].order.join(", ")}\n${page}-folded: ${[...single[page].folded].join(", ")}`);
  const foreign = String(previous || "")
    .split(/\r?\n/)
    .filter((line) => {
      const match = /^([a-z][a-z0-9]*)-(?:order|folded):/.exec(line);
      return match && !PAGES[match[1]];
    });
  return `---
type: settings
---

# Cairn layout

How the pages built from blocks are arranged, shared by the desktop app, the web app
and the phone. Change it in Cairn: the arrow on a block folds it, the up and down buttons (or dragging the
handle) move it. Editing these lines by hand works too. Block names:

${Object.entries(PAGES).map(([page, { panels }]) => `- ${page}: ${panels.map((panel) => panel.id).join(", ")}`).join("\n")}

${[...pairs, ...foreign].join("\n")}
`;
}

let saveTimer = null;

function canWriteLayout() {
  return !readOnly() && typeof state.store?.write === "function" && state.store.writable !== false;
}

async function writeLayout(text) {
  const existing = fileByPath(LAYOUT_FILE);
  let saved;
  try {
    saved = await state.store.write(LAYOUT_FILE, text, existing ? existing.mtime : null);
  } catch (error) {
    // Another device saved in between: the newest choice wins.
    if (typeof error?.mtime !== "number") throw error;
    saved = await state.store.write(LAYOUT_FILE, text, error.mtime);
  }
  upsertFile(LAYOUT_FILE, text, saved?.mtime ?? Date.now());
}

// What the file should say now: every page it already holds, with this device's newest choices on top.
function fileText() {
  const previous = fileByPath(LAYOUT_FILE)?.text || "";
  const layouts = {};
  for (const page of Object.keys(PAGES)) {
    const saved = pending[page] || parseLayout(previous, page);
    if (saved) layouts[page] = saved;
  }
  return layoutText(layouts, previous);
}

function saveLayout(layout, page = "vibe") {
  const chosen = { order: layout.order, folded: [...layout.folded] };
  pending[page] = chosen;
  try {
    localStorage.setItem(localKey(page), JSON.stringify(chosen));
  } catch {
    /* storage is off: the brain file still carries it */
  }
  if (!canWriteLayout()) return;
  // Written to the brain once the clicking stops, so a few quick moves make one change.
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const written = { ...pending };
    writeLayout(fileText())
      .then(() => {
        for (const name of Object.keys(written)) if (pending[name] === written[name]) delete pending[name];
      })
      .catch(() => {
        /* offline: this device keeps the layout and the next change tries again */
      });
  }, SAVE_DELAY_MS);
}

// A block opened by a jump (a lane to its row, a chart bar to its day) stays open until you fold it again.
const peeked = () => state.vibePeek || (state.vibePeek = new Set());
const peekKey = (id, page) => (page === "vibe" ? id : `${page}:${id}`);

export function isFolded(id, layout = pageLayout("vibe"), page = "vibe") {
  return layout.folded.has(id) && !peeked().has(peekKey(id, page));
}

export function openPanel(id, page = "vibe") {
  if (!isFolded(id, pageLayout(page), page)) return false;
  peeked().add(peekKey(id, page));
  renderView();
  return true;
}

const boardOf = (page) => `.vibe-board[data-page="${CSS.escape(page)}"]`;

export function togglePanel(id, page = "vibe") {
  const layout = pageLayout(page);
  const folded = isFolded(id, layout, page);
  peeked().delete(peekKey(id, page));
  if (folded) layout.folded.delete(id);
  else layout.folded.add(id);
  saveLayout(layout, page);
  renderView();
  $(`${boardOf(page)} .vp-toggle[data-panel="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
}

// Move a block one place up or down among the blocks shown on this page.
export function movePanel(id, step, page = "vibe") {
  const layout = pageLayout(page);
  const shown = $$(`${boardOf(page)} .vibe-panel`).map((el) => el.dataset.panel);
  const at = shown.indexOf(id);
  const other = shown[at + Number(step)];
  if (at < 0 || !other) return;
  placePanel(layout, id, other, Number(step) > 0 ? "after" : "before");
  saveLayout(layout, page);
  renderView();
  $(`${boardOf(page)} .vp-move[data-panel="${CSS.escape(id)}"][data-step="${step}"]`)?.focus({ preventScroll: true });
}

function placePanel(layout, id, target, where) {
  const order = layout.order.filter((other) => other !== id);
  const index = order.indexOf(target);
  order.splice(where === "after" ? index + 1 : index, 0, id);
  layout.order = order;
}

export function resetLayout(page = "vibe") {
  for (const key of [...peeked()]) if (page === "vibe" ? !key.includes(":") : key.startsWith(`${page}:`)) peeked().delete(key);
  saveLayout({ order: panelsOf(page).map((panel) => panel.id), folded: new Set(PAGES[page]?.folded || []) }, page);
  renderView();
}

// `blocks` maps a block id to { html, summary }; empty blocks are left out. Two half blocks in a row pair up.
// A block may give its own `label` for the cases where the usual title would be wrong.
export function panelsHtml(blocks, page = "vibe") {
  const layout = pageLayout(page);
  const shown = layout.order.filter((id) => blocks[id]?.html);
  const meta = new Map(panelsOf(page).map((panel) => [panel.id, panel]));
  let pairedWithPrevious = false;
  return shown
    .map((id, index) => {
      const { half } = meta.get(id);
      const label = blocks[id].label || meta.get(id).label;
      const nextHalf = meta.get(shown[index + 1])?.half;
      const pair = half && (pairedWithPrevious || nextHalf);
      pairedWithPrevious = Boolean(half && !pairedWithPrevious && nextHalf);
      const folded = isFolded(id, layout, page);
      const summary = blocks[id].summary || "";
      const where = `data-panel="${id}" data-page="${page}"`;
      return `<section class="vibe-panel${pair ? " half" : ""}${folded ? " folded" : ""}" ${where} aria-label="${esc(label)}">
        <header class="vp-head">
          <button type="button" class="vp-toggle" data-action="vibe-panel-fold" ${where} aria-expanded="${!folded}">
            ${icon("chevron", "sm")}<span class="vp-title">${esc(label)}</span>${summary ? `<span class="vp-sum">${esc(summary)}</span>` : ""}
          </button>
          <span class="vp-tools">
            <button type="button" class="vp-move" data-action="vibe-panel-move" ${where} data-step="-1" ${index ? "" : "disabled"} aria-label="Move ${esc(label)} up" title="Move up">&uarr;</button>
            <button type="button" class="vp-move" data-action="vibe-panel-move" ${where} data-step="1" ${index < shown.length - 1 ? "" : "disabled"} aria-label="Move ${esc(label)} down" title="Move down">&darr;</button>
            <span class="vp-grip" draggable="true" data-panel="${id}" title="Drag to move" aria-hidden="true">&#8942;&#8942;</span>
          </span>
        </header>
        ${folded ? "" : `<div class="vp-body">${blocks[id].html}</div>`}
      </section>`;
    })
    .join("");
}

// A page's blocks, plus the line under them that says how to arrange them and puts the default order back.
export function boardHtml(page, blocks) {
  const note = readOnly()
    ? ""
    : `<p class="vibe-layout-note muted small">Fold a block with its arrow, move it with ↑ ↓ or drag ⋮⋮. ${
        fileByPath(LAYOUT_FILE) ? `The layout is kept in <code>${LAYOUT_FILE}</code>, so every device shows the same.` : "Your layout is saved to the brain, so every device shows the same."
      } <button type="button" class="btn sm ghost" data-action="vibe-layout-reset" data-page="${page}">Reset layout</button></p>`;
  return `<div class="vibe-board" data-page="${page}">${panelsHtml(blocks, page)}</div>${note}`;
}

// The numbers at the top of a page: `tiles` is a list of { value, label, sub, title, go }. A tile with `go`
// is a button that opens that page.
export function dashboardHtml(tiles) {
  return `<div class="figures page-figures">${tiles
    .map((tile) => {
      const tag = tile.go ? "button" : "div";
      const inner = `<span class="value">${esc(String(tile.value))}</span><span class="label">${esc(tile.label)}</span>${tile.sub ? `<span class="sub">${esc(tile.sub)}</span>` : ""}`;
      return `<${tag} class="figure"${tile.go ? ` type="button" data-go="${esc(tile.go)}"` : ""}${tile.title ? ` title="${esc(tile.title)}"` : ""}>${inner}</${tag}>`;
    })
    .join("")}</div>`;
}

// What changed lately on a page: `rows` is a list of { when, text (html), path } and `empty` the line shown without any.
export function recapHtml(rows, empty) {
  if (!rows.length) return `<p class="muted small page-recap-empty">${esc(empty)}</p>`;
  return `<ol class="page-recap">${rows
    .map((row) => `<li>${row.when ? `<span class="t">${esc(row.when)}</span>` : ""}<span class="txt">${row.html}</span></li>`)
    .join("")}</ol>`;
}

// Recap rows for notes, newest change first: `describe(note)` is the text after the note's name.
export function noteRecap(notes, describe, empty, limit = 6) {
  const rows = [...notes]
    .sort((a, b) => (b.mtime || 0) - (a.mtime || 0))
    .slice(0, limit)
    .map((note) => {
      const detail = describe ? describe(note) : "";
      return { when: relativeTime(note.mtime), html: `<button class="link-button" data-open="${esc(note.path)}">${esc(note.title)}</button>${detail ? ` <span class="muted">· ${esc(detail)}</span>` : ""}` };
    });
  return recapHtml(rows, empty);
}

// Drag a block by its handle and drop it on another: above that block's middle puts it before, below after.
export function wirePanels() {
  for (const board of $$(".vibe-board")) wireBoard(board, board.dataset.page || "vibe");
}

function wireBoard(board, page) {
  let dragged = null;
  const clear = () => $$(".vibe-panel.drop-before, .vibe-panel.drop-after", board).forEach((el) => el.classList.remove("drop-before", "drop-after"));
  const sideOf = (panel, event) => {
    const box = panel.getBoundingClientRect();
    return event.clientY < box.top + box.height / 2 ? "before" : "after";
  };
  board.addEventListener("dragstart", (event) => {
    const grip = event.target.closest?.(".vp-grip");
    if (!grip) return;
    dragged = grip.dataset.panel;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", dragged);
    grip.closest(".vibe-panel").classList.add("dragging");
  });
  board.addEventListener("dragover", (event) => {
    const panel = event.target.closest?.(".vibe-panel");
    if (!dragged || !panel || panel.dataset.panel === dragged) return;
    event.preventDefault();
    clear();
    panel.classList.add(`drop-${sideOf(panel, event)}`);
  });
  board.addEventListener("dragleave", (event) => {
    if (!board.contains(event.relatedTarget)) clear();
  });
  board.addEventListener("drop", (event) => {
    const panel = event.target.closest?.(".vibe-panel");
    if (!dragged || !panel || panel.dataset.panel === dragged) return;
    event.preventDefault();
    const layout = pageLayout(page);
    placePanel(layout, dragged, panel.dataset.panel, sideOf(panel, event));
    dragged = null;
    saveLayout(layout, page);
    renderView();
  });
  board.addEventListener("dragend", () => {
    dragged = null;
    clear();
    $$(".vibe-panel.dragging", board).forEach((el) => el.classList.remove("dragging"));
  });
}
