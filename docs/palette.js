// The command palette (Ctrl+K).

import { escapeHtml as esc } from "./md.js";
import { $, TYPE_META, byRecent, icon } from "./ui.js";
import { VIEWS, refreshTodos, state } from "./state.js";
import { openNote } from "./drawer.js";
import { capture, commitBrain, newNote, openToday, saveContext, switchBrain, toggleTheme } from "./actions.js";
import { setView } from "./app.js";

function commands() {
  const list = VIEWS.map((v) => ({ label: `Go to ${v.label}`, kind: "View", icon: v.icon, run: () => setView(v.id) }));
  list.push(
    { label: "New idea", kind: "Create", icon: "idea", run: () => newNote("idea") },
    { label: "New task", kind: "Create", icon: "task", run: () => newNote("task") },
    { label: "New decision", kind: "Create", icon: "decision", run: () => newNote("decision") },
    { label: "New note", kind: "Create", icon: "note", run: () => newNote("note") },
    { label: "Open today’s journal", kind: "Create", icon: "journal", run: openToday },
    { label: "Save context pack to brain/CONTEXT.md", kind: "Project", icon: "sparkle", run: saveContext },
    { label: "Rescan code for TODOs", kind: "Project", icon: "code", run: () => setView("code") || refreshTodos() },
    { label: "Toggle light / dark theme", kind: "App", icon: "moon", run: toggleTheme },
    { label: "Switch brain", kind: "App", icon: "folder", run: switchBrain },
  );
  if (state.info?.canGit) list.push({ label: "Commit brain", kind: "Project", icon: "git", run: commitBrain });
  return list;
}

export function openPalette() {
  if ($(".palette-wrap")) return;
  const wrap = document.createElement("div");
  wrap.className = "palette-wrap";
  wrap.innerHTML = `<div class="palette" role="dialog" aria-modal="true" aria-label="Search and commands"><input placeholder="Search notes or type a command…" aria-label="Search notes or type a command" autocomplete="off"><div class="palette-list" role="listbox"></div></div>`;
  document.body.appendChild(wrap);
  const input = $("input", wrap);
  const listEl = $(".palette-list", wrap);
  let items = [];
  let active = 0;
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    const cmd = commands().filter((c) => !q || c.label.toLowerCase().includes(q));
    const found = state.notes
      .map((n) => {
        const inTitle = n.title.toLowerCase().includes(q);
        const inBody = !inTitle && q && n.body.toLowerCase().includes(q);
        return { n, score: inTitle ? 2 : inBody ? 1 : 0 };
      })
      .filter((r) => !q || r.score > 0)
      .sort((a, b) => b.score - a.score || byRecent(a.n, b.n))
      .slice(0, q ? 8 : 5)
      .map(({ n }) => ({ label: n.title, kind: TYPE_META[n.type].label, icon: TYPE_META[n.type].icon, run: () => openNote(n.path) }));
    const ordered = q ? [...found, ...cmd.slice(0, 6)] : [...cmd.slice(0, 6), ...found];
    items = ordered;
    active = Math.min(active, Math.max(items.length - 1, 0));
    const section = (title, list, offset) =>
      list.length
        ? `<div class="palette-section">${title}</div>${list
            .map((item, i) => `<button class="palette-item ${i + offset === active ? "on" : ""}" data-index="${i + offset}" role="option">${icon(item.icon, "sm")}<span class="label">${esc(item.label)}</span><span class="kind">${item.kind}</span></button>`)
            .join("")}`
        : "";
    const first = q ? found : cmd.slice(0, 6);
    const second = q ? cmd.slice(0, 6) : found;
    listEl.innerHTML =
      section(q ? "Notes" : "Commands", first, 0) + section(q ? "Commands" : "Recent", second, first.length) ||
      `<div class="empty">No matches. Press Enter to capture “${esc(input.value)}” as an idea.</div>`;
  };
  const close = () => wrap.remove();
  const runActive = () => {
    const item = items[active];
    close();
    if (item) item.run();
    else if (input.value.trim()) capture(input.value.trim());
  };
  input.addEventListener("input", () => {
    active = 0;
    draw();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      active = Math.min(active + 1, items.length - 1);
      draw();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      active = Math.max(active - 1, 0);
      draw();
    } else if (e.key === "Enter") {
      e.preventDefault();
      runActive();
    } else if (e.key === "Escape") {
      e.stopPropagation();
      close();
    }
  });
  listEl.addEventListener("click", (e) => {
    const button = e.target.closest("[data-index]");
    if (!button) return;
    active = Number(button.dataset.index);
    runActive();
  });
  wrap.addEventListener("click", (e) => {
    if (e.target === wrap) close();
  });
  draw();
  input.focus();
}
