import * as core from "./core.js";
import { renderMarkdown, toggleTask, escapeHtml as esc } from "./md.js";
import { ServerStore, FolderStore, DemoStore, GitHubStore, ConflictError } from "./store.js";
import * as gh from "./github.js";
import * as auth from "./auth.js";

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
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
  play: '<path d="M7 5v14l11-7z"/>',
  zap: '<path d="M13 3 5 14h6l-1 7 8-11h-6z"/>',
  save: '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z"/><path d="M8 3v5h7M8 21v-7h8v7"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17" cy="9" r="2.6"/><path d="M16.5 14.2c2.6.2 4.5 2.6 4.5 5.8"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  github: '<path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>',
};

const icon = (name, cls = "") => `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

const LOGO = `<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="var(--ink)"/><ellipse cx="16" cy="23.6" rx="9.2" ry="3.4" fill="var(--bg)"/><ellipse cx="15.2" cy="17.2" rx="6.4" ry="2.8" fill="var(--bg)" fill-opacity=".9"/><ellipse cx="16.4" cy="11.4" rx="3.8" ry="2.3" fill="var(--accent)"/></svg>`;

const TYPE_META = {
  idea: { label: "Idea", icon: "idea" },
  task: { label: "Task", icon: "task" },
  decision: { label: "Decision", icon: "decision" },
  note: { label: "Note", icon: "note" },
  journal: { label: "Journal", icon: "journal" },
};

const STATUS_LABEL = {
  raw: "Raw", exploring: "Exploring", ready: "Ready", parked: "Parked",
  todo: "To do", doing: "Doing", done: "Done",
  proposed: "Proposed", accepted: "Accepted", superseded: "Superseded",
};

const STATUS_HINT = {
  raw: "Just captured. No judging yet.",
  exploring: "Worth thinking through.",
  ready: "Clear enough to act on.",
  parked: "Not now, maybe later.",
};

const SPARKS = [
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

const VIEWS = [
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

const REPO_URL = "https://github.com/juw12/cairn";
const LAST_BRAIN_KEY = "cairn.lastBrain";

// ============================================================ state

const state = {
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

const readOnly = () => Boolean(state.info?.readOnly);

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function relativeTime(ms) {
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

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

// ============================================================ data

function rebuildNotes() {
  state.notes = state.files
    .filter((f) => !core.SPECIAL_FILES.includes(f.path))
    .map((f) => core.noteFromFile(f.path, f.text, f.mtime));
}

async function loadAll() {
  state.files = await state.store.list();
  rebuildNotes();
}

function noteByPath(path) {
  return state.notes.find((n) => n.path === path) || null;
}

function fileByPath(path) {
  return state.files.find((f) => f.path === path) || null;
}

function upsertFile(path, text, mtime) {
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

async function createNote({ type, title, tags = [], body = null, status = null, extraMeta = {} }) {
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

function saveNote(path, changes) {
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

async function deleteNote(path) {
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

async function refreshTodos() {
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
function scheduleGitRefresh() {
  if (!state.info?.canGit) return;
  clearTimeout(gitTimer);
  gitTimer = setTimeout(refreshGit, 700);
}

async function refreshGit() {
  if (!state.info?.canGit) return;
  try {
    state.git = await state.store.gitStatus();
  } catch {
    state.git = null;
  }
  renderSidebarFoot();
}

// ============================================================ guide (first-run)

const GUIDE_KEY = "cairn.guide";

function guide() {
  try {
    return { captured: false, moved: false, promoted: false, dismissed: false, ...JSON.parse(localStorage.getItem(GUIDE_KEY) || "{}") };
  } catch {
    return { captured: false, moved: false, promoted: false, dismissed: false };
  }
}

function markGuide(step) {
  const current = guide();
  if (current[step]) return;
  current[step] = true;
  localStorage.setItem(GUIDE_KEY, JSON.stringify(current));
  if (current.captured && current.moved && current.promoted) {
    toast("That's the whole loop: capture, brainstorm, act. Everything is plain Markdown in brain/.");
  }
}

// ============================================================ shell rendering

function render() {
  renderNav();
  renderView();
}

function counts() {
  const by = (type, statuses) => state.notes.filter((n) => n.type === type && (!statuses || statuses.includes(n.status))).length;
  return {
    home: "",
    ideas: by("idea", ["raw", "exploring", "ready"]),
    board: by("task", ["todo", "doing"]),
    decisions: by("decision", ["proposed"]) || "",
    notes: state.notes.length,
    journal: by("journal"),
    map: "",
    code: state.todos ? state.todos.length : "",
    context: "",
    people: "",
  };
}

function renderNav() {
  const counted = counts();
  let lastGroup = "";
  const html = [];
  for (const view of VIEWS) {
    if (view.group !== lastGroup) {
      html.push(`<div class="nav-label">${view.group}</div>`);
      lastGroup = view.group;
    }
    const active = state.view === view.id ? " active" : "";
    const count = counted[view.id] === "" ? "" : `<span class="count">${counted[view.id]}</span>`;
    html.push(
      `<button class="${active.trim()}" data-go="${view.id}" ${active ? 'aria-current="page"' : ""}>${icon(view.icon)}<span>${view.label}</span>${count}</button>`,
    );
  }
  $("#nav").innerHTML = html.join("");
}

function renderSidebarFoot() {
  const theme = document.documentElement.dataset.theme;
  const parts = [];
  if (state.info?.canGit && state.git?.available) {
    const changes = state.git.changes.length;
    parts.push(`
      <div class="git-card">
        <div class="row"><span class="branch">${icon("branch", "sm")}${esc(state.git.branch || "git")}</span><span class="muted">${changes ? plural(changes, "change") : "clean"}</span></div>
        <button class="btn sm ${changes ? "primary" : ""}" style="width:100%;margin-top:8px" data-action="commit" ${changes ? "" : "disabled"}>${icon("git", "sm")} Commit brain</button>
      </div>`);
  }
  const me = auth.session();
  parts.push(
    me
      ? `<div class="account-row">
          <img src="${esc(me.avatar)}" alt="" width="22" height="22">
          <span class="who" title="Signed in with GitHub${me.remember ? ", remembered on this device" : " for this tab"}">@${esc(me.login)}</span>
          <button class="btn sm ghost" data-action="sign-out">Sign out</button>
        </div>`
      : `<button class="btn sm account-signin" data-action="sign-in">${icon("github", "sm")} Sign in to edit from any device</button>`,
  );
  parts.push(`
    <div class="foot-row">
      <button class="btn sm ghost" data-action="theme" aria-label="Toggle theme">${icon(theme === "dark" ? "sun" : "moon", "sm")} ${theme === "dark" ? "Light" : "Dark"}</button>
      <a class="btn sm ghost" href="${REPO_URL}" target="_blank" rel="noopener">${icon("github", "sm")} GitHub</a>
    </div>`);
  $("#sidebar-foot").innerHTML = parts.join("");
}

function setView(view, { push = true } = {}) {
  if (!VIEWS.some((v) => v.id === view)) view = "home";
  state.view = view;
  if (push && location.hash !== `#/${view}`) history.pushState(null, "", `#/${view}`);
  $("#app").classList.remove("nav-open");
  render();
  $("#view").scrollTop = 0;
  window.scrollTo(0, 0);
  if (view === "code" && state.todos === null) refreshTodos();
}

function renderView() {
  const renderers = { home, ideas, board, decisions, notes: notesView, journal, map: mapView, code: codeView, context: contextView, people: peopleView };
  const view = $("#view");
  // Background loads (e.g. the discover list) re-render the view; keep whatever the person is typing.
  const typing = $("#people-query");
  const hadFocus = typing && document.activeElement === typing;
  if (typing) state.peopleQuery = typing.value;
  view.innerHTML = readOnlyBanner() + (renderers[state.view] || home)();
  if (hadFocus) {
    const restored = $("#people-query");
    restored?.focus();
    restored?.setSelectionRange(restored.value.length, restored.value.length);
  }
  afterRender();
}

function afterRender() {
  if ((state.view === "ideas" || state.view === "board") && !readOnly()) wireBoard();
  if (state.view === "map") wireMap();
}

// ============================================================ shared pieces

function typePill(type) {
  const meta = TYPE_META[type] || TYPE_META.note;
  return `<span class="pill t-${type}">${icon(meta.icon, "sm")}${meta.label}</span>`;
}

function statusPill(status) {
  if (!status) return "";
  return `<span class="pill status-${esc(status)}">${esc(STATUS_LABEL[status] || status)}</span>`;
}

