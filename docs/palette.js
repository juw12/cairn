// The command palette (Ctrl+K), plus pick(): the same list UI for choosing one thing.

import { escapeHtml as esc } from "./md.js";
import { $, TYPE_META, ask, byRecent, icon, toast } from "./ui.js";
import { VIEWS, createNote, noteByPath, readOnly, refreshTodos, state } from "./state.js";
import { bookmarkOpenNote, closeOtherTabs, closeTab, cycleTab, flushPending, insertIntoOpenNote, openInNewTab, openNote, openTabs, renameOpenNote, reopenClosedTab } from "./drawer.js";
import { chooseCanvas, newCanvas } from "./canvas.js";
import { pluginCommands, pluginViews } from "./plugins.js";
import { capture, commitBrain, newNote, openSettings, openToday, saveContext, switchBrain, syncBrain, toggleTheme } from "./actions.js";
import { syncObsidian } from "./obsidian.js";
import { fillTemplate, searchNotes, searchScore, templates } from "./vault.js";
import { startTour } from "./tour.js";
import { render, setView } from "./app.js";

function commands() {
  const list = [...VIEWS, ...pluginViews()].map((v) => ({ label: `Go to ${v.label}`, kind: v.group === "Plugins" ? "Plugin" : "View", icon: v.icon, run: () => setView(v.id) }));
  const editable = !readOnly();
  if (editable) {
    list.push(
      { label: "New idea", kind: "Create", icon: "idea", run: () => newNote("idea") },
      { label: "New task", kind: "Create", icon: "task", run: () => newNote("task") },
      { label: "New decision", kind: "Create", icon: "decision", run: () => newNote("decision") },
      { label: "New note", kind: "Create", icon: "note", run: () => newNote("note") },
      { label: "New note from template", kind: "Create", icon: "template", run: newFromTemplate },
      { label: "New canvas", kind: "Create", icon: "canvas", run: newCanvas },
      { label: "Open today’s journal", kind: "Create", icon: "journal", run: openToday },
    );
    if (state.open) {
      list.push(
        { label: "Insert template into this note", kind: "Note", icon: "template", run: insertTemplate },
        { label: "Rename or move this note", kind: "Note", icon: "pencil", run: renameOpenNote },
      );
    }
    list.push({ label: "Save context pack to brain/CONTEXT.md", kind: "Project", icon: "sparkle", run: saveContext });
  }
  if (state.open) list.push({ label: "Toggle bookmark on this note", kind: "Note", icon: "star", run: () => bookmarkOpenNote() });
  list.push({ label: "Open a canvas…", kind: "Navigate", icon: "canvas", run: chooseCanvas });
  list.push({ label: "Open a note in a new tab…", kind: "Tabs", icon: "tabs", run: openInNewTab });
  const tabs = openTabs();
  if (tabs.length > 1) {
    list.push(
      { label: "Next tab", kind: "Tabs  Ctrl+Alt+→", icon: "tabs", run: () => cycleTab(1) },
      { label: "Previous tab", kind: "Tabs  Ctrl+Alt+←", icon: "tabs", run: () => cycleTab(-1) },
      { label: "Close other tabs", kind: "Tabs", icon: "tabs", run: closeOtherTabs },
    );
  }
  if (tabs.length) list.push({ label: "Close this tab", kind: "Tabs  Ctrl+Alt+W", icon: "x", run: () => closeTab() });
  if ((state.closedTabs || []).length) list.push({ label: "Reopen closed tab", kind: "Tabs  Ctrl+Alt+T", icon: "tabs", run: reopenClosedTab });
  list.push({ label: "Manage plugins", kind: "App", icon: "puzzle", run: () => setView("plugins") });
  list.push(...pluginCommands());
  list.push(
    { label: "Open a random note", kind: "Navigate", icon: "shuffle", run: openRandom },
    { label: "Show bookmarks", kind: "Navigate", icon: "star", run: () => {
      state.notesFilter = "bookmarks";
      setView("notes");
    } },
    { label: "Show folders", kind: "Navigate", icon: "folder", run: () => {
      state.notesLayout = "folders";
      setView("notes");
    } },
    { label: "Rescan code for TODOs", kind: "Project", icon: "code", run: () => setView("code") || refreshTodos() },
    { label: "Toggle light / dark theme", kind: "App", icon: "moon", run: toggleTheme },
    { label: "Take the tour", kind: "Help", icon: "help", run: startTour },
    { label: "Switch brain", kind: "App", icon: "folder", run: switchBrain },
  );
  if (state.info?.canGit) list.push({ label: "Commit brain", kind: "Project", icon: "git", run: commitBrain });
  if (state.obsidian?.status?.linked) list.push({ label: "Sync with Obsidian now", kind: "Project", icon: "vault", run: () => syncObsidian() });
  if (state.info?.canGit && state.git?.remote) list.push({ label: "Sync with GitHub", kind: "Project", icon: "refresh", run: syncBrain });
  if (state.info?.desktop) list.push({ label: "Settings", kind: "App", icon: "gear", run: openSettings });
  return list;
}

