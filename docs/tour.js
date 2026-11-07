// The first-run tour: a spotlight walks through the app's main moves the first time someone opens
// Cairn. Next/Back/Skip, arrow keys and Esc; remembered per browser; run again from the palette.

import { escapeHtml as esc } from "./md.js";
import { $ } from "./ui.js";
import { readOnly, state } from "./state.js";

export const TOUR_KEY = "cairn.tour.v1";

const STEPS = [
  {
    title: "Welcome to Cairn",
    text: "A second brain that lives next to your code: plain Markdown notes you can also open in Obsidian, VS Code or any AI assistant. This one-minute tour shows the main moves.",
  },
  {
    target: "#capture-form",
    title: "Capture anything",
    text: "Type a thought and press Enter. Start with todo:, decision: or log: to file it as a task, a decision or a journal line, and add #tags. Press C anywhere to jump here.",
  },
  {
    target: '#nav [data-go="ideas"]',
    title: "Brainstorm ideas",
    text: "Ideas move from Raw to Ready on a board. Open one and press Spark for a prompt that pushes it further, then Promote it to a task.",
  },
  {
    target: '#nav [data-go="board"]',
    title: "Get it done",
    text: "Tasks on a board. Drag cards between To do, Doing and Done; each move is a one-line change in the note's file.",
  },
  {
    target: '#nav [data-go="notes"]',
    title: "Notes that link",
    text: "Every note, as a list or a folder tree, with bookmarks and search operators like tag:release or path:ideas/. In a note, type [[ to link another one; backlinks and unlinked mentions appear underneath.",
  },
  {
    target: '#nav [data-go="map"]',
    title: "See how it connects",
    text: "The Map draws every link between notes: clusters, orphans and broken links you can fix in one click.",
  },
  {
    target: "#search-btn",
    title: "Do anything from the keyboard",
    text: "Ctrl+K searches every note and runs commands: new note from a template, open a random note, rename, bookmark and more.",
  },
  {
    target: '#nav [data-go="obsidian"]',
    title: "Works with Obsidian",
    text: "Point Cairn at an Obsidian vault and it keeps both in sync. Callouts, embeds, ==highlights==, footnotes and properties look the same here.",
  },
  {
    target: '#nav [data-go="proof"]',
    title: "Proof of work",
    text: "Claims in your notes, checked against GitHub, so your public profile shows what you really shipped.",
  },
  {
    target: "#sidebar-foot",
    title: "Save, sync and settings",
    text: "Your brain is plain files. Commit or sync to GitHub from here, switch light and dark, and open Settings.",
  },
  {
    title: "You're set",
    text: "Start by capturing one thought. You can take this tour again any time: Ctrl+K, then “Take the tour”.",
    finish: "Start capturing",
  },
];

let active = null;

export function tourSeen() {
  try {
    return localStorage.getItem(TOUR_KEY) === "done";
  } catch {
    return true;
  }
}

// First open of a brain you can edit. Automated browsers (tests) only get it when they ask.
export function maybeStartTour() {
  if (tourSeen() || active) return;
  if (navigator.webdriver && !window.CAIRN_TOUR) return;
  if (!state.info || state.info.mode === "visitor" || readOnly()) return;
  setTimeout(() => {
    if (active || tourSeen() || $(".modal-wrap, .palette-wrap, #drawer-layer")) return;
    startTour();
  }, 450);
}

export function startTour() {
  if (active) return;
  const wrap = document.createElement("div");
  wrap.className = "tour";
  wrap.setAttribute("role", "dialog");
  wrap.setAttribute("aria-modal", "true");
  wrap.setAttribute("aria-labelledby", "tour-title");
  wrap.innerHTML = `<div class="tour-hole"></div><div class="tour-card" tabindex="-1"></div>`;
  document.body.appendChild(wrap);
  const openedNav = !$("#app").classList.contains("nav-open");
  active = { wrap, index: 0, openedNav };
  wrap.addEventListener("click", (event) => {
    const button = event.target.closest("[data-tour]");
    if (!button) return;
    const action = button.dataset.tour;
    if (action === "next") go(active.index + 1);
    else if (action === "back") go(active.index - 1);
    else if (action === "skip") finish();
    else if (action === "dot") go(Number(button.dataset.index));
  });
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", place);
  go(0);
}