function tagChips(tags, max = 3) {
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

function snippetOf(note) {
  const prose = note.body
    .split("\n")
    .filter((line) => !/^\s*(\||#|```|---)/.test(line))
    .join("\n");
  return plainText(core.summary(prose));
}

function checklist(note) {
  const all = note.body.match(/^\s*(?:[-*]|\d+\.)\s+\[[ xX]\]/gm) || [];
  const done = note.body.match(/^\s*(?:[-*]|\d+\.)\s+\[[xX]\]/gm) || [];
  return { total: all.length, done: done.length };
}

function rowItem(note, meta = "") {
  return `<button class="row-item" data-open="${esc(note.path)}"><span class="dot t-${note.type}"></span><span class="title">${esc(note.title)}</span><span class="meta">${meta}</span></button>`;
}

function emptyState(iconName, title, text, action = "") {
  return `<div class="empty-state">${icon(iconName)}<h3>${title}</h3><p>${text}</p>${action}</div>`;
}

function pageHead(title, sub, actions = "", eyebrow = "") {
  // Someone else's brain is read-only: no create buttons, so nothing promises an action we would refuse.
  if (readOnly()) actions = "";
  return `<div class="page-head"><div>${eyebrow ? `<p class="eyebrow">${eyebrow}</p>` : ""}<h1>${title}</h1>${sub ? `<p>${sub}</p>` : ""}</div><div class="actions">${actions}</div></div>`;
}

function byRecent(a, b) {
  return (b.mtime || 0) - (a.mtime || 0);
}

// ============================================================ views

function home() {
  const tasks = state.notes.filter((n) => n.type === "task");
  const doing = tasks.filter((n) => n.status === "doing").sort(byRecent);
  const done = tasks.filter((n) => n.status === "done");
  const ideasInPlay = state.notes.filter((n) => n.type === "idea" && ["raw", "exploring", "ready"].includes(n.status));
  const fresh = state.notes.filter((n) => n.type === "idea" && n.status === "raw").sort(byRecent).slice(0, 5);
  const proposed = state.notes.filter((n) => n.type === "decision" && n.status === "proposed");
  const recent = [...state.notes].sort(byRecent).slice(0, 6);
  const hour = new Date().getHours();
  const greeting = readOnly()
    ? `What @${esc(state.info.owner)} is working on`
    : hour < 12 ? "Good morning." : hour < 18 ? "Good afternoon." : "Good evening.";
  const dateLabel = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  const g = guide();
  const showGuide = !readOnly() && !g.dismissed && !(g.captured && g.moved && g.promoted);
  const step = (isDone, n, title, text) =>
    `<li class="guide-step ${isDone ? "done" : ""}"><span class="num">${isDone ? icon("check", "sm") : n}</span><div><strong>${title}</strong><span>${text}</span></div></li>`;
  const guideHtml = showGuide
    ? `<section class="guide" id="guide" aria-label="Getting started">
        <div class="guide-intro"><h2>Three moves to get the hang of it</h2><p>Everything you do here is saved as plain Markdown in <code>brain/</code>.</p></div>
        <ol class="guide-steps">
          ${step(g.captured, 1, "Capture a thought", "Type in the bar at the top, press Enter.")}
          ${step(g.moved, 2, "Move it forward", "On Ideas, drag a card from Raw to Exploring.")}
          ${step(g.promoted, 3, "Turn it into work", "Open an idea, press Promote to task.")}
        </ol>
        <button class="btn ghost sm icon-only guide-close" data-action="dismiss-guide" aria-label="Hide the guide">${icon("x", "sm")}</button>
      </section>`
    : "";

  const todoCount = state.todos === null ? "–" : state.todos.length;
  const figure = (view, value, label) => `<button class="figure" data-go="${view}"><span class="value">${value}</span><span class="label">${label}</span></button>`;
  const emptyBrain = state.notes.length === 0;
  const sampleButton = emptyBrain && state.info.mode === "demo" ? `<button class="btn primary" data-action="load-sample">${icon("play")} Load the sample project</button>` : "";
  const intro = emptyBrain
    ? "An empty brain. Write the first thing on your mind in the bar above."
    : readOnly()
      ? `A read-only look at <strong>${esc(state.info.owner)}/${esc(state.info.repoName)}</strong>.`
      : `Here's where <strong>${esc(state.info.project)}</strong> stands.`;

  const column = (title, view, linkLabel, rows, empty) => `
    <section class="column-block">
      <header><h2>${title}</h2><button class="text-link" data-go="${view}">${linkLabel} ${icon("arrow", "sm")}</button></header>
      ${rows.length ? `<div class="rows">${rows.join("")}</div>` : `<p class="empty">${empty}</p>`}
    </section>`;

  return `
    <header class="today">
      <div class="today-text">
        <p class="eyebrow">${dateLabel}</p>
        <h1>${greeting}</h1>
        <p class="lede">${intro}</p>
        <div class="actions">
          ${readOnly() ? "" : `<button class="btn" data-action="journal-today">${icon("journal")} Today's journal</button>`}
          ${sampleButton}
        </div>
      </div>
      <figure class="cairn">
        ${cairnSvg(done.length)}
        <figcaption><strong>${done.length}</strong> ${done.length === 1 ? "stone" : "stones"}<span>one for every task finished</span></figcaption>
      </figure>
    </header>
    ${guideHtml}
    <div class="figures">
      ${figure("board", doing.length, "in progress")}
      ${figure("ideas", ideasInPlay.length, "ideas in play")}
      ${figure("decisions", proposed.length, "open decisions")}
      ${figure("code", todoCount, "code TODOs")}
    </div>
    <div class="columns-3">
      ${column("Doing now", "board", "Board", doing.map((n) => rowItem(n, checklistMeta(n))), "Nothing in progress. Drag a task to Doing.")}
      ${column("Fresh ideas", "ideas", "Ideas", fresh.map((n) => rowItem(n, relativeTime(n.mtime))), "No raw ideas. Capture one above.")}
      ${column("Needs a decision", "decisions", "Decisions", proposed.map((n) => rowItem(n, "proposed")), "No open questions.")}
    </div>
    ${column("Recently touched", "notes", "All notes", recent.map((n) => rowItem(n, `${TYPE_META[n.type].label}${n.mtime ? " · " + relativeTime(n.mtime) : ""}`)), "Nothing yet.")}`;
}

function checklistMeta(note) {
  const { total, done } = checklist(note);
  return total ? `${done}/${total}` : relativeTime(note.mtime);
}

function itemCard(note) {
  const snippet = snippetOf(note);
  const { total, done } = checklist(note);
  const links = core.extractLinks(note.body).length;
  const source = typeof note.meta.source === "string" ? note.meta.source : "";
  const statuses = core.STATUSES[note.type];
  return `
    <article class="item-card t-${note.type}" draggable="${readOnly() ? "false" : "true"}" data-open="${esc(note.path)}" data-drag="${esc(note.path)}" tabindex="0" aria-label="${esc(note.title)}">
      <div class="title">${esc(note.title)}</div>
      ${snippet ? `<div class="snippet">${esc(snippet)}</div>` : ""}
      ${total ? `<div class="progress" title="${done} of ${total} done"><i style="width:${Math.round((done / total) * 100)}%"></i></div>` : ""}
      <div class="foot">
        ${tagChips(note.tags, 2)}<span class="grow"></span>
        ${source ? `<span class="source-chip" title="${esc(source)}">${icon("code", "sm")}${esc(source.split("/").pop())}</span>` : ""}
        ${links ? `<span class="metric" title="${plural(links, "link")}">${icon("link", "sm")}${links}</span>` : ""}
        ${total ? `<span class="metric">${done}/${total}</span>` : ""}
      </div>
      ${readOnly() ? "" : `<select class="move-select" data-move="${esc(note.path)}" aria-label="Move ${esc(note.title)}">
        ${statuses.map((s) => `<option value="${s}" ${s === note.status ? "selected" : ""}>${STATUS_LABEL[s]}</option>`).join("")}
      </select>`}
    </article>`;
}

function boardColumns(type) {
  const statuses = core.STATUSES[type];
  const items = state.notes.filter((n) => n.type === type);
  return `<div class="board" data-board="${type}">${statuses
    .map((status) => {
      const inColumn = items.filter((n) => n.status === status).sort(byRecent);
      const placeholder = type === "idea" ? "Add an idea…" : "Add a task…";
      return `
        <section class="column" data-column="${status}" aria-label="${STATUS_LABEL[status]}">
          <div class="column-head"><span class="dot status-${status}" style="background:var(--t)"></span><h2>${STATUS_LABEL[status]}</h2><span class="count">${inColumn.length}</span></div>
          ${STATUS_HINT[status] ? `<p class="column-hint">${STATUS_HINT[status]}</p>` : ""}
          ${status !== "done" && status !== "parked" && !readOnly() ? `<form class="quick-add" data-quick="${type}:${status}"><input name="title" placeholder="${placeholder}" aria-label="${placeholder} (${STATUS_LABEL[status]})"></form>` : ""}
          ${inColumn.map(itemCard).join("")}
        </section>`;
    })
    .join("")}</div>`;
}

function ideas() {
  return `${pageHead("Ideas", readOnly()
    ? "What this brain is thinking about, from raw to ready. Open one to read it in full."
    : "Brainstorm here. Capture without judging, then drag ideas right as they firm up. Open one to spark it with a prompt or promote it to a task.",
    `<button class="btn" data-action="spark-random">${icon("zap")} Spark a prompt</button><button class="btn primary" data-action="new" data-type="idea">${icon("plus")} New idea</button>`)}
    ${state.spark && !state.open ? `<div class="spark-card">${icon("zap")}<div><strong>${esc(state.spark)}</strong><br><span class="muted">Pick an idea and answer this in it, or capture what it makes you think of.</span></div></div>` : ""}
    ${boardColumns("idea")}`;
}

function board() {
  return `${pageHead("Board", readOnly()
    ? "What this brain is working on, from to do to done. Open a card to read its checklist."
    : "Everything you are going to do, from to do to done. Drag cards between columns; open one to edit its checklist.",
    `<button class="btn primary" data-action="new" data-type="task">${icon("plus")} New task</button>`)}
    ${boardColumns("task")}`;
}

function listRow(note) {
  const snippet = snippetOf(note);
  return `
    <button class="list-row" data-open="${esc(note.path)}">
      <span class="type-icon t-${note.type}">${icon(TYPE_META[note.type].icon)}</span>
      <span class="main-col"><div class="title">${esc(note.title)}</div>${snippet ? `<div class="snippet">${esc(snippet)}</div>` : ""}</span>
      <span class="tags">${statusPill(note.status)}${note.tags.slice(0, 2).map((t) => `<span class="tag">#${esc(t)}</span>`).join("")}</span>
      <span class="when">${relativeTime(note.mtime)}</span>
    </button>`;
}

function decisions() {
  const all = state.notes.filter((n) => n.type === "decision");
  const groups = core.STATUSES.decision
    .map((status) => {
      const items = all.filter((n) => n.status === status).sort((a, b) => (a.slug < b.slug ? 1 : -1));
      if (!items.length) return "";
      return `<div class="group-title">${statusPill(status)} ${items.length}</div><div class="card list">${items.map(listRow).join("")}</div>`;
    })
    .join("");
  return `${pageHead("Decisions", "A lightweight decision log: context, options, decision, consequences. Reviewed in pull requests like code, so the “why” never gets lost.",
    `<button class="btn primary" data-action="new" data-type="decision">${icon("plus")} New decision</button>`)}
    ${all.length ? groups : `<div class="card">${emptyState("decision", "No decisions yet", "Capture one with “decision: use SQLite” or press New decision. Each one gets a numbered file and a template.")}</div>`}`;
}

function notesView() {
  const filters = [["all", "All"], ["note", "Notes"], ["idea", "Ideas"], ["task", "Tasks"], ["decision", "Decisions"], ["journal", "Journal"]];
  const query = state.notesQuery.trim().toLowerCase();
  const tagCounts = new Map();
  for (const note of state.notes) for (const tag of note.tags) tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
  const topTags = [...tagCounts.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).slice(0, 12);
  const items = state.notes
    .filter((n) => state.notesFilter === "all" || n.type === state.notesFilter)
    .filter((n) => !state.notesTag || n.tags.includes(state.notesTag))
    .filter((n) => !query || n.title.toLowerCase().includes(query) || n.body.toLowerCase().includes(query) || n.tags.some((t) => t.includes(query)))
    .sort(byRecent);
  return `${pageHead("Notes", "Everything in the brain, newest first. Link notes with [[file-name]]; backlinks show up in each note.",
    `<button class="btn primary" data-action="new" data-type="note">${icon("plus")} New note</button>`)}
    <div class="toolbar">
      <div class="segmented" role="tablist" aria-label="Filter by type">${filters.map(([id, label]) => `<button class="${state.notesFilter === id ? "on" : ""}" data-filter="${id}" role="tab" aria-selected="${state.notesFilter === id}">${label}</button>`).join("")}</div>
      <label class="search-field">${icon("search", "sm")}<span class="sr-only">Search notes</span><input class="input" id="notes-search" placeholder="Search titles, text and tags" value="${esc(state.notesQuery)}"></label>
    </div>
    ${topTags.length ? `<div class="tags" style="margin-bottom:14px">${topTags.map(([tag, count]) => `<button class="tag ${state.notesTag === tag ? "on" : ""}" data-tag="${esc(tag)}">#${esc(tag)} <span class="muted">&nbsp;${count}</span></button>`).join("")}</div>` : ""}
    <div class="card list" id="notes-list">${items.length ? items.map(listRow).join("") : emptyState("search", "Nothing matches", "Try another word, or clear the filters.")}</div>`;
}

function journal() {
  const pages = state.notes.filter((n) => n.type === "journal").sort((a, b) => (a.slug < b.slug ? 1 : -1));
  return `${pageHead("Journal", "One page per day. Log progress as you go, or capture “log: fixed the GPX bug” from anywhere to add a timestamped line to today’s page.",
    `<button class="btn primary" data-action="journal-today">${icon("journal")} Open today</button>`)}
    <div class="card list">${pages.length ? pages.map(listRow).join("") : emptyState("journal", "No journal pages yet", "Open today’s page to start. It’s just a Markdown file named after the date.")}</div>`;
}

// ------------------------------------------------------------ map

function layoutGraph(graph) {
  const n = graph.nodes.length;
  const pos = new Map();
  graph.nodes.forEach((node, index) => {
    const angle = (index / Math.max(n, 1)) * Math.PI * 2;
    const radius = 160 + (index % 3) * 40;
    pos.set(node.id, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, vx: 0, vy: 0 });
  });
  const edges = graph.edges.map((e) => [pos.get(e.source), pos.get(e.target)]);
  const points = [...pos.values()];
  for (let step = 0; step < 360; step++) {
    const cooling = 1 - step / 360;
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const a = points[i];
        const b = points[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let dist2 = dx * dx + dy * dy;
        if (dist2 < 0.01) {
          dx = (i - j) * 0.1;
          dy = 0.1;
          dist2 = 0.02;
        }
        const force = 9000 / dist2;
        const dist = Math.sqrt(dist2);
        a.vx += (dx / dist) * force;
        a.vy += (dy / dist) * force;
        b.vx -= (dx / dist) * force;
        b.vy -= (dy / dist) * force;
      }
    }
    for (const [a, b] of edges) {
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = (dist - 130) * 0.02;
      a.vx += (dx / dist) * force;
      a.vy += (dy / dist) * force;
      b.vx -= (dx / dist) * force;
      b.vy -= (dy / dist) * force;
    }
    for (const p of points) {
      p.vx -= p.x * 0.008;
      p.vy -= p.y * 0.008;
      p.x += Math.max(-12, Math.min(12, p.vx)) * cooling;
      p.y += Math.max(-12, Math.min(12, p.vy)) * cooling;
      p.vx *= 0.5;
      p.vy *= 0.5;
    }
  }
  return pos;
}

let mapState = null;

function mapView() {
  const graph = core.buildGraph(state.notes);
  if (!graph.nodes.length) {
    return `${pageHead("Map", "How your ideas, tasks, decisions and notes connect.")}<div class="card">${emptyState("map", "Nothing to map yet", "Notes appear here once you have some. Link them with [[file-name]] to draw connections.")}</div>`;
  }
  const pos = layoutGraph(graph);
  const degree = new Map(graph.nodes.map((n) => [n.id, 0]));
  for (const e of graph.edges) {
    degree.set(e.source, degree.get(e.source) + 1);
    degree.set(e.target, degree.get(e.target) + 1);
  }
  const xs = [...pos.values()].map((p) => p.x);
  const ys = [...pos.values()].map((p) => p.y);
  const pad = 90;
  const box = { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + pad * 2, h: Math.max(...ys) - Math.min(...ys) + pad * 2 };
  mapState = { box: { ...box }, edges: graph.edges };
  const edgeSvg = graph.edges
    .map((e) => {
      const a = pos.get(e.source);
      const b = pos.get(e.target);
      return `<line class="map-edge" data-a="${esc(e.source)}" data-b="${esc(e.target)}" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"/>`;
    })
    .join("");
  const nodeSvg = graph.nodes
    .map((node) => {
      const p = pos.get(node.id);
      const r = 7 + Math.sqrt(degree.get(node.id)) * 3;
      const label = node.title.length > 28 ? node.title.slice(0, 27) + "…" : node.title;
      return `<g class="map-node" data-open="${esc(node.id)}" data-node="${esc(node.id)}" transform="translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})" tabindex="0" role="button" aria-label="${esc(node.title)}">
        <circle r="${r.toFixed(1)}" fill="var(--${node.type})"/><text y="${(r + 14).toFixed(1)}" text-anchor="middle">${esc(label)}</text></g>`;
    })
    .join("");
  const legend = core.TYPES.map((t) => `<span><i class="dot t-${t}"></i>${TYPE_META[t].label}</span>`).join("");
  return `${pageHead("Map", `${plural(graph.nodes.length, "note")} and ${plural(graph.edges.length, "link")}. Hover to see neighbours, scroll to zoom, drag to pan, click to open.`,
    `<button class="btn" data-action="map-reset">${icon("refresh")} Reset view</button>`)}
    <div class="card map-wrap" id="map-wrap">
      <svg id="map-svg" viewBox="${box.x} ${box.y} ${box.w} ${box.h}" aria-label="Note map">${edgeSvg}${nodeSvg}</svg>
      <div class="map-legend">${legend}</div>
    </div>`;
}

function wireMap() {
  const svg = $("#map-svg");
  const wrap = $("#map-wrap");
  if (!svg || !mapState) return;
  const setBox = () => svg.setAttribute("viewBox", `${mapState.box.x} ${mapState.box.y} ${mapState.box.w} ${mapState.box.h}`);
  $$(".map-node", svg).forEach((node) => {
    node.addEventListener("mouseenter", () => {
      const id = node.dataset.node;
      wrap.classList.add("focus");
      node.classList.add("lit");
      $$(".map-edge", svg).forEach((edge) => {
        if (edge.dataset.a === id || edge.dataset.b === id) {
          edge.classList.add("lit");
          const other = edge.dataset.a === id ? edge.dataset.b : edge.dataset.a;
          $$(".map-node", svg).find((n) => n.dataset.node === other)?.classList.add("lit");
        }
      });
    });
    node.addEventListener("mouseleave", () => {
      wrap.classList.remove("focus");
      $$(".lit", svg).forEach((el) => el.classList.remove("lit"));
    });
  });
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = svg.getBoundingClientRect();
    const scale = event.deltaY > 0 ? 1.12 : 1 / 1.12;
    const mx = mapState.box.x + ((event.clientX - rect.left) / rect.width) * mapState.box.w;
    const my = mapState.box.y + ((event.clientY - rect.top) / rect.height) * mapState.box.h;
    mapState.box.w *= scale;
    mapState.box.h *= scale;
    mapState.box.x = mx - (mx - mapState.box.x) * scale;
    mapState.box.y = my - (my - mapState.box.y) * scale;
    setBox();
  }, { passive: false });
  let drag = null;
  svg.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".map-node")) return;
    drag = { x: event.clientX, y: event.clientY, box: { ...mapState.box } };
    svg.classList.add("panning");
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const rect = svg.getBoundingClientRect();
    mapState.box.x = drag.box.x - ((event.clientX - drag.x) / rect.width) * drag.box.w;
    mapState.box.y = drag.box.y - ((event.clientY - drag.y) / rect.height) * drag.box.h;
    setBox();
  });
  const end = () => {
    drag = null;
    svg.classList.remove("panning");
  };
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);
}

