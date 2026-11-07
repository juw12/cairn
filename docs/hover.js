// Page preview: hover a [[link]] for a moment to read the note without opening it (Obsidian's
// "Page preview"). Works for links in notes, embeds and the Map's side panel.

import { escapeHtml as esc, sectionOf } from "./md.js";
import { typePill } from "./ui.js";
import { noteByPath, state } from "./state.js";
import { hydrateAssets, renderNote } from "./vault.js";

const DELAY = 380;
let showTimer = null;
let hideTimer = null;
let card = null;
let current = null;

function hide() {
  clearTimeout(showTimer);
  card?.remove();
  card = null;
  current = null;
}

function show(link) {
  const note = noteByPath(link.dataset.open);
  if (!note || !document.body.contains(link)) return;
  const heading = link.dataset.heading || "";
  const section = heading ? sectionOf(note.body, heading) : null;
  const source = (section ?? note.body).split("\n").slice(0, 60).join("\n");
  card?.remove();
  card = document.createElement("div");
  card.className = "hover-card";
  card.setAttribute("role", "tooltip");
  card.innerHTML = `
    <div class="hover-head">${typePill(note.type)}<strong>${esc(note.title)}</strong></div>
    <div class="prose hover-body">${source.trim() ? renderNote(note, { source, depth: 1, tasks: false }) : '<p class="muted">Empty note.</p>'}</div>
    <div class="hover-foot">${esc(note.path)} · click to open</div>`;
  document.body.appendChild(card);
  hydrateAssets(card, note.path);
  const rect = link.getBoundingClientRect();
  const width = Math.min(420, window.innerWidth - 16);
  card.style.width = `${width}px`;
  const height = card.offsetHeight;
  const below = rect.bottom + 8 + height < window.innerHeight;
  card.style.top = `${below ? rect.bottom + 8 : Math.max(8, rect.top - height - 8)}px`;
  card.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
  card.addEventListener("mouseenter", () => clearTimeout(hideTimer));
  card.addEventListener("mouseleave", () => {
    hideTimer = setTimeout(hide, 200);
  });
  current = link;
}

export function initHoverPreview() {
  if (!matchMedia("(hover: hover)").matches) return;
  document.addEventListener("mouseover", (event) => {
    const link = event.target.closest?.(".wikilink[data-open]");
    if (!link || link === current || !state.notes.length) return;
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    showTimer = setTimeout(() => show(link), DELAY);
  });
  document.addEventListener("mouseout", (event) => {
    const link = event.target.closest?.(".wikilink[data-open]");
    if (!link || link.contains(event.relatedTarget)) return;
    clearTimeout(showTimer);
    if (card) hideTimer = setTimeout(() => {
      if (!card?.matches(":hover")) hide();
    }, 220);
  });
  document.addEventListener("click", hide, true);
  document.addEventListener("keydown", hide, true);
  window.addEventListener("scroll", hide, true);
}
