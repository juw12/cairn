// Entry point: shell rendering, global events and boot.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { ServerStore, FolderStore, DemoStore, GitHubStore } from "./store.js";
import * as gh from "./github.js";
import * as auth from "./auth.js";
import { brainKey, readBrain } from "./brain-cache.js";
import { $, LOGO, SPARKS, TYPE_META, ask, icon, plural, toast, today } from "./ui.js";
import { GUIDE_KEY, HUBS, LAST_BRAIN_KEY, REPO_URL, VIEWS, createNote, deleteNote, guide, hubOf, loadAll, markGuide, noteByPath, readOnly, refreshGit, refreshTodos, state } from "./state.js";
import { board, codeView, contextView, decisions, home, ideas, mcpSnippet, moveNote, notesView, showMoreNotes, wireBoard } from "./views.js";
import { mapView, resetMap, wireMap } from "./map.js";
import { initObsidian, linkObsidian, obsidianView, syncObsidian, toggleAutoSync, unlinkObsidian } from "./obsidian.js";
import { afterSignOut, createMyBrain, lookupProfile, openBrain, openSignIn, peopleView, readOnlyBanner } from "./people.js";
import { addFriend, loadLeaderboard, publishStats, removeFriend, setLeaderboardStat, toggleLeaderboardDevice, unpublishStats } from "./leaderboard.js";
import { checkMyClaims, forgetProofLogin, newClaim, proofView, setProofLogin, showAllClaims, vouchFor } from "./proofview.js";
import { copyAgentHook, endSession, jumpToList, jumpToLive, leaveVibe, liveSessions, loadSampleSessions, setOverviewOutcome, setVibeDevice, setVibeFilter, setVibeOutcome, shareStats, showMoreSessions, startSession, moveRepo, switchPusher, cloudSyncNow, measureSpeed, handWork, handPhone, pickHandAgent, moveRank, resetRank, toggleRow, toggleSessionDay, foldAllDays, jumpToDay, backToChart, toggleWhy, vibeView, wireVibe } from "./vibe.js";
import { addProperty, addSpark, bookmarkOpenNote, closeDrawer, closeTab, createMissing, cycleTab, flushPending, openNote, openSplit, promote, renameOpenNote, renderDrawer, reopenClosedTab, resetTabs, toggleSplit, useHeld } from "./drawer.js";
import { capture, commitBrain, copyText, newNote, openPairPhone, openSettings, openToday, openVersions, reloadBrain, saveContext, switchBrain, syncBrain, todoToTask, toggleTheme } from "./actions.js";
import { openPalette } from "./palette.js";
import { startLiveSync } from "./live.js";
import { chatView, glowView, startChatAbout, wireChat, wireGlow } from "./chat.js";
import { showShortcuts } from "./shortcuts.js";
import { initVoiceLog, journalView, openLogSheet, syncLogFab, wireJournal } from "./voicelog.js";
import { showWelcome } from "./welcome.js";
import { openSignIn as showSignInDialog } from "./signin.js";
import { maybeStartTour, startTour } from "./tour.js";
import { initHoverPreview } from "./hover.js";
import { initRichContent } from "./rich.js";
import { canvasCount, canvasView, flushCanvas, wireCanvas } from "./canvas.js";
import { autopilotCount, autopilotView, jobAction, previewBrief, queueDone, queueItems, queueToggle, removePro, selectJob, toggleEnabled, wireAutopilot } from "./autopilot.js";
import { hidePanel, movePanel, openPanel, resetLayout, showAllPanels, togglePanel, wirePanels } from "./vibelayout.js";
import { pickChartDay, setChartMetric, setChartRange, showNeeds, wireHome } from "./home.js";
import { renameDevice, sayHandoff, sendHandoff, toggleHandoff, toggleHandoffForm, toggleSettled } from "./handoffs.js";
import { projectsAction, projectsView, submitProjectsOwner } from "./projects.js";
import { diaryView, setDiaryDevice, showAllDiaryTasks, toggleDiaryDay } from "./diaryview.js";
import { wireRepo } from "./repo.js";
import { initDatabases } from "./database.js";
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
    chat: "",
    glow: "",
    ideas: by("idea", ["raw", "exploring", "ready"]),
    board: by("task", ["todo", "doing"]),
    decisions: by("decision", ["proposed"]) || "",
    notes: state.notes.length,
    journal: by("journal"),
    diary: "",
    map: "",
    code: state.todos ? state.todos.length : "",
    projects: "",
    vibe: ((n) => (n > 1 ? `${n} live` : n ? "live" : ""))(liveSessions().length),
    autopilot: autopilotCount() || queueItems().length || "",
    context: "",
    obsidian: "",
    canvas: canvasCount() || "",
    plugins: "",
    people: "",
    proof: state.notes.filter((n) => typeof n.meta?.claim === "string" && n.meta.claim.trim()).length || "",
  };
}