// ------------------------------------------------------------ code inbox

function sourceKey(item) {
  return `${item.path}:${item.line}`;
}

function codeView() {
  const refresh = `<button class="btn" data-action="refresh-todos">${icon("refresh")} Rescan</button>`;
  const head = pageHead("Code inbox", "TODO, FIXME and HACK comments found in the code around your brain. Turn the ones that matter into tasks; each task remembers its file and line.", refresh);
  if (state.todos === null) return `${head}<div class="card">${emptyState("code", "Scanning your code…", "Looking for TODO, FIXME and HACK comments.")}</div>`;
  if (state.todosError) return `${head}<div class="card">${emptyState("code", "Couldn’t scan the code", esc(state.todosError))}</div>`;
  if (!state.todos.length) {
    const hint = state.info.mode === "folder" && !state.info.label.includes("brain/") ? "You opened the brain folder itself. Open the repo root instead to scan its code." : "No TODO, FIXME or HACK comments found. Nice and tidy.";
    return `${head}<div class="card">${emptyState("check", "Inbox zero", hint)}</div>`;
  }
  const tracked = new Set(state.notes.map((n) => (typeof n.meta.source === "string" ? n.meta.source : "")).filter(Boolean));
  const groups = new Map();
  for (const item of state.todos) {
    if (!groups.has(item.path)) groups.set(item.path, []);
    groups.get(item.path).push(item);
  }
  const html = [...groups.entries()]
    .map(([path, items]) => `
      <section class="card file-group">
        <div class="file-head">${icon("code", "sm")}${esc(path)}<span class="muted" style="margin-left:auto">${plural(items.length, "item")}</span></div>
        ${items
          .map((item) => {
            const key = sourceKey(item);
            const action = tracked.has(key)
              ? `<span class="tracked">${icon("check", "sm")} Tracked</span>`
              : `<button class="btn sm" data-action="todo-task" data-source="${esc(key)}">${icon("plus", "sm")} Make task</button>`;
            return `<div class="todo-row"><span class="pill tag-${esc(item.tag)}">${esc(item.tag)}</span><span>${esc(item.text)}</span><span class="line">line ${item.line}</span>${action}</div>`;
          })
          .join("")}
      </section>`)
    .join("");
  return `${head}${html}`;
}

// ------------------------------------------------------------ context pack

