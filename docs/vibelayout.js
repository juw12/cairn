// The Vibe page as blocks you arrange: fold any block to one line, move it up or down (arrows or drag), and
// get the same layout on every device. The layout lives in brain/CAIRN-LAYOUT.md, so the desktop app, the
// web app and the phone read the same file; a copy in localStorage covers a read-only brain.

import { escapeHtml as esc } from "./md.js";
import { $, $$, icon } from "./ui.js";
import { fileByPath, readOnly, state, upsertFile } from "./state.js";
import { renderView } from "./app.js";

export const LAYOUT_FILE = "CAIRN-LAYOUT.md";
const LOCAL_KEY = "cairn.vibeLayout";
const SAVE_DELAY_MS = 1200;

// Default order. `half` blocks sit two to a row on a wide screen when two of them follow each other.
export const PANELS = [
  { id: "recap", label: "Live recap" },
  { id: "machine", label: "This computer", half: true },
  { id: "github", label: "GitHub minutes", half: true },
  { id: "overview", label: "Overview" },
  { id: "live", label: "Live session cards" },
  { id: "figures", label: "Numbers" },
  { id: "activity", label: "Activity" },
  { id: "sessions", label: "Sessions", half: true },
  { id: "feedback", label: "Feedback", half: true },
  { id: "skills", label: "Skills", half: true },
  { id: "models", label: "Models", half: true },
  { id: "hook", label: "Agent instructions" },
];
// Until you fold or move something yourself: the agent hook starts folded, and so do the full live cards once
// more than a few run at once (the recap and the overview already list every one of them).
const MANY_LIVE = 3;
const defaultFolded = () => ["hook", ...(liveCount > MANY_LIVE ? ["live"] : [])];
let liveCount = 0;

export function setLiveCount(count) {
  liveCount = count;
}
const KNOWN = new Set(PANELS.map((panel) => panel.id));

// "vibe-order: a, b, c" and "vibe-folded: x, y" lines, anywhere in the file.
export function parseLayout(text) {
  const list = (key) => {
    const match = String(text || "").match(new RegExp(`^${key}:[ \\t]*(.*)$`, "m"));
    return match ? match[1].split(",").map((id) => id.trim()).filter((id) => KNOWN.has(id)) : null;
  };
  const order = list("vibe-order");
  const folded = list("vibe-folded");
  return order || folded ? { order: order || [], folded: folded || [] } : null;
}

// Blocks the saved order does not know (added in a later version) keep their default place.
function complete(order) {
  const out = [...new Set(order.filter((id) => KNOWN.has(id)))];
  PANELS.forEach((panel, index) => {
    if (out.includes(panel.id)) return;
    const before = PANELS.slice(0, index).map((p) => p.id).filter((id) => out.includes(id)).at(-1);
    out.splice(before ? out.indexOf(before) + 1 : 0, 0, panel.id);
  });
  return out;
}

function localLayout() {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_KEY)) || null;
  } catch {
    return null;
  }
}

// The layout just chosen on this device, until the brain file has it.
let pending = null;

export function vibeLayout() {
  const fromFile = parseLayout(fileByPath(LAYOUT_FILE)?.text);
  // A brain you can only read still lets you arrange the page for yourself on this device.
  const saved = pending || (canWriteLayout() ? fromFile || localLayout() : localLayout() || fromFile);
  return {
    order: complete(saved?.order || []),
    folded: new Set(saved ? saved.folded : defaultFolded()),
  };
}

