// Presentation helpers shared by every view: icons, pills, page heads, dialogs and toasts.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { readOnly } from "./state.js";

// ============================================================ constants

const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>',
  idea: '<path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1 2V16h5.2v-.2c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z"/>',
  board: '<rect x="3" y="4" width="5" height="16" rx="1.5"/><rect x="10" y="4" width="5" height="11" rx="1.5"/><rect x="17" y="4" width="4" height="7" rx="1.5"/>',
  task: '<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path d="m8 12 3 3 5-6"/>',
  note: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/><path d="M8.5 13h7M8.5 17h5"/>',
  decision: '<path d="M12 3v18"/><path d="M5 5.5h11l3 3-3 3H5z"/><path d="M19 13.5H8l-3 3 3 3h11z" opacity=".55"/>',
  journal: '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  map: '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.2 10.9 15.8 7.1M8.2 13.1l7.6 3.8"/>',
  code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>',
  sparkle: '<path d="M12 3l1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7-4.7-1.8 4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  git: '<circle cx="12" cy="12" r="3.5"/><path d="M3 12h5.5M15.5 12H21"/>',
  branch: '<circle cx="6" cy="5.5" r="2"/><circle cx="6" cy="18.5" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7.5v9M18 10c0 4-6 3-11.5 7"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
  play: '<path d="M7 5v14l11-7z"/>',
  zap: '<path d="M13 3 5 14h6l-1 7 8-11h-6z"/>',
  save: '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z"/><path d="M8 3v5h7M8 21v-7h8v7"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17" cy="9" r="2.6"/><path d="M16.5 14.2c2.6.2 4.5 2.6 4.5 5.8"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  github: '<path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>',
};

export const icon = (name, cls = "") => `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

export const LOGO = `<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="var(--ink)"/><ellipse cx="16" cy="23.6" rx="9.2" ry="3.4" fill="var(--bg)"/><ellipse cx="15.2" cy="17.2" rx="6.4" ry="2.8" fill="var(--bg)" fill-opacity=".9"/><ellipse cx="16.4" cy="11.4" rx="3.8" ry="2.3" fill="var(--accent)"/></svg>`;

export const TYPE_META = {
  idea: { label: "Idea", icon: "idea" },
  task: { label: "Task", icon: "task" },
  decision: { label: "Decision", icon: "decision" },
  note: { label: "Note", icon: "note" },
  journal: { label: "Journal", icon: "journal" },
};

export const STATUS_LABEL = {
  raw: "Raw", exploring: "Exploring", ready: "Ready", parked: "Parked",
  todo: "To do", doing: "Doing", done: "Done",
  proposed: "Proposed", accepted: "Accepted", superseded: "Superseded",
};

export const STATUS_HINT = {
  raw: "Just captured. No judging yet.",
  exploring: "Worth thinking through.",
  ready: "Clear enough to act on.",
  parked: "Not now, maybe later.",
};

export const SPARKS = [
  "What would make this 10× simpler?",
  "Who would pay for this, and why today?",
  "What is the smallest version we could test this week?",
  "What would we do if we had to ship it tomorrow?",
  "What is the riskiest assumption here?",
  "How would a competitor copy this, and what stops them?",
  "What does this look like if it fails? Why did it fail?",
  "What would we remove to make room for this?",
  "Which existing note or decision does this contradict?",
  "If this works, what becomes possible next?",
  "Explain it to a new teammate in two sentences.",
  "What evidence would change our mind?",
  "What is the opposite idea, and is it better?",
  "Which part could be automated or deleted?",
  "What would the user do the moment before and after using this?",
  "What constraint, if removed, makes this trivial?",
  "Combine this with another idea on the wall. What appears?",
  "How would we measure that it worked?",
];

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function relativeTime(ms) {
  if (!ms) return "";
  const diff = Date.now() - ms;
  const minutes = Math.round(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

// ============================================================ shared pieces

export function typePill(type) {
  const meta = TYPE_META[type] || TYPE_META.note;
  return `<span class="pill t-${type}">${icon(meta.icon, "sm")}${meta.label}</span>`;
}

export function statusPill(status) {
  if (!status) return "";
  return `<span class="pill status-${esc(status)}">${esc(STATUS_LABEL[status] || status)}</span>`;
}

export function tagChips(tags, max = 3) {
  if (!tags.length) return "";
  const shown = tags.slice(0, max).map((t) => `<span class="tag">#${esc(t)}</span>`).join("");
  const more = tags.length > max ? `<span class="tag">+${tags.length - max}</span>` : "";
  return `<span class="tags">${shown}${more}</span>`;
}

