// App state and the data layer: load, create, save and delete notes through the active store.

import * as core from "./core.js";
import { ConflictError } from "./store.js";
import { $, toast, today } from "./ui.js";
import { closeDrawer, forgetTab, renderDrawer } from "./drawer.js";
import { notesChanged } from "./plugins.js";
import { render, renderNav, renderSidebarFoot, renderView } from "./app.js";

export const VIEWS = [
  { id: "home", label: "Today", icon: "home", group: "Think & do" },
  { id: "ideas", label: "Ideas", icon: "idea", group: "Think & do" },
  { id: "board", label: "Board", icon: "board", group: "Think & do" },
  { id: "decisions", label: "Decisions", icon: "decision", group: "Think & do" },
  { id: "notes", label: "Notes", icon: "note", group: "Remember" },
  { id: "journal", label: "Journal", icon: "journal", group: "Remember" },
  { id: "map", label: "Map", icon: "map", group: "Remember" },
  { id: "canvas", label: "Canvas", icon: "canvas", group: "Remember" },
  { id: "code", label: "Code inbox", icon: "code", group: "Project" },
  { id: "projects", label: "Projects", icon: "star", group: "Project" },
  { id: "vibe", label: "Vibe", icon: "activity", group: "Project" },
  { id: "autopilot", label: "Autopilot", icon: "route", group: "Project" },
  { id: "founder", label: "Founder Studio", icon: "target", group: "Project" },
  { id: "context", label: "Context pack", icon: "sparkle", group: "Project" },
  { id: "obsidian", label: "Obsidian sync", icon: "vault", group: "Project" },
  { id: "plugins", label: "Plugins", icon: "puzzle", group: "Project" },
  { id: "people", label: "People", icon: "users", group: "Network" },
  { id: "proof", label: "Proof", icon: "check", group: "Network" },
];

// The sidebar shows one entry per hub. A hub with several pages shows them as tabs at the top of the page;
// the first page is the one it opens on a fresh browser, and `count` names the page whose count the entry shows.
export const HUBS = [
  { id: "today", label: "Today", icon: "home", views: ["home"] },
  { id: "plan", label: "Plan", icon: "board", views: ["board", "ideas", "decisions"], count: "board" },
  { id: "notes", label: "Notes", icon: "note", views: ["notes", "journal"], count: "notes" },
  { id: "map", label: "Map", icon: "map", views: ["map", "canvas"] },
  { id: "build", label: "Build", icon: "code", views: ["vibe", "projects", "autopilot", "code"], count: "vibe" },
  { id: "founder", label: "Founder Studio", icon: "target", views: ["founder"] },
  { id: "integrations", label: "Integrations", icon: "puzzle", views: ["context", "obsidian", "plugins"] },
  { id: "people", label: "People", icon: "users", views: ["people", "proof"] },
];

export const hubOf = (view) => HUBS.find((hub) => hub.views.includes(view)) || null;

export const REPO_URL = "https://github.com/juw12/cairn";
export const LAST_BRAIN_KEY = "cairn.lastBrain";

// ============================================================ state

export const state = {
  store: null,
  info: null,
  files: [],
  notes: [],
  view: "home",
  open: null,
  todos: null,
  todosError: "",
  git: null,
  notesFilter: "all",
  notesQuery: "",
  notesTag: null,
  editorMode: "write",
  contextMode: "preview",
  spark: null,
  peopleQuery: "",
  profile: null,
  discover: null,
  obsidian: null,
  proofCheck: null,
  canvas: null,
  tabs: null,
  lastTab: null,
  closedTabs: [],
};

export const readOnly = () => Boolean(state.info?.readOnly);

// ============================================================ data

function rebuildNotes() {
  state.notes = state.files
    .filter((f) => core.isNotePath(f.path) && !core.SPECIAL_FILES.includes(f.path))
    .map((f) => core.noteFromFile(f.path, f.text, f.mtime));
  notesChanged();
}

export async function loadAll() {
  state.files = await state.store.list();
  rebuildNotes();
}

export function noteByPath(path) {
  return state.notes.find((n) => n.path === path) || null;
}

export function fileByPath(path) {
  return state.files.find((f) => f.path === path) || null;
}

export function upsertFile(path, text, mtime) {
  const existing = fileByPath(path);
  if (text === null) {
    state.files = state.files.filter((f) => f.path !== path);
  } else if (existing) {
    existing.text = text;
    existing.mtime = mtime;
  } else {
    state.files.push({ path, text, mtime });
    state.files.sort((a, b) => (a.path < b.path ? -1 : 1));
  }
  rebuildNotes();
}