function contextView() {
  const pack = core.contextPack(state.notes, state.info.project, today());
  const existing = fileByPath("CONTEXT.md");
  const saved = existing ? `Last saved ${relativeTime(existing.mtime)}${existing.text === pack ? " · up to date" : " · out of date"}` : "Not saved yet";
  const body = state.contextMode === "preview"
    ? `<div class="prose" style="padding:22px 26px">${renderMarkdown(pack, (t) => core.resolveLink(state.notes, t))}</div>`
    : `<pre class="pre-box">${esc(pack)}</pre>`;
  return `${pageHead("Context pack", "A one-page brief of where the project stands, for teammates and AI assistants (Copilot, Claude, Cursor, ChatGPT…). No API keys: it’s just a file your tools can read.",
    `<button class="btn" data-action="copy-context">${icon("copy")} Copy</button><button class="btn primary" data-action="save-context">${icon("save")} Save to brain/CONTEXT.md</button>`)}
    <div class="callout" style="margin-bottom:16px">${icon("sparkle")}<div>Tell your assistant: <strong>“Read brain/CONTEXT.md before you start.”</strong> Or add that line to <code>AGENTS.md</code>, <code>CLAUDE.md</code> or <code>.github/copilot-instructions.md</code> so it happens every time. Regenerate after big changes; <code>cairn context</code> does the same from the terminal.</div></div>
    <div class="toolbar"><div class="segmented" role="tablist" aria-label="View"><button class="${state.contextMode === "preview" ? "on" : ""}" data-context-mode="preview">Preview</button><button class="${state.contextMode === "raw" ? "on" : ""}" data-context-mode="raw">Markdown</button></div><span class="muted" style="margin-left:auto;font-size:12.5px">${saved} · ${pack.split("\n").length} lines</span></div>
    <section class="card">${body}</section>`;
}


// ============================================================ network: profiles, discover, read-only brains

function readOnlyBanner() {
  if (!readOnly()) return "";
  const { owner, repoName, url, avatar } = state.info;
  return `
    <div class="ro-banner" role="note">
      ${avatar ? `<img class="ro-avatar" src="${esc(avatar)}" alt="" width="28" height="28">` : ""}
      <div class="ro-text"><strong>@${esc(owner)}'s brain</strong><span>${esc(owner)}/${esc(repoName)} · ${icon("lock", "sm")} read-only</span></div>
      <div class="ro-actions">
        <button class="btn sm ghost" data-action="open-profile" data-login="${esc(owner)}">Profile</button>
        <a class="btn sm ghost" href="${esc(url)}" target="_blank" rel="noopener">${icon("github", "sm")} Repo</a>
        <a class="btn sm primary" href="${esc(gh.joinRequestUrl(owner, repoName))}" target="_blank" rel="noopener">Ask to join</a>
        <button class="btn sm" data-action="leave-brain">Back to my brain</button>
      </div>
    </div>`;
}

function brainNotes(files) {
  return files.filter((f) => !core.SPECIAL_FILES.includes(f.path)).map((f) => core.noteFromFile(f.path, f.text, f.mtime));
}

function proofOfWork(notes) {
  const pick = (type, statuses) => notes.filter((n) => n.type === type && statuses.includes(n.status));
  return {
    now: pick("task", ["doing"]),
    shipped: pick("task", ["done"]),
    decisions: pick("decision", ["accepted"]),
    open: notes.filter((n) => n.type === "idea" && (n.status === "ready" || n.tags.includes("help-wanted"))),
    ideas: pick("idea", ["raw", "exploring", "ready"]).length,
  };
}

async function lookupProfile(raw) {
  const parsed = gh.parseGithubInput(raw);
  state.peopleQuery = String(raw || "").trim();
  if (!parsed) {
    state.profile = { error: "That doesn't look like a GitHub username or link." };
    return setView("people");
  }
  if (parsed.repo) return openBrain(`${parsed.owner}/${parsed.repo}`);
  state.profile = { loading: true, login: parsed.owner };
  setView("people");
  try {
    const [user, brains] = await Promise.all([gh.fetchUser(parsed.owner), gh.findBrains(parsed.owner)]);
    state.profile = { user, brains, proof: null };
    renderView();
    if (brains.length) {
      const loaded = await gh.loadBrainFiles(brains[0].owner, brains[0].name, brains[0].branch);
      state.profile.proof = proofOfWork(brainNotes(loaded.files));
      state.profile.proofRepo = brains[0];
    }
  } catch (error) {
    state.profile = { ...(state.profile || {}), loading: false, error: error.message };
  }
  if (state.view === "people") renderView();
}

async function loadDiscover() {
  if (state.discover) return;
  state.discover = { loading: true, items: [] };
  try {
    state.discover = { items: await gh.discoverBrains() };
  } catch (error) {
    state.discover = { items: [], error: error.message };
  }
  if (state.view === "people") renderView();
}

function openBrain(fullName) {
  location.href = `${location.pathname}?brain=${encodeURIComponent(fullName)}`;
}

function proofList(title, items, empty, meta = () => "") {
  return `<section class="proof-col"><h3>${title} <span class="muted">${items.length}</span></h3>${
    items.length
      ? `<ul>${items.slice(0, 5).map((n) => `<li><span class="dot t-${n.type}"></span>${esc(n.title)}${meta(n)}</li>`).join("")}</ul>`
      : `<p class="muted">${empty}</p>`
  }</section>`;
}

