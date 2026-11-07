// Entry point: shell rendering, global events and boot.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { ServerStore, FolderStore, DemoStore, GitHubStore } from "./store.js";
import * as gh from "./github.js";
import * as auth from "./auth.js";
import { $, LOGO, SPARKS, TYPE_META, ask, icon, plural, toast, today } from "./ui.js";
import { GUIDE_KEY, LAST_BRAIN_KEY, REPO_URL, VIEWS, createNote, deleteNote, guide, loadAll, markGuide, noteByPath, readOnly, refreshGit, refreshTodos, state } from "./state.js";
import { board, codeView, contextView, decisions, home, ideas, journal, moveNote, notesView, wireBoard } from "./views.js";
import { mapView, resetMap, wireMap } from "./map.js";
import { initObsidian, linkObsidian, obsidianView, syncObsidian, toggleAutoSync, unlinkObsidian } from "./obsidian.js";
import { afterSignOut, createMyBrain, lookupProfile, openBrain, openSignIn, peopleView, readOnlyBanner } from "./people.js";
import { checkMyClaims, forgetProofLogin, newClaim, proofView, setProofLogin, vouchFor } from "./proofview.js";
import { addProperty, addSpark, bookmarkOpenNote, closeDrawer, closeTab, createMissing, cycleTab, flushPending, openNote, promote, renameOpenNote, renderDrawer, reopenClosedTab, resetTabs, useHeld } from "./drawer.js";
import { capture, commitBrain, copyText, newNote, openSettings, openToday, reloadBrain, saveContext, switchBrain, syncBrain, todoToTask, toggleTheme } from "./actions.js";
import { openPalette } from "./palette.js";
import { showWelcome } from "./welcome.js";
import { maybeStartTour, startTour } from "./tour.js";
import { initHoverPreview } from "./hover.js";
import { initRichContent } from "./rich.js";
import { canvasCount, canvasView, flushCanvas, wireCanvas } from "./canvas.js";
import { emit as emitPluginEvent, handlePluginChange, handlePluginClick, handlePluginSubmit, initPlugins, isPluginView, pluginViews, pluginsView, renderPluginView, wirePlugins } from "./plugins.js";

// ============================================================ shell rendering

export function render() {
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
    obsidian: "",
    canvas: canvasCount() || "",
    plugins: "",
    people: "",
    proof: state.notes.filter((n) => typeof n.meta?.claim === "string" && n.meta.claim.trim()).length || "",
  };
}

export function renderNav() {
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
  const extra = pluginViews();
  if (extra.length) html.push(`<div class="nav-label">Plugins</div>`);
  for (const view of extra) {
    const active = state.view === view.id;
    html.push(`<button class="${active ? "active" : ""}" data-go="${esc(view.id)}" ${active ? 'aria-current="page"' : ""}>${icon(view.icon)}<span>${esc(view.label)}</span></button>`);
  }
  $("#nav").innerHTML = html.join("");
}