export async function createNote({ type, title, tags = [], body = null, status = null, extraMeta = {} }) {
  const path = core.notePath(type, title, state.files.map((f) => f.path), today());
  const meta = { title: title || today(), type };
  const initialStatus = status || core.defaultStatus(type);
  if (initialStatus) meta.status = initialStatus;
  meta.tags = tags;
  meta.created = today();
  Object.assign(meta, extraMeta);
  const text = core.serializeNote(meta, body ?? core.TEMPLATES[type] ?? "");
  const { mtime } = await state.store.write(path, text, null);
  upsertFile(path, text, mtime);
  scheduleGitRefresh();
  return noteByPath(path);
}

const saveChains = new Map();

export function saveNote(path, changes) {
  const run = async () => {
    const note = noteByPath(path);
    if (!note) return null;
    // Only properties that really changed are written, and everything else in the file keeps its exact
    // bytes (order, comments, list style, CRLF), so notes from Obsidian or any editor stay as they were.
    const properties = {};
    if (changes.title !== undefined && changes.title !== note.title) properties.title = changes.title;
    if (changes.status !== undefined && changes.status !== note.status) properties.status = changes.status;
    if (changes.tags !== undefined && JSON.stringify(changes.tags) !== JSON.stringify(note.tags)) properties.tags = changes.tags;
    // Any other front-matter property (the editor's Properties panel); core.REMOVE deletes one.
    for (const [key, value] of Object.entries(changes.properties || {})) properties[key] = value;
    const original = fileByPath(path)?.text ?? "";
    const text = core.updateNoteText(original, properties, changes.body);
    if (text === original) return noteByPath(path);
    try {
      const { mtime } = await state.store.write(path, text, note.mtime);
      upsertFile(path, text, mtime);
      scheduleGitRefresh();
      return noteByPath(path);
    } catch (error) {
      if (error instanceof ConflictError) {
        // Show the newer version, and hold on to ours so nothing typed is lost.
        state.held = { path, text, kind: "conflict" };
        upsertFile(path, error.text, error.mtime);
        toast("This note changed somewhere else. Both versions are kept; pick one in the note.");
        if (state.open === path) renderDrawer();
        render();
        return null;
      }
      state.held = { path, text, kind: "failed", message: error.message || "Could not save" };
      toast(`${error.message || "Could not save"} Your text is kept in the note.`, { error: true });
      if (state.open === path) renderDrawer();
      throw error;
    }
  };
  const next = (saveChains.get(path) || Promise.resolve()).then(run, run);
  saveChains.set(path, next.catch(() => null));
  return next;
}

export async function deleteNote(path) {
  const file = fileByPath(path);
  if (!file) return;
  try {
    await state.store.remove(path);
  } catch (error) {
    toast(error.message || "Couldn't delete that note.", { error: true });
    return;
  }
  upsertFile(path, null);
  scheduleGitRefresh();
  forgetTab(path);
  closeDrawer();
  render();
  toast(`Deleted brain/${path}`, {
    action: {
      label: "Undo",
      run: async () => {
        const { mtime } = await state.store.write(path, file.text, null);
        upsertFile(path, file.text, mtime);
        render();
      },
    },
  });
}

export async function refreshTodos() {
  if (!state.info?.canScan) return;
  try {
    state.todos = await state.store.todos();
    state.todosError = "";
  } catch (error) {
    state.todos = [];
    state.todosError = error.message || "Could not scan the code";
  }
  renderNav();
  if (["home", "code"].includes(state.view)) renderView();
}

let gitTimer = null;
export function scheduleGitRefresh() {
  if (!state.info?.canGit) return;
  clearTimeout(gitTimer);
  gitTimer = setTimeout(refreshGit, 700);
}

export async function refreshGit() {
  if (!state.info?.canGit) return;
  try {
    state.git = await state.store.gitStatus();
  } catch {
    state.git = null;
  }
  renderSidebarFoot();
}

// ============================================================ guide (first-run)

export const GUIDE_KEY = "cairn.guide";

export function guide() {
  try {
    return { captured: false, moved: false, promoted: false, dismissed: false, ...JSON.parse(localStorage.getItem(GUIDE_KEY) || "{}") };
  } catch {
    return { captured: false, moved: false, promoted: false, dismissed: false };
  }
}

export function markGuide(step) {
  const current = guide();
  if (current[step]) return;
  current[step] = true;
  localStorage.setItem(GUIDE_KEY, JSON.stringify(current));
  if (current.captured && current.moved && current.promoted) {
    toast("That's the whole loop: capture, brainstorm, act. Everything is plain Markdown in brain/.");
  }
}