function onKey(event) {
  if (!active) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    finish();
  } else if (event.key === "ArrowRight" || (event.key === "Enter" && !event.target.closest?.("[data-tour]"))) {
    event.preventDefault();
    event.stopPropagation();
    go(active.index + 1);
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    event.stopPropagation();
    go(active.index - 1);
  } else if (event.key === "Tab") {
    // Keep focus inside the tour card.
    const buttons = [...active.wrap.querySelectorAll(".tour-card button")];
    if (!buttons.length) return;
    const index = buttons.indexOf(document.activeElement);
    event.preventDefault();
    const next = event.shiftKey ? (index <= 0 ? buttons.length - 1 : index - 1) : (index + 1) % buttons.length;
    buttons[next].focus();
  } else if (!event.ctrlKey && !event.metaKey) {
    // The app's single-key shortcuts (C, /) must not fire behind the tour.
    event.stopPropagation();
  }
}

function go(index) {
  if (!active) return;
  if (index >= STEPS.length) return finish({ capture: true });
  active.index = Math.max(0, index);
  const step = STEPS[active.index];
  const last = active.index === STEPS.length - 1;
  const small = matchMedia("(max-width: 900px)").matches;
  const inSidebar = step.target && (step.target.startsWith("#nav") || step.target === "#sidebar-foot");
  $("#app").classList.toggle("nav-open", Boolean(small && inSidebar));
  const card = $(".tour-card", active.wrap);
  card.innerHTML = `
    <div class="tour-count">${active.index + 1} / ${STEPS.length}</div>
    <h2 id="tour-title">${esc(step.title)}</h2>
    <p>${esc(step.text)}</p>
    <div class="tour-dots" aria-hidden="true">${STEPS.map((_, i) => `<button type="button" tabindex="-1" class="${i === active.index ? "on" : ""}" data-tour="dot" data-index="${i}"></button>`).join("")}</div>
    <div class="tour-actions">
      ${last ? "" : `<button type="button" class="btn ghost sm" data-tour="skip">Skip tour</button>`}
      <span class="grow"></span>
      ${active.index ? `<button type="button" class="btn sm" data-tour="back">Back</button>` : ""}
      <button type="button" class="btn sm primary" data-tour="next">${last ? esc(step.finish || "Done") : active.index === 0 ? "Show me" : "Next"}</button>
    </div>`;
  // The sidebar slides in on small screens; measure once it has.
  setTimeout(place, small && inSidebar ? 230 : 0);
  $('[data-tour="next"]', card).focus({ preventScroll: true });
}

function place() {
  if (!active) return;
  const step = STEPS[active.index];
  const hole = $(".tour-hole", active.wrap);
  const card = $(".tour-card", active.wrap);
  const target = step.target ? document.querySelector(step.target) : null;
  const rect = target && target.getClientRects().length ? target.getBoundingClientRect() : null;
  const width = Math.min(360, window.innerWidth - 24);
  card.style.width = `${width}px`;
  if (!rect || rect.width === 0) {
    active.wrap.classList.add("centered");
    Object.assign(hole.style, { top: `${window.innerHeight / 2}px`, left: `${window.innerWidth / 2}px`, width: "0px", height: "0px" });
    card.style.left = `${(window.innerWidth - width) / 2}px`;
    card.style.top = `${Math.max(16, (window.innerHeight - card.offsetHeight) / 2)}px`;
    return;
  }
  active.wrap.classList.remove("centered");
  const pad = 6;
  Object.assign(hole.style, {
    top: `${rect.top - pad}px`,
    left: `${rect.left - pad}px`,
    width: `${rect.width + pad * 2}px`,
    height: `${rect.height + pad * 2}px`,
  });
  const gap = 14;
  const height = card.offsetHeight;
  let left;
  let top;
  if (rect.right + gap + width <= window.innerWidth - 12) {
    left = rect.right + gap;
    top = rect.top + rect.height / 2 - height / 2;
  } else if (rect.bottom + gap + height <= window.innerHeight - 12) {
    left = rect.left + rect.width / 2 - width / 2;
    top = rect.bottom + gap;
  } else {
    left = rect.left + rect.width / 2 - width / 2;
    top = rect.top - gap - height;
  }
  card.style.left = `${Math.max(12, Math.min(left, window.innerWidth - width - 12))}px`;
  card.style.top = `${Math.max(12, Math.min(top, window.innerHeight - height - 12))}px`;
}

function finish({ capture = false } = {}) {
  if (!active) return;
  try {
    localStorage.setItem(TOUR_KEY, "done");
  } catch {
    /* private mode: the tour just shows again next time */
  }
  document.removeEventListener("keydown", onKey, true);
  window.removeEventListener("resize", place);
  active.wrap.remove();
  $("#app").classList.remove("nav-open");
  active = null;
  if (capture && !readOnly()) $("#capture-input")?.focus();
}

export const tourOpen = () => Boolean(active);