function profileBlock() {
  const p = state.profile;
  if (!p) return "";
  if (p.loading && !p.user) return `<div class="profile-card skeleton"><div class="avatar-lg"></div><div><h2>Looking up @${esc(p.login)}…</h2></div></div>`;
  if (p.error && !p.user) return `<div class="notice">${icon("search")}<div>${esc(p.error)}</div></div>`;
  const { user, brains, proof, proofRepo } = p;
  const facts = [user.location, user.since ? `on GitHub since ${user.since}` : ""].filter(Boolean).map(esc).join(" · ");
  const first = brains[0];
  return `
    <article class="profile-card">
      <img class="avatar-lg" src="${esc(user.avatar)}" alt="" width="96" height="96">
      <div class="profile-main">
        <h2>${esc(user.name)}</h2>
        <p class="handle">@${esc(user.login)}${facts ? ` · ${facts}` : ""}</p>
        ${user.bio ? `<p class="bio">${esc(user.bio)}</p>` : ""}
        <div class="actions">
          ${first ? `<button class="btn primary" data-action="open-brain" data-repo="${esc(first.fullName)}">Open brain</button>` : ""}
          ${first ? `<a class="btn" href="${esc(gh.joinRequestUrl(first.owner, first.name))}" target="_blank" rel="noopener">Ask to join</a>` : ""}
          <a class="btn ghost" href="${esc(user.url)}" target="_blank" rel="noopener">${icon("github", "sm")} GitHub</a>
          ${user.blog ? `<a class="btn ghost" href="${esc(user.blog.startsWith("http") ? user.blog : "https://" + user.blog)}" target="_blank" rel="noopener">${icon("external", "sm")} Website</a>` : ""}
        </div>
      </div>
    </article>
    ${
      proof
        ? `<div class="proof">
            <p class="proof-note">Proof of work from <strong>${esc(proofRepo.fullName)}</strong>. Read from their files, not self-reported.</p>
            <div class="proof-grid">
              ${proofList("Working on", proof.now, "Nothing in progress.")}
              ${proofList("Shipped", proof.shipped, "Nothing shipped yet.")}
              ${proofList("Decisions", proof.decisions, "No decisions recorded.")}
              ${proofList("Open to collaborate", proof.open, "No ideas flagged ready or #help-wanted.")}
            </div>
          </div>`
        : brains.length
          ? `<p class="muted">Reading their brain…</p>`
          : `<div class="notice">${icon("lock")}<div><strong>@${esc(user.login)} hasn't published a brain yet.</strong><br>A brain becomes visible here when its repo has the <code>${gh.BRAIN_TOPIC}</code> topic. Nothing private is ever shown.</div></div>`
    }
    ${
      brains.length
        ? `<h3 class="section-title">Published brains</h3><div class="repo-list">${brains.map(repoRow).join("")}</div>`
        : ""
    }`;
}

function repoRow(repo) {
  const updated = repo.pushedAt ? relativeTime(Date.parse(repo.pushedAt)) : "";
  return `
    <div class="repo-row">
      ${repo.avatar ? `<img src="${esc(repo.avatar)}" alt="" width="36" height="36" loading="lazy">` : ""}
      <div class="repo-main">
        <button class="link-button" data-action="open-brain" data-repo="${esc(repo.fullName)}">${esc(repo.fullName)}</button>
        <p>${esc(repo.description || "No description.")}</p>
      </div>
      <span class="muted repo-meta">${updated}</span>
      <div class="repo-actions">
        <button class="btn sm ghost" data-action="open-profile" data-login="${esc(repo.owner)}">Profile</button>
        <button class="btn sm" data-action="open-brain" data-repo="${esc(repo.fullName)}">Open</button>
      </div>
    </div>`;
}

// ------------------------------------------------------------ sign in

function openSignIn() {
  if ($("#signin-dialog")) return;
  const deviceReady = Boolean(auth.authUrl());
  const wrap = document.createElement("div");
  wrap.className = "modal-wrap";
  wrap.id = "signin-dialog";
  wrap.innerHTML = `
    <div class="modal signin" role="dialog" aria-modal="true" aria-labelledby="signin-title">
      <h2 id="signin-title">Sign in with GitHub</h2>
      <p>Edit your brain from any device. Your notes stay in your own GitHub repo; Cairn has no server and no database.</p>
      <ul class="signin-facts">
        <li><strong>Access:</strong> read and write your <em>public</em> repos (GitHub calls this <code>public_repo</code>). Cairn only touches <code>brain/</code> folders.</li>
        <li><strong>Where it lives:</strong> in this browser only, sent only to <code>api.github.com</code>.</li>
        <li><strong>Undo:</strong> sign out any time, or revoke access on GitHub.</li>
      </ul>
      ${deviceReady ? `
        <button type="button" class="btn primary wide" data-signin="device">${icon("github", "sm")} Continue with GitHub</button>
        <p class="hint">You approve on github.com. The sign-in passes once through Cairn's tiny relay, which keeps nothing; after that Cairn talks only to GitHub.</p>
        <div class="device-step" id="device-step" hidden></div>
        <div class="or"><span>or paste a token</span></div>` : ""}
      <form id="token-form" class="token-form" autocomplete="off">
        <label for="token-input">Personal access token</label>
        <input id="token-input" class="input" type="password" spellcheck="false" placeholder="github_pat_… or ghp_…">
        <p class="hint">Make one on GitHub: a <a href="${auth.TOKEN_LINKS.fineGrained}" target="_blank" rel="noopener">fine-grained token</a> is the narrowest (choose only your brain repo, Contents: Read and write). A <a href="${auth.TOKEN_LINKS.classic}" target="_blank" rel="noopener">classic token with public_repo</a> also lets you create new brains from here.</p>
        <label class="check"><input type="checkbox" id="signin-remember"> Remember on this device</label>
        <p class="hint" id="remember-hint">Off: forgotten when you close this tab. Turn it on only on your own device.</p>
        <p class="form-error" id="signin-error" role="alert" hidden></p>
        <div class="actions">
          <button type="button" class="btn ghost" data-cancel>Cancel</button>
          <button type="submit" class="btn ${deviceReady ? "" : "primary"}" id="token-submit">Sign in with token</button>
        </div>
      </form>
    </div>`;
  document.body.appendChild(wrap);
  const controller = new AbortController();
  const close = () => {
    controller.abort();
    wrap.remove();
  };
  const showError = (message) => {
    const el = $("#signin-error", wrap);
    el.textContent = message;
    el.hidden = !message;
  };
  const remember = () => $("#signin-remember", wrap).checked;
  $("#signin-remember", wrap).addEventListener("change", (e) => {
    $("#remember-hint", wrap).textContent = e.target.checked
      ? "On: kept in this browser until you sign out. Use only on your own device."
      : "Off: forgotten when you close this tab. Turn it on only on your own device.";
  });
  wrap.addEventListener("click", (e) => {
    if (e.target === wrap || e.target.closest("[data-cancel]")) close();
  });
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
    }
  });
  $("#token-form", wrap).addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("#token-submit", wrap);
    button.disabled = true;
    button.textContent = "Checking…";
    showError("");
    try {
      const me = await auth.signInWithToken($("#token-input", wrap).value, { remember: remember() });
      close();
      afterSignIn(me);
    } catch (error) {
      showError(error.message);
      button.disabled = false;
      button.textContent = "Sign in with token";
    }
  });
  $("[data-signin=device]", wrap)?.addEventListener("click", async (e) => {
    const step = $("#device-step", wrap);
    e.currentTarget.disabled = true;
    showError("");
    try {
      const flow = await auth.startDeviceFlow();
      step.hidden = false;
      step.innerHTML = `
        <p>Enter this code on GitHub:</p>
        <div class="device-code" id="device-code">${esc(flow.userCode)}</div>
        <a class="btn primary wide" id="device-open" href="${esc(flow.verificationUri)}" target="_blank" rel="noopener">${icon("copy", "sm")} Copy code and open GitHub</a>
        <p class="muted waiting">Waiting for you to approve on GitHub…</p>`;
      $("#device-open", wrap).addEventListener("click", () => navigator.clipboard?.writeText(flow.userCode).catch(() => {}));
      const me = await auth.finishDeviceFlow(flow, { remember: remember(), signal: controller.signal, pollMs: window.CAIRN_TEST_POLL_MS });
      close();
      afterSignIn(me);
    } catch (error) {
      if (controller.signal.aborted) return;
      showError(error.message);
      step.hidden = true;
      e.currentTarget.disabled = false;
    }
  });
  setTimeout(() => (deviceReady ? $("[data-signin=device]", wrap) : $("#token-input", wrap))?.focus(), 0);
}

function afterSignIn(me) {
  toast(`Signed in as @${me.login}`);
  state.mine = null;
  // A brain you were reading may now be editable; otherwise show your brains.
  if (state.info?.mode === "github") return location.reload();
  location.href = `${location.pathname}?u=${encodeURIComponent(me.login)}`;
}

async function afterSignOut() {
  state.mine = null;
  if (state.store instanceof GitHubStore && state.store.writable) {
    // Keep what's on screen, but stop offering edits that can't be saved.
    await flushPending().catch(() => {});
    state.store.writable = false;
    state.info = await state.store.info();
    applyInfo();
    if (state.open) renderDrawer();
    render();
    return;
  }
  renderSidebarFoot();
  if (state.view === "people") renderView();
}

async function loadMine() {
  if (state.mine || !auth.signedIn()) return;
  state.mine = { loading: true, items: [] };
  try {
    state.mine = { items: await gh.myBrains() };
  } catch (error) {
    state.mine = { items: [], error: error.message };
  }
  if (state.view === "people") renderView();
}

function myBrainsBlock() {
  const me = auth.session();
  if (!me) {
    return `
      <section class="signin-cta">
        <div><h3>Edit your own brain from any device</h3><p>Sign in with GitHub. Your notes stay in your repo; each save is a commit.</p></div>
        <button class="btn primary" data-action="sign-in">${icon("github", "sm")} Sign in</button>
      </section>`;
  }
  loadMine();
  const mine = state.mine;
  const list = !mine || mine.loading
    ? `<p class="muted">Looking for your brains…</p>`
    : mine.error
      ? `<div class="notice">${icon("search")}<div>${esc(mine.error)}</div></div>`
      : mine.items.length
        ? `<div class="repo-list">${mine.items.map(repoRow).join("")}</div>`
        : `<p class="muted">No brains yet. A brain is a repo with the <code>${gh.BRAIN_TOPIC}</code> topic. Create one below.</p>`;
  return `
    <section class="my-brains">
      <h3 class="section-title">Your brains</h3>
      ${list}
      <form class="create-brain" id="create-brain-form" autocomplete="off">
        <label for="new-brain-name">New brain</label>
        <span class="prefix">github.com/${esc(me.login)}/</span>
        <input id="new-brain-name" class="input" value="${mine?.items?.length ? "" : "brain"}" placeholder="repo-name" spellcheck="false">
        <button class="btn" type="submit" data-action="create-brain">${icon("plus", "sm")} Create</button>
      </form>
      <p class="hint">Creates a public repo with the <code>${gh.BRAIN_TOPIC}</code> topic and a starter idea and task. Anyone can read it; only you can edit it.</p>
    </section>`;
}

async function createMyBrain() {
  const input = $("#new-brain-name");
  const name = (input?.value || "").trim();
  if (!/^[A-Za-z0-9._-]{1,100}$/.test(name)) {
    toast("Use letters, numbers, dots, dashes or underscores for the repo name.", { error: true });
    input?.focus();
    return;
  }
  const button = $("#create-brain-form button");
  if (button) {
    button.disabled = true;
    button.textContent = "Creating…";
  }
  try {
    const repo = await gh.createBrain(name, "My Cairn brain: ideas, tasks and decisions in plain Markdown.", today());
    toast(`Created ${repo.fullName}`);
    openBrain(repo.fullName);
  } catch (error) {
    const message = error.conflict
      ? `You already have a repo called "${name}". Pick another name.`
      : /refused/.test(error.message || "")
        ? "Your token can't create repos. Use a classic token with public_repo, or create the repo on GitHub and add the cairn-brain topic."
        : error.message;
    toast(message || "Couldn't create the brain.", { error: true });
    if (button) {
      button.disabled = false;
      button.innerHTML = `${icon("plus", "sm")} Create`;
    }
  }
}

function peopleView() {
  loadDiscover();
  const d = state.discover;
  // Don't repeat the looked-up person's own brains in the "others" list.
  const profileLogin = state.profile?.user?.login?.toLowerCase();
  const others = d?.items?.filter((repo) => repo.owner.toLowerCase() !== profileLogin) || [];
  const discoverHtml = !d || d.loading
    ? `<p class="muted">Loading published brains…</p>`
    : d.error
      ? `<div class="notice">${icon("search")}<div>${esc(d.error)}</div></div>`
      : others.length
        ? `<div class="repo-list">${others.map(repoRow).join("")}</div>`
        : `<p class="muted">${profileLogin ? "No one else has published a brain yet." : "No published brains yet. Be the first."}</p>`;
  const publishHint = state.info.mode === "server" || state.info.mode === "folder"
    ? `<section class="publish"><h3>Publish your brain</h3><p>Push your repo to GitHub as public and add one topic. Only repos you mark this way ever appear here.</p><code>gh repo edit OWNER/${esc(state.info.project)} --add-topic ${gh.BRAIN_TOPIC}</code></section>`
    : `<section class="publish"><h3>Publish your brain</h3><p>Keep your <code>brain/</code> folder in a public GitHub repo and add the <code>${gh.BRAIN_TOPIC}</code> topic. Only repos you mark this way ever appear here.</p></section>`;
  return `
    ${pageHead("People", "Look up anyone on GitHub and see what they're thinking about, building and deciding. Their brain is plain files in their repo, shown read-only.")}
    <form class="people-search" id="people-form" autocomplete="off">
      <label for="people-query" class="sr-only">GitHub username or link</label>
      ${icon("search")}
      <input id="people-query" name="query" placeholder="Paste a GitHub profile or repo link, or type a username" value="${esc(state.peopleQuery)}">
      <button class="btn primary" type="submit">Look up</button>
    </form>
    ${myBrainsBlock()}
    ${profileBlock()}
    <h3 class="section-title">Recently published by others</h3>
    ${discoverHtml}
    ${publishHint}`;
}

// ------------------------------------------------------------ the cairn: one stone per finished task

function cairnSvg(count) {
  const shown = Math.min(count, 8);
  const tones = ["#c9bfac", "#b7ab95", "#d3cab9", "#a99c84", "#c2b6a1", "#9f927b", "#cdc3b1", "#b2a58e"];
  let y = 156;
  const stones = [];
  for (let i = 0; i < shown; i++) {
    const width = 112 - i * 11;
    const height = 30 - i * 2;
    const cx = 80 + (((i * 37) % 13) - 6);
    const tilt = ((i * 53) % 11) - 5;
    y -= height * 0.8;
    const cy = y.toFixed(1);
    stones.push(
      `<g transform="rotate(${tilt} ${cx} ${cy})">` +
        `<ellipse cx="${cx}" cy="${cy}" rx="${width / 2}" ry="${height / 2}" fill="${tones[i]}" stroke="var(--ink)" stroke-opacity=".28"/>` +
        `<ellipse cx="${cx - width * 0.12}" cy="${(y - height * 0.18).toFixed(1)}" rx="${width * 0.26}" ry="${height * 0.16}" fill="#fff" fill-opacity=".28"/>` +
      `</g>`,
    );
  }
  const nextWidth = 112 - shown * 11;
  const ghost = shown < 8
    ? `<ellipse cx="80" cy="${(y - (30 - shown * 2) * 0.8).toFixed(1)}" rx="${nextWidth / 2}" ry="${(30 - shown * 2) / 2}" fill="none" stroke="var(--accent)" stroke-dasharray="3 4" stroke-opacity=".8"/>`
    : "";
  return `<svg class="cairn-art" viewBox="0 0 160 168" aria-hidden="true"><line x1="6" y1="160" x2="154" y2="160" stroke="var(--line-strong)"/>${stones.join("")}${ghost}</svg>`;
}

// ============================================================ boards: drag and drop

function wireBoard() {
  let dragging = null;
  $$(".item-card[data-drag]").forEach((card) => {
    card.addEventListener("dragstart", (event) => {
      dragging = card.dataset.drag;
      event.dataTransfer.setData("text/plain", dragging);
      event.dataTransfer.effectAllowed = "move";
      requestAnimationFrame(() => card.classList.add("dragging"));
    });
    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      $$(".column.drop").forEach((c) => c.classList.remove("drop"));
    });
  });
  $$(".column[data-column]").forEach((column) => {
    column.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      column.classList.add("drop");
    });
    column.addEventListener("dragleave", (event) => {
      if (!column.contains(event.relatedTarget)) column.classList.remove("drop");
    });
    column.addEventListener("drop", (event) => {
      event.preventDefault();
      column.classList.remove("drop");
      const path = event.dataTransfer.getData("text/plain") || dragging;
      if (path) moveNote(path, column.dataset.column);
    });
  });
}

async function moveNote(path, status) {
  const note = noteByPath(path);
  if (!note || note.status === status) return;
  await saveNote(path, { status });
  if (note.type === "idea") markGuide("moved");
  render();
  if (note.type === "task" && status === "done") {
    // The one celebrated moment: finishing work adds a stone to the cairn on Today.
    const stones = state.notes.filter((n) => n.type === "task" && n.status === "done").length;
    toast(`Done: “${note.title}”. That's ${stones} ${stones === 1 ? "stone" : "stones"} on the cairn.`);
    return;
  }
  toast(`“${note.title}” → ${STATUS_LABEL[status] || status}`);
}