function plainText(line) {
  return line
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[*_`~]+/g, "")
    .trim();
}

export function snippetOf(note) {
  const prose = note.body
    .split("\n")
    .filter((line) => !/^\s*(\||#|```|---)/.test(line))
    .join("\n");
  return plainText(core.summary(prose));
}

export function checklist(note) {
  const all = note.body.match(/^\s*(?:[-*]|\d+\.)\s+\[[ xX]\]/gm) || [];
  const done = note.body.match(/^\s*(?:[-*]|\d+\.)\s+\[[xX]\]/gm) || [];
  return { total: all.length, done: done.length };
}

export function rowItem(note, meta = "") {
  return `<button class="row-item" data-open="${esc(note.path)}"><span class="dot t-${note.type}"></span><span class="title">${esc(note.title)}</span><span class="meta">${meta}</span></button>`;
}

export function emptyState(iconName, title, text, action = "") {
  return `<div class="empty-state">${icon(iconName)}<h3>${title}</h3><p>${text}</p>${action}</div>`;
}

export function pageHead(title, sub, actions = "", eyebrow = "") {
  // Someone else's brain is read-only: no create buttons, so nothing promises an action we would refuse.
  if (readOnly()) actions = "";
  return `<div class="page-head"><div>${eyebrow ? `<p class="eyebrow">${eyebrow}</p>` : ""}<h1>${title}</h1>${sub ? `<p>${sub}</p>` : ""}</div><div class="actions">${actions}</div></div>`;
}

export function byRecent(a, b) {
  return (b.mtime || 0) - (a.mtime || 0);
}

// ============================================================ modal, toast, palette

export function ask({ title, text = "", placeholder = "", value = "", confirm = "OK", list = null, noInput = false, danger = false }) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "modal-wrap";
    wrap.innerHTML = `
      <form class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <h2>${esc(title)}</h2>
        ${text ? `<p>${esc(text)}</p>` : ""}
        ${list ? `<ul>${list.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : ""}
        ${noInput ? "" : `<input class="input" name="value" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off">`}
        <div class="actions">
          ${noInput ? "" : `<button type="button" class="btn ghost" data-cancel>Cancel</button>`}
          <button type="submit" class="btn ${danger ? "danger" : "primary"}">${esc(confirm)}</button>
        </div>
      </form>`;
    const done = (result) => {
      wrap.remove();
      resolve(result);
    };
    wrap.addEventListener("click", (e) => {
      if (e.target === wrap || e.target.closest("[data-cancel]")) done(null);
    });
    wrap.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        done(null);
      }
    });
    $("form", wrap).addEventListener("submit", (e) => {
      e.preventDefault();
      done(noInput ? true : $("input", wrap).value.trim());
    });
    document.body.appendChild(wrap);
    setTimeout(() => ($("input", wrap) || $("button[type=submit]", wrap)).focus(), 20);
  });
}

export function toast(message, { action = null, error = false } = {}) {
  const el = document.createElement("div");
  el.className = `toast${error ? " error" : ""}`;
  el.innerHTML = `<span>${esc(message)}</span>${action ? `<button class="btn sm">${esc(action.label)}</button>` : ""}`;
  if (action) {
    $("button", el).addEventListener("click", () => {
      el.remove();
      action.run();
    });
  }
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), action ? 6500 : 4000);
}

// Broken avatars (offline, blocked CDN) fall back to a quiet neutral disc instead of a torn-image icon.
const AVATAR_FALLBACK =
  "data:image/svg+xml," +
  encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="5" fill="#d8d2c4"/></svg>');
document.addEventListener(
  "error",
  (event) => {
    const image = event.target;
    if (!(image instanceof HTMLImageElement) || image.dataset.fallback) return;
    image.dataset.fallback = "1";
    image.src = AVATAR_FALLBACK;
  },
  true,
);