function openRandom() {
  const pool = state.notes.filter((n) => n.path !== state.open && !/^(_?templates)\//i.test(n.path));
  if (!pool.length) return toast("No notes yet.");
  openNote(pool[Math.floor(Math.random() * pool.length)].path);
}

function chooseTemplate() {
  const list = templates();
  if (!list.length) {
    toast("No templates yet. Put notes in a Templates/ folder; {{title}}, {{date}} and {{time}} are filled in.");
    return Promise.resolve(null);
  }
  return pick(list.map((n) => ({ label: n.title, kind: n.path, icon: "template", value: n })), "Choose a template…");
}

async function newFromTemplate() {
  const template = await chooseTemplate();
  if (!template) return;
  const title = await ask({ title: "New note from template", text: `Using “${template.title}”.`, placeholder: "Title", confirm: "Create" });
  if (!title) return;
  const body = fillTemplate(template.body, { title });
  const extra = Object.fromEntries(Object.entries(template.meta).filter(([key]) => !["title", "type", "created", "status", "tags"].includes(key)));
  const note = await createNote({ type: "note", title, tags: template.tags, body, extraMeta: extra });
  render();
  openNote(note.path);
}

async function insertTemplate() {
  const path = state.open;
  const template = await chooseTemplate();
  if (!template || !noteByPath(path)) return;
  state.open = path;
  await flushPending();
  const done = await insertIntoOpenNote(fillTemplate(template.body, { title: noteByPath(path).title }));
  if (done) toast(`Inserted “${template.title}”`);
}

// A searchable list in the palette's style. Resolves with the chosen item's value, or null.
export function pick(items, placeholder = "Choose…") {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "palette-wrap";
    wrap.innerHTML = `<div class="palette" role="dialog" aria-modal="true" aria-label="${esc(placeholder)}"><input placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}" autocomplete="off"><div class="palette-list" role="listbox"></div></div>`;
    document.body.appendChild(wrap);
    const input = $("input", wrap);
    const listEl = $(".palette-list", wrap);
    let shown = [];
    let active = 0;
    const draw = () => {
      const q = input.value.trim().toLowerCase();
      shown = items.filter((item) => !q || item.label.toLowerCase().includes(q) || (item.kind || "").toLowerCase().includes(q));
      active = Math.min(active, Math.max(shown.length - 1, 0));
      listEl.innerHTML = shown.length
        ? shown.map((item, i) => `<button class="palette-item ${i === active ? "on" : ""}" data-index="${i}" role="option">${icon(item.icon || "note", "sm")}<span class="label">${esc(item.label)}</span><span class="kind">${esc(item.kind || "")}</span></button>`).join("")
        : `<div class="empty">Nothing matches.</div>`;
    };
    const done = (value) => {
      wrap.remove();
      resolve(value);
    };
    input.addEventListener("input", () => {
      active = 0;
      draw();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        active = Math.max(0, Math.min(shown.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
        draw();
      } else if (e.key === "Enter") {
        e.preventDefault();
        done(shown[active] ? shown[active].value : null);
      } else if (e.key === "Escape") {
        e.stopPropagation();
        done(null);
      }
    });
    listEl.addEventListener("click", (e) => {
      const button = e.target.closest("[data-index]");
      if (button) done(shown[Number(button.dataset.index)].value);
    });
    wrap.addEventListener("click", (e) => {
      if (e.target === wrap) done(null);
    });
    draw();
    input.focus();
  });
}

export function openPalette() {
  if ($(".palette-wrap")) return;
  const wrap = document.createElement("div");
  wrap.className = "palette-wrap";
  wrap.innerHTML = `<div class="palette" role="dialog" aria-modal="true" aria-label="Search and commands"><input placeholder="Search notes or type a command… (tag:, path:, &quot;exact&quot;)" aria-label="Search notes or type a command" autocomplete="off"><div class="palette-list" role="listbox"></div></div>`;
  document.body.appendChild(wrap);
  const input = $("input", wrap);
  const listEl = $(".palette-list", wrap);
  let items = [];
  let active = 0;
  const draw = () => {
    const raw = input.value.trim();
    const q = raw.toLowerCase();
    const cmd = commands().filter((c) => !q || c.label.toLowerCase().includes(q));
    const matched = q ? searchNotes(state.notes, raw) : state.notes;
    const found = matched
      .map((n) => ({ n, score: q ? searchScore(n, raw) : 0 }))
      .sort((a, b) => b.score - a.score || byRecent(a.n, b.n))
      .slice(0, q ? 8 : 5)
      .map(({ n }) => ({ label: n.title, kind: TYPE_META[n.type].label, icon: TYPE_META[n.type].icon, run: () => openNote(n.path) }));
    const exact = state.notes.some((n) => n.title.toLowerCase() === q);
    const create = q && !readOnly() && !exact && !/^[-\w]+:/.test(raw)
      ? [{ label: `Create note “${raw}”`, kind: "Create", icon: "plus", run: async () => {
          const note = await createNote({ type: "note", title: raw });
          render();
          openNote(note.path);
        } }]
      : [];
    // Matching notes, then matching commands; "Create note" comes last so it never hides a command.
    const first = q ? found : cmd.slice(0, 6);
    const second = q ? [...cmd.slice(0, 6), ...create] : found;
    items = [...first, ...second];
    active = Math.min(active, Math.max(items.length - 1, 0));
    const section = (title, list, offset) =>
      list.length
        ? `<div class="palette-section">${title}</div>${list
            .map((item, i) => `<button class="palette-item ${i + offset === active ? "on" : ""}" data-index="${i + offset}" role="option">${icon(item.icon, "sm")}<span class="label">${esc(item.label)}</span><span class="kind">${item.kind}</span></button>`)
            .join("")}`
        : "";
    listEl.innerHTML =
      section(q ? "Notes" : "Commands", first, 0) + section(q ? "Commands" : "Recent", second, first.length) ||
      `<div class="empty">No matches. Press Enter to capture “${esc(input.value)}” as an idea.</div>`;
  };
  const close = () => wrap.remove();
  const runActive = () => {
    const item = items[active];
    close();
    if (item) item.run();
    else if (input.value.trim() && !readOnly()) capture(input.value.trim());
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