// ============================================================ editor drawer

let pending = null;
let pendingTimer = null;

function openNote(path) {
  if (!noteByPath(path)) return;
  flushPending();
  state.open = path;
  renderDrawer();
}

function closeDrawer() {
  flushPending();
  state.open = null;
  state.spark = null;
  $("#drawer-layer")?.remove();
}

function queueChange(changes) {
  if (!state.open) return;
  if (!pending || pending.path !== state.open) pending = { path: state.open, changes: {} };
  Object.assign(pending.changes, changes);
  setSaveState("Saving…", true);
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(flushPending, state.info?.saveDelay || 450);
}

function flushPending() {
  clearTimeout(pendingTimer);
  if (!pending) return Promise.resolve();
  const { path, changes } = pending;
  pending = null;
  return saveNote(path, changes)
    .then((saved) => {
      if (saved) setSaveState(state.info?.mode === "github" ? "Saved to GitHub" : "Saved");
      renderView();
    })
    .catch(() => setSaveState("Not saved"));
}

function setSaveState(text, saving = false) {
  const el = $("#save-state");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("saving", saving);
}

function heldBanner(note) {
  const held = state.held;
  if (!held || held.path !== note.path) return "";
  const lead = held.kind === "conflict"
    ? "<strong>This note changed somewhere else.</strong> You're looking at the newer version. Your edit is kept here."
    : `<strong>Not saved.</strong> ${esc(held.message || "")} Your text is kept here.`;
  return `
    <div class="held-card" role="alert">
      <p>${lead}</p>
      <details><summary>Show your version</summary><pre>${esc(held.text)}</pre></details>
      <div class="held-actions">
        ${readOnly() ? "" : `<button class="btn sm primary" data-action="held-use">${held.kind === "conflict" ? "Use my version" : "Try again"}</button>`}
        <button class="btn sm" data-action="held-copy">${icon("copy", "sm")} Copy my text</button>
        <button class="btn sm ghost" data-action="held-discard">${held.kind === "conflict" ? "Keep the newer one" : "Discard"}</button>
      </div>
    </div>`;
}

async function useHeld() {
  const held = state.held;
  if (!held) return;
  const file = fileByPath(held.path);
  const { mtime } = await state.store.write(held.path, held.text, file ? file.mtime : null);
  state.held = null;
  upsertFile(held.path, held.text, mtime);
  if (state.open === held.path) renderDrawer();
  render();
  toast(state.info?.mode === "github" ? "Saved your version to GitHub" : "Saved your version");
}