export function layoutText({ order, folded }) {
  return `---
type: settings
---

# Cairn layout

How the Vibe page is arranged, shared by the desktop app, the web app and the phone. Change it in Cairn:
the arrow on a block folds it, the up and down buttons (or dragging the handle) move it. Editing these
two lines by hand works too; block names: ${PANELS.map((panel) => panel.id).join(", ")}.

vibe-order: ${order.join(", ")}
vibe-folded: ${[...folded].join(", ")}
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

function saveLayout(layout) {
  pending = { order: layout.order, folded: [...layout.folded] };
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(pending));
  } catch {
    /* storage is off: the brain file still carries it */
  }
  if (!canWriteLayout()) return;
  // Written to the brain once the clicking stops, so a few quick moves make one change.
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const text = layoutText(pending);
    writeLayout(text)
      .then(() => {
        if (layoutText(pending) === text) pending = null;
      })
      .catch(() => {
        /* offline: this device keeps the layout and the next change tries again */
      });
  }, SAVE_DELAY_MS);
}

// A block opened by a jump (a lane to its card, a chart bar to its day) stays open until you fold it again.
const peeked = () => state.vibePeek || (state.vibePeek = new Set());

export function isFolded(id, layout = vibeLayout()) {
  return layout.folded.has(id) && !peeked().has(id);
}

export function openPanel(id) {
  if (!isFolded(id)) return false;
  peeked().add(id);
  renderView();
  return true;
}

export function togglePanel(id) {
  const layout = vibeLayout();
  const folded = isFolded(id, layout);
  peeked().delete(id);
  if (folded) layout.folded.delete(id);
  else layout.folded.add(id);
  saveLayout(layout);
  renderView();
  $(`.vp-toggle[data-panel="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
}

// Move a block one place up or down among the blocks shown on this page.
export function movePanel(id, step) {
  const layout = vibeLayout();
  const shown = $$(".vibe-panel").map((el) => el.dataset.panel);
  const at = shown.indexOf(id);
  const other = shown[at + Number(step)];
  if (at < 0 || !other) return;
  placePanel(layout, id, other, Number(step) > 0 ? "after" : "before");
  saveLayout(layout);
  renderView();
  $(`.vp-move[data-panel="${CSS.escape(id)}"][data-step="${step}"]`)?.focus({ preventScroll: true });
}

function placePanel(layout, id, target, where) {
  const order = layout.order.filter((other) => other !== id);
  const index = order.indexOf(target);
  order.splice(where === "after" ? index + 1 : index, 0, id);
  layout.order = order;
}

export function resetLayout() {
  state.vibePeek = new Set();
  saveLayout({ order: PANELS.map((panel) => panel.id), folded: new Set(defaultFolded()) });
  renderView();
}

// `blocks` maps a block id to { html, summary }; empty blocks are left out. Two half blocks in a row pair up.
export function panelsHtml(blocks) {
  const layout = vibeLayout();
  const shown = layout.order.filter((id) => blocks[id]?.html);
  const meta = new Map(PANELS.map((panel) => [panel.id, panel]));
  let pairedWithPrevious = false;
  return shown
    .map((id, index) => {
      const { label, half } = meta.get(id);
      const nextHalf = meta.get(shown[index + 1])?.half;
      const pair = half && (pairedWithPrevious || nextHalf);
      pairedWithPrevious = Boolean(half && !pairedWithPrevious && nextHalf);
      const folded = isFolded(id, layout);
      const summary = blocks[id].summary || "";
      return `<section class="vibe-panel${pair ? " half" : ""}${folded ? " folded" : ""}" data-panel="${id}" aria-label="${esc(label)}">
        <header class="vp-head">
          <button type="button" class="vp-toggle" data-action="vibe-panel-fold" data-panel="${id}" aria-expanded="${!folded}">
            ${icon("chevron", "sm")}<span class="vp-title">${esc(label)}</span>${summary ? `<span class="vp-sum">${esc(summary)}</span>` : ""}
          </button>
          <span class="vp-tools">
            <button type="button" class="vp-move" data-action="vibe-panel-move" data-panel="${id}" data-step="-1" ${index ? "" : "disabled"} aria-label="Move ${esc(label)} up" title="Move up">&uarr;</button>
            <button type="button" class="vp-move" data-action="vibe-panel-move" data-panel="${id}" data-step="1" ${index < shown.length - 1 ? "" : "disabled"} aria-label="Move ${esc(label)} down" title="Move down">&darr;</button>
            <span class="vp-grip" draggable="true" data-panel="${id}" title="Drag to move" aria-hidden="true">&#8942;&#8942;</span>
          </span>
        </header>
        ${folded ? "" : `<div class="vp-body">${blocks[id].html}</div>`}
      </section>`;
    })
    .join("");
}

// Drag a block by its handle and drop it on another: above that block's middle puts it before, below after.
export function wirePanels() {
  const board = $(".vibe-board");
  if (!board) return;
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
    const layout = vibeLayout();
    placePanel(layout, dragged, panel.dataset.panel, sideOf(panel, event));
    dragged = null;
    saveLayout(layout);
    renderView();
  });
  board.addEventListener("dragend", () => {
    dragged = null;
    clear();
    $$(".vibe-panel.dragging", board).forEach((el) => el.classList.remove("dragging"));
  });
}