// ---- hubs: the sidebar has one entry per hub; the pages inside a hub are tabs at the top of the view.

const HUB_TAB_KEY = "cairn.hubTab";

function hubTabs() {
  try {
    return JSON.parse(localStorage.getItem(HUB_TAB_KEY)) || {};
  } catch {
    return {};
  }
}

// The page a sidebar entry opens: the tab used last inside that hub, else its first page.
function hubTarget(hub, remembered) {
  return hub.views.includes(remembered[hub.id]) ? remembered[hub.id] : hub.views[0];
}

function rememberHubTab(view) {
  const hub = hubOf(view);
  if (!hub || hub.views.length < 2) return;
  try {
    localStorage.setItem(HUB_TAB_KEY, JSON.stringify({ ...hubTabs(), [hub.id]: view }));
  } catch {
    /* storage is off: the entry just opens its first page */
  }
}

function hubTabStrip() {
  const hub = hubOf(state.view);
  if (!hub || hub.views.length < 2) return "";
  const counted = counts();
  const tabs = hub.views.map((id) => {
    const active = id === state.view;
    const count = counted[id] === "" ? "" : `<span class="count">${counted[id]}</span>`;
    return `<button type="button" class="hub-tab${active ? " active" : ""}" data-go="${id}" ${active ? 'aria-current="page"' : ""}>${VIEWS.find((view) => view.id === id).label}${count}</button>`;
  });
  return `<nav class="hub-tabs" aria-label="${hub.label} pages">${tabs.join("")}</nav>`;
}

export function renderNav() {
  const counted = counts();
  const nav = $("#nav");
  // Rendering replaces the buttons, so note which entry holds the keyboard focus and hand it back afterwards.
  const focused = nav.contains(document.activeElement) ? document.activeElement : null;
  const focusKey = focused ? focused.dataset.hub || focused.dataset.go : "";
  const remembered = hubTabs();
  const html = [];
  for (const hub of HUBS) {
    if (hub.hidden) continue;
    const active = hub.views.includes(state.view);
    const number = hub.count ? counted[hub.count] : "";
    const count = number === "" ? "" : `<span class="count">${number}</span>`;
    html.push(
      `<button class="${active ? "active" : ""}" data-hub="${hub.id}" data-views="${hub.views.join(" ")}" data-go="${hubTarget(hub, remembered)}" ${active ? 'aria-current="page"' : ""}>${icon(hub.icon)}<span>${hub.label}</span>${count}</button>`,
    );
  }
  const extra = pluginViews();
  if (extra.length) html.push(`<div class="nav-label">Plugins</div>`);
  for (const view of extra) {
    const active = state.view === view.id;
    html.push(`<button class="${active ? "active" : ""}" data-go="${esc(view.id)}" ${active ? 'aria-current="page"' : ""}>${icon(view.icon)}<span>${esc(view.label)}</span></button>`);
  }
  nav.innerHTML = html.join("");
  // On a phone the sidebar is a drawer that closes after a choice, so only refocus while it is on screen.
  const sidebarShown = !matchMedia("(max-width: 900px)").matches || $("#app").classList.contains("nav-open");
  if (focusKey && sidebarShown) {
    const again = [...nav.querySelectorAll("button")].find((button) => (button.dataset.hub || button.dataset.go) === focusKey);
    if (again) again.focus({ preventScroll: true });
  }
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
      <button class="btn sm ghost icon-only" data-go="obsidian" aria-label="Tools: all notes, code inbox, Obsidian sync, context pack, plugins" title="Tools: all notes, code inbox, Obsidian sync, context pack, plugins">${icon("puzzle", "sm")}</button>
      ${state.info?.desktop
        ? `<button class="btn sm ghost" data-action="settings">${icon("gear", "sm")} Settings</button>`
        : `<a class="btn sm ghost" href="${REPO_URL}" data-github target="_blank" rel="noopener">${icon("github", "sm")} GitHub</a>`}
    </div>
    ${state.build.newest > state.build.page
      ? `<button class="btn sm primary foot-update" data-action="update-now" title="Reload Cairn into the newest build. Nothing you typed is lost.">${icon("refresh", "sm")} Update to build ${state.build.newest}</button>`
      : ""}
    <button class="btn sm ghost foot-versions" data-action="versions" title="Every build and what changed">${icon("history", "sm")} Version history</button>`);
  $("#sidebar-foot").innerHTML = parts.join("");
}

// The page an address names: "#/projects/juw12/cairn/files/docs" is the Projects page; what follows the first
// part is the page's own (there, a repo and a folder in it, see repo.js).
const viewOfHash = () => location.hash.replace(/^#\/?/, "").split("/")[0] || "home";

export function setView(view, { push = true } = {}) {
  if (!VIEWS.some((v) => v.id === view) && !isPluginView(view)) view = "home";
  if (state.view === "canvas" && view !== "canvas") flushCanvas();
  state.view = view;
  rememberHubTab(view);
  emitPluginEvent("view-change", { view });
  if (push && location.hash !== `#/${view}`) history.pushState(null, "", `#/${view}`);
  $("#app").classList.remove("nav-open");
  render();
  $("#view").scrollTop = 0;
  window.scrollTo(0, 0);
  if (view === "code" && state.todos === null) refreshTodos();
}