function renderDrawer() {
  $("#drawer-layer")?.remove();
  const note = noteByPath(state.open);
  if (!note) return;
  const statuses = core.STATUSES[note.type];
  const backs = core.backlinks(state.notes, note.path).map(noteByPath).filter(Boolean);
  const created = typeof note.meta.created === "string" ? note.meta.created : "";
  const source = typeof note.meta.source === "string" ? note.meta.source : "";
  const isIdea = note.type === "idea" && !readOnly();
  if (readOnly()) state.editorMode = "preview";
  const lockedAttr = readOnly() ? "disabled" : "";
  const layer = document.createElement("div");
  layer.id = "drawer-layer";
  layer.innerHTML = `
    <div class="scrim" data-action="close-drawer"></div>
    <aside class="drawer" role="dialog" aria-modal="true" aria-label="Edit ${esc(note.title)}">
      <div class="drawer-head">
        ${typePill(note.type)}
        <span class="path" title="brain/${esc(note.path)}">brain/${esc(note.path)}</span>
        <span class="save-state" id="save-state">${readOnly() ? "Read-only" : "Saved"}</span>
        <button class="btn ghost sm icon-only" data-action="close-drawer" aria-label="Close">${icon("x")}</button>
      </div>
      <div class="drawer-body">
        ${heldBanner(note)}
        ${state.spark && isIdea ? `<div class="spark-card">${icon("zap")}<div><strong>${esc(state.spark)}</strong><br><span class="muted">Added to the note below. Answer it in a line or two.</span></div></div>` : ""}
        <input class="title-input" id="ed-title" value="${esc(note.title)}" aria-label="Title" placeholder="Untitled" ${lockedAttr}>
        <div class="props">
          ${statuses.length ? `<label for="ed-status">Status</label><div><select id="ed-status" class="select" ${lockedAttr}>${statuses.map((s) => `<option value="${s}" ${s === note.status ? "selected" : ""}>${STATUS_LABEL[s]}</option>`).join("")}</select></div>` : ""}
          <label for="ed-tags">Tags</label><input id="ed-tags" class="input" value="${esc(note.tags.join(", "))}" placeholder="comma, separated" ${lockedAttr}>
          ${source ? `<label>Code</label><div><span class="source-chip">${icon("code", "sm")}${esc(source)}</span></div>` : ""}
          ${created ? `<label>Created</label><span class="muted">${esc(created)}</span>` : ""}
        </div>
        <div class="editor-tabs" ${readOnly() ? "hidden" : ""}>
          <div class="segmented" role="tablist" aria-label="Editor mode">
            <button class="${state.editorMode === "write" ? "on" : ""}" data-editor-mode="write" role="tab">Write</button>
            <button class="${state.editorMode === "preview" ? "on" : ""}" data-editor-mode="preview" role="tab">Preview</button>
          </div>
          <span class="grow"></span>
          ${isIdea ? `<button class="btn sm" data-action="spark">${icon("zap", "sm")} Spark</button><button class="btn sm primary" data-action="promote">${icon("arrow", "sm")} Promote to task</button>` : ""}
          <button class="btn sm ghost danger icon-only" data-action="delete" aria-label="Delete note">${icon("trash", "sm")}</button>
        </div>
        ${state.editorMode === "write"
          ? `<textarea id="ed-body" class="body-input" aria-label="Note body" placeholder="Write in Markdown. Link other notes with [[file-name]].">${esc(note.body)}</textarea>`
          : `<div class="prose" id="ed-preview">${note.body.trim() ? renderMarkdown(note.body, (t) => core.resolveLink(state.notes, t)) : '<p class="muted">Nothing written yet.</p>'}</div>`}
        <div class="backlinks">
          <h3>Linked from</h3>
          ${backs.length ? backs.map((n) => rowItem(n, TYPE_META[n.type].label)).join("") : `<div class="empty">Nothing links here yet. Link to it from another note with <code>[[${esc(note.slug)}]]</code>.</div>`}
        </div>
      </div>
    </aside>`;
  $("#layer").appendChild(layer);

  $("#ed-title", layer).addEventListener("input", (e) => queueChange({ title: e.target.value.trim() || note.slug }));
  $("#ed-tags", layer).addEventListener("input", (e) =>
    queueChange({ tags: e.target.value.split(",").map((t) => t.trim().replace(/^#/, "").toLowerCase()).filter(Boolean) }),
  );
  $("#ed-status", layer)?.addEventListener("change", async (e) => {
    await flushPending();
    await moveNote(note.path, e.target.value);
  });
  const body = $("#ed-body", layer);
  if (body) {
    body.addEventListener("input", (e) => queueChange({ body: e.target.value }));
    body.addEventListener("keydown", (e) => {
      if (e.key === "Tab") {
        e.preventDefault();
        body.setRangeText("  ", body.selectionStart, body.selectionEnd, "end");
        queueChange({ body: body.value });
      }
    });
  }
  $("#ed-preview", layer)?.addEventListener("change", async (e) => {
    const box = e.target.closest("input[data-task]");
    if (!box) return;
    const current = noteByPath(note.path);
    await saveNote(note.path, { body: toggleTask(current.body, Number(box.dataset.task)) });
    renderView();
  });
  setTimeout(() => {
    // Never steal focus from a field the person already clicked into (fast typists, slow phones).
    if (layer.contains(document.activeElement) && document.activeElement !== layer) return;
    if (state.spark && body) body.focus();
    else $("#ed-title", layer)?.focus({ preventScroll: true });
  }, 30);
  if (state.spark && body) {
    body.selectionStart = body.selectionEnd = body.value.length;
    body.scrollTop = body.scrollHeight;
  }
}

async function addSpark() {
  const note = noteByPath(state.open);
  if (!note) return;
  await flushPending();
  const options = SPARKS.filter((s) => s !== state.spark);
  state.spark = options[Math.floor(Math.random() * options.length)];
  const body = note.body.replace(/\s*$/, "") + `\n\n### Spark: ${state.spark}\n\n`;
  await saveNote(note.path, { body });
  state.editorMode = "write";
  renderDrawer();
}

async function promote() {
  const idea = noteByPath(state.open);
  if (!idea) return;
  await flushPending();
  const task = await createNote({ type: "task", title: idea.title, tags: idea.tags, body: `From idea [[${idea.slug}]].\n\n- [ ] \n` });
  const status = ["raw", "exploring"].includes(idea.status) ? "ready" : idea.status;
  await saveNote(idea.path, { status, body: idea.body.replace(/\s*$/, "") + `\n\n→ Task: [[${task.slug}]]\n` });
  markGuide("promoted");
  state.spark = null;
  state.open = task.path;
  renderDrawer();
  render();
  toast(`Promoted to a task in To do`, { action: { label: "Board", run: () => setView("board") } });
}

// ============================================================ capture, journal, commands

function journalPathFor(day) {
  return `journal/${day}.md`;
}

async function openToday() {
  const path = journalPathFor(today());
  if (!noteByPath(path)) {
    await createNote({ type: "journal", title: today() });
    markGuide("captured");
  }
  render();
  openNote(path);
}

async function appendToJournal(line) {
  const path = journalPathFor(today());
  if (!noteByPath(path)) await createNote({ type: "journal", title: today(), body: "## Log\n\n" });
  const note = noteByPath(path);
  const time = new Date().toTimeString().slice(0, 5);
  const cleaned = note.body.replace(/\n- \s*$/, "\n").replace(/\s*$/, "");
  await saveNote(path, { body: `${cleaned}\n- ${time} ${line}\n` });
  return noteByPath(path);
}

async function capture(text) {
  const parsed = core.parseCapture(text);
  if (!parsed.title && !parsed.body) return;
  let note;
  if (parsed.type === "journal") {
    note = await appendToJournal(parsed.title + (parsed.tags.length ? " " + parsed.tags.map((t) => "#" + t).join(" ") : ""));
  } else {
    note = await createNote({ type: parsed.type, title: parsed.title, tags: parsed.tags, body: parsed.body ? parsed.body + "\n" : null });
  }
  markGuide("captured");
  render();
  const label = parsed.type === "journal" ? "Logged to today’s journal" : `${TYPE_META[parsed.type].label} saved to brain/${note.path}`;
  toast(label, { action: { label: "Open", run: () => openNote(note.path) } });
}

async function newNote(type) {
  const prompts = {
    idea: ["New idea", "What’s the idea?", "e.g. Share a live trip link"],
    task: ["New task", "What needs doing?", "e.g. Write onboarding copy"],
    decision: ["New decision", "What are you deciding?", "e.g. Use SQLite for local storage"],
    note: ["New note", "Give it a title", "e.g. Architecture overview"],
  };
  const [title, text, placeholder] = prompts[type] || prompts.note;
  const value = await ask({ title, text, placeholder, confirm: "Create" });
  if (!value) return;
  const parsed = core.parseCapture(value);
  const note = await createNote({ type, title: parsed.title || value, tags: parsed.tags });
  render();
  openNote(note.path);
}

async function todoToTask(source) {
  const item = (state.todos || []).find((i) => sourceKey(i) === source);
  if (!item) return;
  const words = item.text === "(no description)" ? `${item.tag} in ${item.path.split("/").pop()}` : item.text;
  const title = (words.charAt(0).toUpperCase() + words.slice(1)).slice(0, 80);
  await createNote({
    type: "task",
    title,
    tags: ["code"],
    extraMeta: { source },
    body: `From a \`${item.tag}\` comment in \`${source}\`.\n`,
  });
  render();
  toast("Task created in To do", { action: { label: "Board", run: () => setView("board") } });
}

async function saveContext() {
  const pack = core.contextPack(state.notes, state.info.project, today());
  const existing = fileByPath("CONTEXT.md");
  try {
    const { mtime } = await state.store.write("CONTEXT.md", pack, existing ? existing.mtime : null);
    upsertFile("CONTEXT.md", pack, mtime);
  } catch (error) {
    if (!(error instanceof ConflictError)) throw error;
    const { mtime } = await state.store.write("CONTEXT.md", pack, error.mtime);
    upsertFile("CONTEXT.md", pack, mtime);
  }
  scheduleGitRefresh();
  renderView();
  toast("Saved brain/CONTEXT.md");
}

async function copyText(text, message) {
  try {
    await navigator.clipboard.writeText(text);
    toast(message);
  } catch {
    toast("Couldn’t reach the clipboard. Select the text and copy it instead.", { error: true });
  }
}

async function commitBrain() {
  const changes = state.git?.changes || [];
  if (!changes.length) return;
  const message = await ask({
    title: "Commit the brain",
    text: `Commits only the brain folder (${plural(changes.length, "change")}). Push with your usual git tool.`,
    value: `brain: update ${plural(changes.length, "note")}`,
    list: changes.map((c) => `${c.status.padEnd(2)} ${c.path}`),
    confirm: "Commit",
  });
  if (!message) return;
  try {
    const result = await state.store.gitCommit(message);
    toast(`Committed ${result.commit}`);
  } catch (error) {
    toast(error.message, { error: true });
  }
  refreshGit();
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("cairn.theme", next);
  renderSidebarFoot();
}

async function switchBrain() {
  if (state.info.mode === "server") {
    await ask({
      title: "This brain is served locally",
      text: `Cairn is running for “${state.info.project}”. To open another repo, stop the server and run it there: python -m cairn --root path/to/repo`,
      confirm: "Got it",
      noInput: true,
    });
    return;
  }
  closeDrawer();
  localStorage.removeItem("cairn.mode");
  $("#app").hidden = true;
  showWelcome(await FolderStore.remembered());
}

// ============================================================ modal, toast, palette

function ask({ title, text = "", placeholder = "", value = "", confirm = "OK", list = null, noInput = false, danger = false }) {
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

function toast(message, { action = null, error = false } = {}) {
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

function openPalette() {
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

// ============================================================ global events

function isTyping(target) {
  return target.closest("input, textarea, select, [contenteditable=true]");
}

function wireGlobal() {
  document.addEventListener("click", async (event) => {
    const target = event.target;
    if (target.closest("select")) return;
    const go = target.closest("[data-go]");
    if (go) return setView(go.dataset.go);
    const open = target.closest("[data-open]");
    if (open && !target.closest("[data-action]")) {
      event.preventDefault();
      return openNote(open.dataset.open);
    }
    const filter = target.closest("[data-filter]");
    if (filter) {
      state.notesFilter = filter.dataset.filter;
      return renderView();
    }
    const tag = target.closest("[data-tag]");
    if (tag) {
      state.notesTag = state.notesTag === tag.dataset.tag ? null : tag.dataset.tag;
      return renderView();
    }
    const editorMode = target.closest("[data-editor-mode]");
    if (editorMode) {
      await flushPending();
      state.editorMode = editorMode.dataset.editorMode;
      return renderDrawer();
    }
    const contextMode = target.closest("[data-context-mode]");
    if (contextMode) {
      state.contextMode = contextMode.dataset.contextMode;
      return renderView();
    }
    const actionEl = target.closest("[data-action]");
    if (!actionEl) return;
    const action = actionEl.dataset.action;
    const mutating = ["new", "journal-today", "spark", "promote", "delete", "save-context", "todo-task", "commit", "load-sample", "spark-random"];
    if (readOnly() && mutating.includes(action)) {
      toast("This brain is read-only. Ask to join to contribute.");
      return;
    }
    const actions = {
      "toggle-nav": () => $("#app").classList.toggle("nav-open"),
      palette: openPalette,
      theme: toggleTheme,
      "close-drawer": () => {
        closeDrawer();
      },
      "dismiss-guide": () => {
        const g = guide();
        g.dismissed = true;
        localStorage.setItem(GUIDE_KEY, JSON.stringify(g));
        renderView();
      },
      new: () => newNote(actionEl.dataset.type),
      "journal-today": openToday,
      "spark-random": () => {
        const options = SPARKS.filter((s) => s !== state.spark);
        state.spark = options[Math.floor(Math.random() * options.length)];
        renderView();
      },
      spark: addSpark,
      promote,
      delete: async () => {
        const note = noteByPath(state.open);
        if (!note) return;
        const ok = await ask({ title: "Delete this note?", text: `brain/${note.path} will be removed. You can undo right after.`, confirm: "Delete", noInput: true, danger: true });
        if (ok) deleteNote(note.path);
      },
      "refresh-todos": () => {
        state.todos = null;
        renderView();
        refreshTodos();
      },
      "todo-task": () => todoToTask(actionEl.dataset.source),
      "copy-context": () => copyText(core.contextPack(state.notes, state.info.project, today()), "Context pack copied"),
      "save-context": saveContext,
      commit: commitBrain,
      "switch-brain": switchBrain,
      "map-reset": () => renderView(),
      "open-brain": () => openBrain(actionEl.dataset.repo),
      "sign-in": openSignIn,
      "sign-out": () => {
        auth.signOut();
        toast("Signed out. Your token was removed from this browser.");
        afterSignOut();
      },
      "create-brain": createMyBrain,
      "held-use": useHeld,
      "held-copy": () => copyText(state.held?.text || "", "Your text was copied"),
      "held-discard": () => {
        state.held = null;
        renderDrawer();
      },
      "open-profile": () => lookupProfile(actionEl.dataset.login),
      "leave-brain": () => {
        location.href = location.pathname;
      },
      "load-sample": async () => {
        state.store.reset(DemoStore.sample());
        await loadAll();
        render();
        toast("Loaded the sample project: Trailmix, an offline hiking app.");
      },
    };
    if (actions[action]) {
      event.preventDefault();
      try {
        await actions[action]();
      } catch (error) {
        console.error(error);
        toast(error.message || "Something went wrong", { error: true });
      }
    }
  });

  document.addEventListener("change", (event) => {
    const move = event.target.closest("[data-move]");
    if (move) moveNote(move.dataset.move, move.value);
  });

  document.addEventListener("input", (event) => {
    if (event.target.id === "notes-search") {
      state.notesQuery = event.target.value;
      const position = event.target.selectionStart;
      renderView();
      const input = $("#notes-search");
      input.focus();
      input.setSelectionRange(position, position);
    }
  });

  document.addEventListener("submit", async (event) => {
    if (event.target.id === "people-form") {
      event.preventDefault();
      return lookupProfile(event.target.query.value);
    }
    if (event.target.id === "create-brain-form") {
      event.preventDefault();
      return createMyBrain();
    }
    const quick = event.target.closest("[data-quick]");
    if (!quick) return;
    event.preventDefault();
    const value = quick.title.value.trim();
    if (!value) return;
    const [type, status] = quick.dataset.quick.split(":");
    const parsed = core.parseCapture(value);
    await createNote({ type, title: parsed.title || value, tags: parsed.tags, status });
    markGuide("captured");
    renderView();
    renderNav();
    $(`[data-quick="${type}:${status}"] input`)?.focus();
  });

  const captureInput = $("#capture-input");
  const captureType = $("#capture-type");
  captureInput.addEventListener("input", () => {
    const value = captureInput.value.trim();
    $("#capture-hint").hidden = Boolean(value);
    captureType.hidden = !value;
    if (value) {
      const type = core.parseCapture(value).type;
      captureType.className = `capture-type pill t-${type}`;
      captureType.textContent = type === "journal" ? "Journal log" : TYPE_META[type].label;
    }
  });
  $("#capture-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = captureInput.value.trim();
    if (!value || readOnly()) return;
    captureInput.value = "";
    captureInput.dispatchEvent(new Event("input"));
    try {
      await capture(value);
    } catch (error) {
      captureInput.value = value;
      toast(error.message || "Could not save", { error: true });
    }
  });

  document.addEventListener("keydown", (event) => {
    const mod = event.ctrlKey || event.metaKey;
    if (mod && event.key.toLowerCase() === "k") {
      event.preventDefault();
      return openPalette();
    }
    if (event.key === "Escape") {
      if ($(".palette-wrap") || $(".modal-wrap")) return;
      if (state.open) return closeDrawer();
      $("#app").classList.remove("nav-open");
      return;
    }
    if (isTyping(event.target) || mod || event.altKey) return;
    if (event.key === "/") {
      event.preventDefault();
      openPalette();
    } else if (event.key === "c") {
      event.preventDefault();
      captureInput.focus();
    } else if (event.key === "Enter" && event.target.matches?.(".item-card, .map-node")) {
      openNote(event.target.dataset.open);
    }
  });

  window.addEventListener("popstate", () => setView(location.hash.replace("#/", "") || "home", { push: false }));
  window.addEventListener("beforeunload", () => flushPending());
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    if (localStorage.getItem("cairn.theme")) return;
    document.documentElement.dataset.theme = e.matches ? "dark" : "light";
    renderSidebarFoot();
  });
}

// ============================================================ welcome + boot

class VisitorStore extends DemoStore {
  constructor() {
    super();
    this.files = {};
  }

  save() {}

  async info() {
    return { mode: "visitor", project: "Explore", label: "Exploring · nothing saved", canGit: false, canScan: false };
  }

  async todos() {
    return [];
  }
}

function exploreTarget(raw) {
  const parsed = gh.parseGithubInput(raw);
  if (!parsed) return null;
  return parsed.repo ? `?brain=${encodeURIComponent(parsed.owner + "/" + parsed.repo)}` : `?u=${encodeURIComponent(parsed.owner)}`;
}


function welcomeAccount() {
  const me = auth.session();
  const last = localStorage.getItem(LAST_BRAIN_KEY);
  if (me) {
    return `
      <div class="welcome-account">
        <img src="${esc(me.avatar)}" alt="" width="28" height="28">
        <span>Signed in as <strong>@${esc(me.login)}</strong></span>
        ${last ? `<button class="btn primary" data-action="open-brain" data-repo="${esc(last)}">${icon("arrow", "sm")} Continue in ${esc(last)}</button>` : ""}
        <a class="btn" href="?u=${encodeURIComponent(me.login)}">Your brains</a>
      </div>`;
  }
  return `
    <div class="welcome-account">
      <span>Keep your brain on GitHub and edit it from your phone, laptop or any browser.</span>
      <button class="btn" data-action="sign-in">${icon("github", "sm")} Sign in with GitHub</button>
    </div>`;
}

function showWelcome(remembered) {
  const welcome = $("#welcome");
  const folderSupported = FolderStore.supported();
  welcome.innerHTML = `
    <div class="welcome-inner">
      <div class="brand">${LOGO}<span>Cairn</span></div>
      <h1>A second brain that lives <em>inside your repo</em></h1>
      <p class="lede">Capture ideas, brainstorm, record decisions and move tasks, all as plain Markdown in a <code>brain/</code> folder next to your code. Works alongside Obsidian, VS Code and any AI assistant.</p>
      <div class="choices">
        <section class="card choice">
          <span class="badge-icon">${icon("play")}</span>
          <h2>Try the demo</h2>
          <p>A sample project (an offline hiking app) with ideas, tasks, decisions and notes. Saved only in this browser.</p>
          <button class="btn primary" data-start="demo">${icon("arrow")} Open the demo</button>
          <button class="sub-link" data-start="empty">or start with an empty brain</button>
        </section>
        <section class="card choice">
          <span class="badge-icon">${icon("folder")}</span>
          <h2>Open a repo folder</h2>
          <p>${folderSupported ? "Pick your project folder. Cairn reads and writes its <code>brain/</code> folder directly. Nothing is uploaded." : "Needs Chrome or Edge on a computer. You can still use the demo or the local app."}</p>
          ${remembered ? `<button class="btn primary" data-start="reopen">${icon("folder")} Reopen “${esc(remembered.root.name)}”</button>` : ""}
          <button class="btn ${remembered ? "" : "primary"}" data-start="folder" ${folderSupported ? "" : "disabled"}>${icon("folder")} Choose a folder</button>
        </section>
        <section class="card choice">
          <span class="badge-icon">${icon("terminal")}</span>
          <h2>Run it locally</h2>
          <p>Full app with code scanning and one-click git commits. Python 3.10+, no dependencies.</p>
          <code>git clone ${REPO_URL}<br>python -m cairn --root ~/your-repo</code>
          <button class="btn" data-copy-cmd>${icon("copy")} Copy commands</button>
        </section>
      </div>
      ${welcomeAccount()}
      <form class="explore-box" id="explore-form" autocomplete="off">
        <label for="explore-input">Or look someone up</label>
        <div class="explore-row">
          ${icon("search")}
          <input id="explore-input" name="who" placeholder="github.com/username, username/repo or just a username">
          <button class="btn" type="submit">Explore</button>
        </div>
      </form>
      <div class="welcome-points">
        <div><strong>Plain files</strong>Markdown with a tiny header. Delete Cairn and your brain still reads fine on GitHub.</div>
        <div><strong>Knows your code</strong>Turns TODO comments into tasks and keeps the file and line.</div>
        <div><strong>Made for AI too</strong>One click writes a context brief any assistant can read.</div>
        <div><strong>Private</strong>No account, no server, no tracking. MIT licensed.</div>
      </div>
      <p class="welcome-foot"><a href="${REPO_URL}" target="_blank" rel="noopener">Source on GitHub</a> · <a href="https://juw12.github.io/p/cairn/" target="_blank" rel="noopener">About Cairn</a></p>
    </div>`;
  welcome.hidden = false;
  $("#explore-form", welcome).onsubmit = (event) => {
    event.preventDefault();
    const target = exploreTarget(event.target.who.value);
    if (!target) return toast("That doesn't look like a GitHub username or link.", { error: true });
    location.href = location.pathname + target;
  };
  welcome.onclick = async (event) => {
    const copyButton = event.target.closest("[data-copy-cmd]");
    if (copyButton) return copyText(`git clone ${REPO_URL}\npython -m cairn --root ~/your-repo`, "Commands copied");
    const button = event.target.closest("[data-start]");
    if (!button) return;
    try {
      const kind = button.dataset.start;
      if (kind === "demo" || kind === "empty") {
        const store = new DemoStore();
        if (kind === "empty") store.reset(DemoStore.empty());
        localStorage.setItem("cairn.mode", "demo");
        return start(store);
      }
      const store = kind === "reopen" ? remembered : await FolderStore.pick();
      if (!(await store.ensurePermission())) return toast("Cairn needs permission to read and write that folder.", { error: true });
      localStorage.setItem("cairn.mode", "folder");
      return start(store);
    } catch (error) {
      if (error.name !== "AbortError") toast(error.message || "Couldn’t open that folder", { error: true });
    }
  };
}

function applyInfo() {
  $("#project-name").textContent = state.info.project;
  $("#project-mode").textContent = state.info.label;
  document.title = state.info.owner ? `${state.info.owner}/${state.info.project} · Cairn` : `${state.info.project} · Cairn`;
  document.body.classList.toggle("readonly", readOnly());
  $("#capture-form").hidden = readOnly() || state.info.mode === "visitor";
  renderSidebarFoot();
}

async function start(store) {
  state.store = store;
  state.info = await store.info();
  state.todos = null;
  state.git = null;
  await loadAll();
  $("#welcome").hidden = true;
  $("#app").hidden = false;
  applyInfo();
  setView(location.hash.replace("#/", "") || "home", { push: false });
  refreshTodos();
  refreshGit();
}

async function boot() {
  $("#brand").innerHTML = `${LOGO}<span>Cairn</span>`;
  $("#menu-btn").innerHTML = icon("menu");
  $("#capture-icon").innerHTML = icon("plus");
  $("#search-btn").innerHTML = `${icon("search", "sm")}<span class="sr-only">Search</span><kbd>Ctrl K</kbd>`;
  if (matchMedia("(max-width: 900px)").matches) {
    $("#capture-input").placeholder = "Capture… idea, todo:, decision:";
  }
  wireGlobal();

  const params = new URLSearchParams(location.search);
  const brainParam = params.get("brain");
  if (brainParam) {
    const parsed = gh.parseGithubInput(brainParam);
    try {
      if (!parsed || !parsed.repo) throw new Error("Use owner/repo, for example juw12/cairn.");
      const repo = await gh.fetchRepo(parsed.owner, parsed.repo);
      const loaded = await gh.loadBrainFiles(repo.owner, repo.name, repo.branch);
      const writable = auth.signedIn() && repo.canPush;
      if (writable) localStorage.setItem(LAST_BRAIN_KEY, repo.fullName);
      return start(new GitHubStore(repo, loaded, { writable }));
    } catch (error) {
      toast(error.message || "Couldn't open that brain.", { error: true });
      await start(new VisitorStore());
      return setView("people");
    }
  }
  const server = await ServerStore.detect();
  const userParam = params.get("u");
  if (server) {
    await start(server);
    if (userParam) lookupProfile(userParam);
    return;
  }
  if (userParam) {
    const mode = localStorage.getItem("cairn.mode");
    await start(mode === "demo" ? new DemoStore() : new VisitorStore());
    return lookupProfile(userParam);
  }
  if (params.has("empty")) {
    const store = new DemoStore();
    store.reset(DemoStore.empty());
    localStorage.setItem("cairn.mode", "demo");
    return start(store);
  }
  if (params.has("demo")) {
    localStorage.setItem("cairn.mode", "demo");
    return start(new DemoStore());
  }
  const mode = localStorage.getItem("cairn.mode");
  if (mode === "demo") return start(new DemoStore());
  showWelcome(mode === "folder" ? await FolderStore.remembered() : null);
}

// A 401 anywhere ends the session; say so plainly and downgrade to read-only.
window.addEventListener("cairn:signed-out", (event) => {
  const reason = event.detail?.reason;
  if (reason) toast(reason, { error: true });
  if (state.store) afterSignOut();
});

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

if ("serviceWorker" in navigator && location.protocol === "https:") {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

window.cairn = { state, core, rerender: () => renderView() };
boot();
