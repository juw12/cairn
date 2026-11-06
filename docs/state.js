// App state and the data layer: load, create, save and delete notes through the active store.

import * as core from "./core.js";
import { ConflictError } from "./store.js";
import { $, toast, today } from "./ui.js";
import { closeDrawer, renderDrawer } from "./drawer.js";
import { render, renderNav, renderSidebarFoot, renderView } from "./app.js";

export const VIEWS = [
  { id: "home", label: "Today", icon: "home", group: "Think & do" },
  { id: "ideas", label: "Ideas", icon: "idea", group: "Think & do" },
  { id: "board", label: "Board", icon: "board", group: "Think & do" },
  { id: "decisions", label: "Decisions", icon: "decision", group: "Think & do" },
  { id: "notes", label: "Notes", icon: "note", group: "Remember" },
  { id: "journal", label: "Journal", icon: "journal", group: "Remember" },
  { id: "map", label: "Map", icon: "map", group: "Remember" },
  { id: "code", label: "Code inbox", icon: "code", group: "Project" },
  { id: "context", label: "Context pack", icon: "sparkle", group: "Project" },
  { id: "people", label: "People", icon: "users", group: "Network" },
];

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
};

export const readOnly = () => Boolean(state.info?.readOnly);

// ============================================================ data

function rebuildNotes() {
  state.notes = state.files
    .filter((f) => !core.SPECIAL_FILES.includes(f.path))
    .map((f) => core.noteFromFile(f.path, f.text, f.mtime));
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
    const meta = { ...note.meta };
    if (changes.title !== undefined) meta.title = changes.title;
    if (changes.status !== undefined) meta.status = changes.status;
    if (changes.tags !== undefined) meta.tags = changes.tags;
    const body = changes.body !== undefined ? changes.body : note.body;
    const text = core.serializeNote(meta, body);
    if (text === fileByPath(path)?.text) return noteByPath(path);
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