export function renderView() {
  const renderers = { home, chat: chatView, glow: glowView, ideas, board, decisions, notes: notesView, journal: journalView, diary: diaryView, map: mapView, canvas: canvasView, code: codeView, context: contextView, obsidian: obsidianView, plugins: pluginsView, people: peopleView, proof: proofView, vibe: vibeView, autopilot: autopilotView, projects: projectsView };
  const view = $("#view");
  // Background loads (e.g. the discover list) re-render the view; keep whatever the person is typing
  // in any field of the view, and keep it focused.
  const typing = $("#people-query");
  if (typing) state.peopleQuery = typing.value;
  const active = document.activeElement;
  const kept = active && view.contains(active) && active.id && /^(INPUT|TEXTAREA)$/.test(active.tagName)
    ? { id: active.id, value: active.value, start: active.selectionStart, end: active.selectionEnd }
    : null;
  const onTabs = Boolean(active && view.contains(active) && active.closest(".hub-tabs"));
  const pluginView = isPluginView(state.view);
  const tabs = pluginView ? "" : hubTabStrip();
  view.classList.toggle("has-hub-tabs", Boolean(tabs));
  view.innerHTML = readOnlyBanner() + tabs + (pluginView
    ? `<div class="plugin-view" data-plugin-view="${esc(state.view)}"></div>`
    : (renderers[state.view] || home)());
  if (onTabs) $('.hub-tabs [aria-current="page"]')?.focus({ preventScroll: true });
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

// A chat started from a session shows its banner in the Conversation block, so that block is unfolded.
function openChatPage() {
  setView("chat");
  openPanel("talk", "chat");
}

function afterRender() {
  if ((state.view === "ideas" || state.view === "board") && !readOnly()) wireBoard();
  if (state.view === "map") wireMap();
  if (state.view === "canvas") wireCanvas();
  if (state.view === "plugins") wirePlugins();
  if (state.view === "journal") wireJournal();
  if (state.view === "home") wireHome();
  if (state.view === "projects") wireRepo();
  syncLogFab();
  wirePanels();
  const pluginView = $("[data-plugin-view]");
  if (pluginView) renderPluginView(state.view, pluginView);
  if (state.view === "vibe") wireVibe();
  else leaveVibe();
  if (state.view === "autopilot") wireAutopilot();
  if (state.view === "chat") wireChat();
  if (state.view === "glow") wireGlow();
}

// ============================================================ global events

function isTyping(target) {
  return target.closest("input, textarea, select, [contenteditable=true]");
}

function wireGlobal() {
  document.addEventListener("click", async (event) => {
    const target = event.target;
    // The narrow-window menu is a drawer over the page: a click anywhere outside it closes it. Its backdrop
    // covers the page, so that click only closes; a click on something above the backdrop still does its job.
    const shell = $("#app");
    if (shell.classList.contains("nav-open") && !target.closest("#sidebar, [data-action=toggle-nav], .tour")) {
      shell.classList.remove("nav-open");
      if (target === shell) return;
    }
    if (target.closest("select")) return;
    const go = target.closest("[data-go]");
    if (go) return setView(go.dataset.go);
    const open = target.closest("[data-open]");
    if (open && !target.closest("[data-action]")) {
      event.preventDefault();
      if (event.altKey && state.open && open.dataset.open !== state.open) return openSplit(open.dataset.open);
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
    const mcpClient = target.closest("[data-mcp-client]");
    if (mcpClient) {
      state.mcpClient = mcpClient.dataset.mcpClient;
      return renderView();
    }
    if (state.view === "plugins" && (await handlePluginClick(target))) return;
    const actionEl = target.closest("[data-action]");
    if (!actionEl) return;
    const action = actionEl.dataset.action;
    const mutating = ["new", "journal-today", "spark", "promote", "delete", "save-context", "todo-task", "commit", "load-sample", "spark-random", "obsidian-sync", "obsidian-unlink", "new-claim", "rename", "prop-add", "vibe-start", "vibe-end", "vibe-sample", "vibe-gh-move", "vibe-hand", "vibe-hand-phone", "vibe-rank", "vibe-rank-reset", "ap-toggle", "ap-job", "ap-flag", "ap-q-done", "ap-pro-remove"];
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
      "vibe-start": startSession,
      "vibe-end": () => endSession(actionEl.dataset.result, actionEl.dataset.path),
      "vibe-sample": loadSampleSessions,
      "vibe-copy-hook": copyAgentHook,
      "vibe-filter": () => setVibeFilter(actionEl.dataset.source),
    "vibe-device": () => setVibeDevice(actionEl.dataset.device),
      "diary-device": () => setDiaryDevice(actionEl.dataset.device),
      "diary-day": () => toggleDiaryDay(actionEl.dataset.day),
      "diary-all": showAllDiaryTasks,
      "vibe-more": () => showMoreSessions(actionEl.dataset.all === "1"),
      "vibe-outcome": () => setVibeOutcome(actionEl.dataset.outcome),
      "vibe-overview": () => setOverviewOutcome(actionEl.dataset.outcome),
      "vibe-lane": () => jumpToLive(actionEl.dataset.path),
      "vibe-continue": () => startChatAbout(actionEl.dataset.path, "continue") && openChatPage(),
      "vibe-newchat": () => startChatAbout(actionEl.dataset.path, "new") && openChatPage(),
      "vibe-share": shareStats,
      "vibe-gh-move": () => moveRepo(actionEl.dataset.repo, actionEl.dataset.from, actionEl.dataset.to),
      "vibe-gh-push": () => switchPusher(actionEl.dataset.account),
      "vibe-cloud-sync": () => cloudSyncNow(Boolean(actionEl.dataset.repos)),
      "vibe-net-speed": measureSpeed,
      "vibe-hand": () => handWork(actionEl.dataset.to || "", actionEl.dataset.session || ""),
      "vibe-hand-agent": () => pickHandAgent(actionEl.dataset.agent || ""),
      "vibe-hand-phone": () => handPhone(actionEl.dataset.to || "", actionEl.dataset.agent || ""),
      "vibe-rank": () => moveRank(actionEl.dataset.session || "", actionEl.dataset.to || ""),
      "vibe-rank-reset": resetRank,
      "vibe-to-list": jumpToList,
      "home-chart-day": () => pickChartDay(actionEl.dataset.day),
      "home-chart-range": () => setChartRange(actionEl.dataset.range) && renderView(),
      "home-chart-metric": () => setChartMetric(actionEl.dataset.metric) && renderView(),
      "handoff-open": () => toggleHandoff(actionEl.dataset.thread),
      "handoff-write": toggleHandoffForm,
      "handoff-settled": toggleSettled,
      "handoff-rename": renameDevice,
      "handoff-say": () => sayHandoff(actionEl.dataset.thread, actionEl.dataset.says),
      "home-needs-all": () => {
        showNeeds();
        setView("notes");
      },
      "vibe-panel-fold": () => togglePanel(actionEl.dataset.panel, actionEl.dataset.page || "vibe"),
      "vibe-panel-move": () => movePanel(actionEl.dataset.panel, actionEl.dataset.step, actionEl.dataset.page || "vibe"),
      "vibe-panel-hide": () => hidePanel(actionEl.dataset.panel, actionEl.dataset.page || "vibe"),
      "vibe-panel-show": () => hidePanel(actionEl.dataset.panel, actionEl.dataset.page || "vibe", true),
      "vibe-panel-show-all": () => showAllPanels(actionEl.dataset.page || "vibe"),
      "vibe-layout-reset": () => resetLayout(actionEl.dataset.page || "vibe"),
      "vibe-row": () => toggleRow(actionEl.dataset.path),
      "vibe-day": () => toggleSessionDay(actionEl.dataset.day),
      "ap-toggle": toggleEnabled,
      "ap-pro-remove": removePro,
      "ap-job": () => jobAction(actionEl.dataset.op, actionEl.dataset.id),
      "ap-select": () => selectJob(actionEl.dataset.id),
      "ap-brief": () => previewBrief($("#ap-goal-form")),
      "ap-flag": () => queueToggle(actionEl.dataset.path, actionEl.dataset.flag),
      "ap-q-done": () => queueDone(actionEl.dataset.path),
      "vibe-fold-all": () => foldAllDays(actionEl.dataset.days),
      "vibe-jump": () => jumpToDay(actionEl.dataset.day),
      "vibe-to-chart": () => backToChart(actionEl.dataset.day),
      "vibe-why": () => toggleWhy(actionEl.dataset.why),
      "projects-refresh": () => projectsAction("projects-refresh", actionEl),
      "projects-sort": () => projectsAction("projects-sort", actionEl),
      "projects-group": () => projectsAction("projects-group", actionEl),
      "projects-filter": () => projectsAction("projects-filter", actionEl),
      "projects-filter-clear": () => projectsAction("projects-filter-clear", actionEl),
      "projects-note": () => projectsAction("projects-note", actionEl),
      "copy-context": () => copyText(core.contextPack(state.notes, state.info.project, today()), "Context pack copied"),
      "save-context": saveContext,
      "copy-mcp": () => copyText(mcpSnippet(), "MCP setup copied"),
      "notes-more": () => {
        showMoreNotes();
        renderView();
      },
      commit: commitBrain,
      sync: syncBrain,
      settings: openSettings,
    versions: openVersions,
    "update-now": () => applyUpdate({ now: true }),
      "switch-brain": switchBrain,
      "obsidian-sync": () => syncObsidian(),
      "obsidian-unlink": unlinkObsidian,
      "map-reset": () => resetMap(),
      "open-brain": () => openBrain(actionEl.dataset.repo),
      "lb-stat": () => setLeaderboardStat(actionEl.dataset.stat),
      "lb-device": () => toggleLeaderboardDevice(actionEl.dataset.device),
      "lb-refresh": () => {
        loadLeaderboard(true);
        renderView();
      },
      "friend-add": () => addFriend(actionEl.dataset.repo),
      "friend-remove": () => removeFriend(actionEl.dataset.repo),
      "stats-publish": publishStats,
      "stats-unpublish": unpublishStats,
      "sign-in": openSignIn,
      "sign-out": () => {
        auth.signOut();
        toast("Signed out. Your token was removed from this browser.");
        afterSignOut();
      },
      "create-brain": createMyBrain,
      "proof-check": checkMyClaims,
      "proof-forget": forgetProofLogin,
      "proof-all": showAllClaims,
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
    // Projects by category: a category folded here stays folded.
    if (event.target.matches?.("details[data-proj-group]")) return projectsAction("projects-group-toggle", event.target);
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
    if (event.target.matches("[data-projects-auto]")) projectsAction("projects-auto-note", event.target);
    handlePluginChange(event.target);
  });

  document.addEventListener("input", (event) => {
    if (event.target.id === "projects-filter") projectsAction("projects-filter-text", event.target);
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
    if (event.target.id === "handoff-form") {
      event.preventDefault();
      return sendHandoff(event.target);
    }
    if (event.target.dataset.handoffReply) {
      event.preventDefault();
      return sayHandoff(event.target.dataset.handoffReply, "note");
    }
    if (event.target.id === "friend-form") {
      event.preventDefault();
      return addFriend(event.target.query.value);
    }
    if (event.target.id === "obsidian-form") {
      event.preventDefault();
      return linkObsidian(event.target);
    }
    if (event.target.id === "proof-login-form") {
      event.preventDefault();
      return setProofLogin(event.target);
    }
    if (event.target.id === "projects-form") {
      event.preventDefault();
      return submitProjectsOwner(event.target);
    }
    if (event.target.id === "create-brain-form") {
      event.preventDefault();
      return createMyBrain();
    }
    if (event.target.id === "plugin-install-form") {
      event.preventDefault();
      return handlePluginSubmit(event.target);
    }
    if (event.target.matches("[data-plugin-form]")) {
      event.preventDefault(); // settings save as they change; Enter must not reload the page
      return;
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
      if ((key === "\\" || event.code === "Backslash") && state.open) {
        event.preventDefault();
        return toggleSplit();
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
    } else if (event.key === "?") {
      event.preventDefault();
      showShortcuts();
    } else if ((event.key === "l" || event.key === "L") && !event.defaultPrevented) {
      // On the Map, L with a note selected means "local map" (handled there first).
      event.preventDefault();
      openLogSheet();
    } else if (event.key === "c") {
      event.preventDefault();
      captureInput.focus();
    } else if (event.key === "Enter" && event.target.matches?.(".item-card, .map-node")) {
      openNote(event.target.dataset.open);
    }
  });

  window.addEventListener("popstate", () => setView(viewOfHash(), { push: false }));
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
  initVoiceLog();
  syncLogFab();
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
  setView(viewOfHash(), { push: false });
  refreshTodos();
  refreshGit();
  initObsidian();
  startLiveSync();
  if (resumeAfterUpdate) {
    const note = resumeAfterUpdate;
    resumeAfterUpdate = null;
    if (note.open && state.notes?.some?.((n) => n.path === note.open)) openNote(note.open);
    toast("Cairn updated to the latest version.");
  } else {
    maybeStartTour();
  }
}

// Sign in by QR: the PC shows a QR code of .../#/chat?signin=<token>. The token sits in the
// fragment (never sent to a server) and is removed from the address bar before anything
// else runs, so it doesn't stay in history, bookmarks or the home-screen shortcut.
async function signInFromLink() {
  const match = location.hash.match(/^#\/([\w-]*)\?signin=([^&]+)/);
  if (!match) return;
  history.replaceState(null, "", `${location.pathname}${location.search}#/${match[1] || "chat"}`);
  try {
    const who = await auth.signInWithToken(decodeURIComponent(match[2]), { remember: true });
    toast(`Signed in as ${who.login}. This device stays signed in.`);
  } catch (error) {
    toast(error.message || "Couldn't sign in with that link.", { error: true });
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
  initDatabases();
  await signInFromLink();

  const params = new URLSearchParams(location.search);
  // A signed-in browser with no ?brain= goes back to the brain it last opened, so the phone,
  // a second laptop and this PC all show the same Map, Live and notes without a special link.
  const lastBrain = auth.signedIn() && !params.has("u") && !params.has("demo") && !params.has("empty")
    && !["demo", "folder"].includes(localStorage.getItem("cairn.mode")) ? localStorage.getItem(LAST_BRAIN_KEY) : null;
  const brainParam = params.get("brain") || (location.protocol === "https:" ? lastBrain : null);
  // Keep the brain in the address bar: "Add to Home Screen" saves this address, so the icon opens your
  // brain instead of the landing page.
  if (brainParam && !params.has("brain")) {
    params.set("brain", brainParam);
    history.replaceState(null, "", `${location.pathname}?${params}${location.hash}`);
  }
  if (brainParam) {
    const parsed = gh.parseGithubInput(brainParam);
    // Opened before on this device (browser tab or home-screen app): show that copy at once; live sync
    // fetches whatever changed on GitHub since, in the background.
    const login = auth.session()?.login;
    const cacheKey = login && parsed?.repo ? brainKey(login, `${parsed.owner}/${parsed.repo}`) : "";
    const cached = cacheKey ? await readBrain(cacheKey) : null;
    if (cached?.files) {
      const loaded = { root: cached.root, files: cached.files.map((file) => ({ ...file, mtime: 0 })), assetPaths: cached.assetPaths, etag: cached.etag, cached: true };
      return start(new GitHubStore(cached.repo, loaded, { writable: cached.writable, cacheKey }));
    }
    // A big vault takes a few seconds over a phone connection: say so instead of a blank page.
    const loading = $("#welcome");
    loading.innerHTML = `
      <div class="welcome-loading" role="status">
        <p>Opening <strong>${esc(brainParam)}</strong>…</p>
        <p class="muted">Loading your notes from GitHub. A big vault takes a few seconds.</p>
      </div>`;
    loading.hidden = false;
    try {
      if (!parsed || !parsed.repo) throw new Error("Use owner/repo, for example juw12/cairn.");
      const repo = await gh.fetchRepo(parsed.owner, parsed.repo);
      const loaded = await gh.loadBrainFiles(repo.owner, repo.name, repo.branch);
      const writable = auth.signedIn() && repo.canPush;
      if (writable) localStorage.setItem(LAST_BRAIN_KEY, repo.fullName);
      // Only your own (writable) brains are kept on the device, the ones Cairn reopens by itself.
      return start(new GitHubStore(repo, loaded, { writable, cacheKey: writable ? cacheKey : "" }));
    } catch (error) {
      loading.hidden = true;
      if (!auth.signedIn()) {
        // Private brains look "not found" to a signed-out browser: ask to sign in, then reopen it.
        toast(`Sign in with GitHub to open ${brainParam}. Private brains need your sign-in on this device.`, { error: true });
        await start(new VisitorStore());
        setView("people");
        return showSignInDialog({ icon, onSignedIn: () => location.reload() });
      }
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
  // A phone's home-screen app stays in memory for days, so it kept running the old version after an
  // update. When a new worker takes over an open page, reload into it once you pause (applyUpdate saves
  // first and reopens the same view). Not on the very first install, when no worker ran this page.
  const hadWorker = Boolean(navigator.serviceWorker.controller);
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    // A page that was open for the very first install has no old version to leave. It still looks whether a
    // newer build is out: before, such a page ignored every later update for as long as it stayed open.
    if (!hadWorker) return void lookForUpdate();
    if (reloading) return;
    reloading = true;
    applyUpdate();
  });
  navigator.serviceWorker.register("sw.js")
    .then((registration) => {
      // Coming back to the app is the moment to look for a new version.
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden) registration.update().catch(() => {});
      });
    })
    .catch(() => {});
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

// ---- a newer build, found by the page itself
// A service worker's own update can be slow or silent, most of all in a phone's home-screen app, and it never
// says anything. So the page also looks for itself: versions.json as the page starts is the build it runs, and
// the same file later (each time Cairn comes back to the front, and every ten minutes while it stays there) is
// the newest build the site serves. A newer one is said out loud with an Update now button, the sidebar offers
// it until it is taken, and it is taken by the usual rule: at once when you were away, otherwise when you pause.
// The desktop app has its own updater and is left to it.
let announcedBuild = 0;

async function servedBuild() {
  try {
    const response = await fetch("versions.json", { cache: "no-store" });
    if (!response.ok) return 0;
    return Number((await response.json()).releases?.[0]?.build) || 0;
  } catch {
    return 0; // offline: nothing newer can be known, and nothing is offered
  }
}

async function lookForUpdate() {
  const build = await servedBuild();
  if (!build) return "unknown";
  if (!state.build.page) {
    state.build.page = build;
    return "current";
  }
  if (build <= state.build.page || state.info?.desktop) return "current";
  state.build.newest = build;
  if ($("#app") && !$("#app").hidden) renderSidebarFoot();
  // The worker takes the new files too, so the next start without a network has them.
  navigator.serviceWorker?.getRegistration?.().then((registration) => registration?.update()).catch(() => {});
  if (announcedBuild !== build) {
    announcedBuild = build;
    toast(`Cairn build ${build} is ready.`, { action: { label: "Update now", run: () => applyUpdate({ now: true }) } });
  }
  applyUpdate();
  return "newer";
}

lookForUpdate();
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) lookForUpdate();
});
setInterval(() => {
  if (!document.hidden) lookForUpdate();
}, window.CAIRN_UPDATE_LOOK_MS ?? 10 * 60 * 1000);

function readyForUpdate(restart) {
  if (document.hidden) return true;
  const idleMs = window.CAIRN_UPDATE_IDLE_MS ?? (restart ? 120_000 : 30_000);
  if (Date.now() - lastInput < idleMs) return false;
  return !document.querySelector(".modal-wrap, .palette-wrap, .tour, .link-suggest");
}

// now: you pressed Update now, so there is no waiting for a pause.
async function applyUpdate({ restart = false, now = false } = {}) {
  clearTimeout(updateTimer);
  if (!now && !readyForUpdate(restart)) {
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
// pairPhone(): the tray's "Pair a phone" opens the QR code.
window.cairn = { state, core, rerender: () => renderView(), reload: () => reloadBrain(), applyUpdate, lookForUpdate, pairPhone: () => openPairPhone() };
boot();