export function renderSidebarFoot() {
  const theme = document.documentElement.dataset.theme;
  const parts = [];
  if (state.info?.canGit && state.git?.available) {
    const changes = state.git.changes.length;
    const { ahead = 0, behind = 0, remote = false } = state.git;
    const status = [
      changes ? plural(changes, "change") : "",
      ahead ? `↑${ahead}` : "",
      behind ? `↓${behind}` : "",
    ].filter(Boolean).join(" · ") || (remote ? "in sync" : "clean");
    const button = remote
      ? `<button class="btn sm ${changes || ahead || behind ? "primary" : ""}" style="width:100%;margin-top:8px" data-action="sync" ${state.syncing ? "disabled" : ""} title="Save your edits, get everyone else's and send yours to GitHub">${icon("refresh", "sm")} ${state.syncing ? "Syncing…" : "Sync"}</button>`
      : `<button class="btn sm ${changes ? "primary" : ""}" style="width:100%;margin-top:8px" data-action="commit" ${changes ? "" : "disabled"}>${icon("git", "sm")} Commit brain</button>`;
    parts.push(`
      <div class="git-card">
        <div class="row"><span class="branch">${icon("branch", "sm")}${esc(state.git.branch || "git")}</span><span class="muted">${status}</span></div>
        ${button}
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
      <button class="btn sm ghost icon-only" data-action="tour" aria-label="Take the tour" title="Take the tour">${icon("help", "sm")}</button>
      ${state.info?.desktop
        ? `<button class="btn sm ghost" data-action="settings">${icon("gear", "sm")} Settings</button>`
        : `<a class="btn sm ghost" href="${REPO_URL}" target="_blank" rel="noopener">${icon("github", "sm")} GitHub</a>`}
    </div>`);
  $("#sidebar-foot").innerHTML = parts.join("");
}

export function setView(view, { push = true } = {}) {
  if (!VIEWS.some((v) => v.id === view) && !isPluginView(view)) view = "home";
  if (state.view === "canvas" && view !== "canvas") flushCanvas();
  state.view = view;
  emitPluginEvent("view-change", { view });
  if (push && location.hash !== `#/${view}`) history.pushState(null, "", `#/${view}`);
  $("#app").classList.remove("nav-open");
  render();
  $("#view").scrollTop = 0;
  window.scrollTo(0, 0);
  if (view === "code" && state.todos === null) refreshTodos();
}

export function renderView() {
  const renderers = { home, ideas, board, decisions, notes: notesView, journal, map: mapView, canvas: canvasView, code: codeView, context: contextView, obsidian: obsidianView, plugins: pluginsView, people: peopleView, proof: proofView };
  const view = $("#view");
  // Background loads (e.g. the discover list) re-render the view; keep whatever the person is typing
  // in any field of the view, and keep it focused.
  const typing = $("#people-query");
  if (typing) state.peopleQuery = typing.value;
  const active = document.activeElement;
  const kept = active && view.contains(active) && active.id && /^(INPUT|TEXTAREA)$/.test(active.tagName)
    ? { id: active.id, value: active.value, start: active.selectionStart, end: active.selectionEnd }
    : null;
  const pluginView = isPluginView(state.view);
  view.innerHTML = readOnlyBanner() + (pluginView
    ? `<div class="plugin-view" data-plugin-view="${esc(state.view)}"></div>`
    : (renderers[state.view] || home)());
  if (kept) {
    const restored = document.getElementById(kept.id);
    if (restored) {
      restored.value = kept.value;
      restored.focus();
      try {
        restored.setSelectionRange(kept.start ?? kept.value.length, kept.end ?? kept.value.length);
      } catch {
        /* some input types have no selection */
      }
    }
  }
  afterRender();
}

function afterRender() {
  if ((state.view === "ideas" || state.view === "board") && !readOnly()) wireBoard();
  if (state.view === "map") wireMap();
  if (state.view === "canvas") wireCanvas();
  if (state.view === "plugins") wirePlugins();
  const pluginView = $("[data-plugin-view]");
  if (pluginView) renderPluginView(state.view, pluginView);
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
      return openNote(open.dataset.open, { heading: open.dataset.heading || null, newTab: event.ctrlKey || event.metaKey });
    }
    const missing = target.closest("[data-missing]");
    if (missing) {
      event.preventDefault();
      return createMissing(missing.dataset.missing);
    }
    const tagLink = target.closest("[data-tag-link]");
    if (tagLink) {
      event.preventDefault();
      if (state.open) closeDrawer();
      state.notesQuery = `tag:${tagLink.dataset.tagLink}`;
      state.notesFilter = "all";
      state.notesTag = null;
      return setView("notes");
    }
    const notesLayout = target.closest("[data-notes-layout]");
    if (notesLayout) {
      state.notesLayout = notesLayout.dataset.notesLayout;
      return renderView();
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
    if (state.view === "plugins" && (await handlePluginClick(target))) return;
    const actionEl = target.closest("[data-action]");
    if (!actionEl) return;
    const action = actionEl.dataset.action;
    const mutating = ["new", "journal-today", "spark", "promote", "delete", "save-context", "todo-task", "commit", "load-sample", "spark-random", "obsidian-sync", "obsidian-unlink", "new-claim", "rename", "prop-add"];
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
      rename: renameOpenNote,
      bookmark: () => bookmarkOpenNote(),
      "prop-add": addProperty,
      tour: startTour,
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
      sync: syncBrain,
      settings: openSettings,
      "switch-brain": switchBrain,
      "obsidian-sync": () => syncObsidian(),
      "obsidian-unlink": unlinkObsidian,
      "map-reset": () => resetMap(),
      "open-brain": () => openBrain(actionEl.dataset.repo),
      "sign-in": openSignIn,
      "sign-out": () => {
        auth.signOut();
        toast("Signed out. Your token was removed from this browser.");
        afterSignOut();
      },
      "create-brain": createMyBrain,
      "proof-check": checkMyClaims,
      "proof-forget": forgetProofLogin,
      "new-claim": newClaim,
      vouch: () => vouchFor(actionEl.dataset.subject, actionEl.dataset.claim),
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

  // Middle-click on any note link or card opens it in a new tab, like a browser.
  document.addEventListener("auxclick", (event) => {
    if (event.button !== 1) return;
    const open = event.target.closest("[data-open]");
    if (!open) return;
    event.preventDefault();
    openNote(open.dataset.open, { heading: open.dataset.heading || null, newTab: true });
  });

  // Folder tree: remember which folders were collapsed across re-renders.
  document.addEventListener("toggle", (event) => {
    const folder = event.target.closest?.("details[data-folder]");
    if (!folder) return;
    state.closedFolders ||= new Set();
    if (folder.open) state.closedFolders.delete(folder.dataset.folder);
    else state.closedFolders.add(folder.dataset.folder);
  }, true);

  document.addEventListener("change", (event) => {
    const move = event.target.closest("[data-move]");
    if (move) moveNote(move.dataset.move, move.value);
    if (event.target.matches("[data-obsidian-auto]")) toggleAutoSync(event.target.checked);
    handlePluginChange(event.target);
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
    if (event.target.id === "obsidian-form") {
      event.preventDefault();
      return linkObsidian(event.target);
    }
    if (event.target.id === "proof-login-form") {
      event.preventDefault();
      return setProofLogin(event.target);
    }
    if (event.target.id === "create-brain-form") {
      event.preventDefault();
      return createMyBrain();
    }
    if (event.target.id === "plugin-install-form") {
      event.preventDefault();
      return handlePluginSubmit(event.target);
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
    // Tabs: Ctrl+Alt+→/← switch, Ctrl+Alt+W closes, Ctrl+Alt+T reopens the last closed tab.
    if (mod && event.altKey && !event.shiftKey) {
      const key = event.key.toLowerCase();
      if (key === "arrowright" || key === "arrowleft") {
        if (!state.open) return;
        event.preventDefault();
        return cycleTab(key === "arrowright" ? 1 : -1);
      }
      if (key === "w" && state.open) {
        event.preventDefault();
        return closeTab();
      }
      if (key === "t") {
        event.preventDefault();
        return reopenClosedTab();
      }
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
  window.addEventListener("beforeunload", () => {
    flushPending();
    flushCanvas();
  });
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


export function applyInfo() {
  $("#project-name").textContent = state.info.project;
  $("#project-mode").textContent = state.info.label;
  document.title = state.info.owner ? `${state.info.owner}/${state.info.project} · Cairn` : `${state.info.project} · Cairn`;
  document.body.classList.toggle("readonly", readOnly());
  $("#capture-form").hidden = readOnly() || state.info.mode === "visitor";
  renderSidebarFoot();
}

export async function start(store) {
  state.store = store;
  state.info = await store.info();
  state.todos = null;
  state.git = null;
  state.canvas = null;
  resetTabs();
  await loadAll();
  await initPlugins();
  $("#welcome").hidden = true;
  $("#app").hidden = false;
  applyInfo();
  setView(location.hash.replace("#/", "") || "home", { push: false });
  refreshTodos();
  refreshGit();
  initObsidian();
  if (resumeAfterUpdate) {
    const note = resumeAfterUpdate;
    resumeAfterUpdate = null;
    if (note.open && state.notes?.some?.((n) => n.path === note.open)) openNote(note.open);
    toast("Cairn updated to the latest version.");
  } else {
    maybeStartTour();
  }
}

async function boot() {
  $("#brand").innerHTML = `${LOGO}<span>Cairn</span>`;
  $("#menu-btn").innerHTML = icon("menu");
  $("#capture-icon").innerHTML = icon("plus");
  $("#search-btn").innerHTML = `${icon("search", "sm")}<span class="sr-only">Search</span><kbd>Ctrl K</kbd>`;
  if (matchMedia("(max-width: 900px)").matches) {
    $("#capture-input").placeholder = "Capture…";
  }
  wireGlobal();
  initHoverPreview();
  initRichContent();

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

if ("serviceWorker" in navigator && location.protocol === "https:") {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

// ============================================================ desktop auto-update
// The desktop app downloads updates in the background and calls applyUpdate(). It waits until
// you pause (nothing typed or clicked for a while, no dialog open), saves, then reloads the page
// (new web app) or asks for a quick restart (new Python part) and reopens the same view and note.

const UPDATE_NOTE = "cairn.updated";
let lastInput = Date.now();
let updateTimer = null;
let resumeAfterUpdate = takeUpdateNote();

for (const type of ["keydown", "pointerdown", "input", "wheel"]) {
  window.addEventListener(type, () => { lastInput = Date.now(); }, { capture: true, passive: true });
}

function takeUpdateNote() {
  try {
    const raw = localStorage.getItem(UPDATE_NOTE);
    if (!raw) return null;
    localStorage.removeItem(UPDATE_NOTE);
    const note = JSON.parse(raw);
    if (!note || Date.now() - note.at > 10 * 60 * 1000) return null;
    if (note.hash && !location.hash) history.replaceState(null, "", note.hash);
    return note;
  } catch {
    return null;
  }
}

function readyForUpdate(restart) {
  if (document.hidden) return true;
  const idleMs = window.CAIRN_UPDATE_IDLE_MS ?? (restart ? 120_000 : 30_000);
  if (Date.now() - lastInput < idleMs) return false;
  return !document.querySelector(".modal-wrap, .palette-wrap, .tour, .link-suggest");
}

async function applyUpdate({ restart = false } = {}) {
  clearTimeout(updateTimer);
  if (!readyForUpdate(restart)) {
    updateTimer = setTimeout(() => applyUpdate({ restart }), window.CAIRN_UPDATE_RETRY_MS ?? 15_000);
    return "waiting";
  }
  await flushPending();
  await flushCanvas();
  localStorage.setItem(UPDATE_NOTE, JSON.stringify({ hash: location.hash, open: state.open || null, at: Date.now() }));
  if (!restart) {
    location.reload();
    return "reloading";
  }
  try {
    const result = await state.store.desktopAction("restart");
    if (result?.restarting) return "restarting";
  } catch {
    // fall through: nothing to restart for, so nothing to resume either
  }
  localStorage.removeItem(UPDATE_NOTE);
  return "skipped";
}

// reload(): the desktop app calls this after a background sync brought in someone else's edits.
window.cairn = { state, core, rerender: () => renderView(), reload: () => reloadBrain(), applyUpdate };
boot();
