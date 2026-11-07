// Capture, journal and other commands that act on the brain.

import * as core from "./core.js";
import { FolderStore, ConflictError } from "./store.js";
import { escapeHtml as esc } from "./md.js";
import { $, TYPE_META, ask, icon, plural, toast, today } from "./ui.js";
import { createNote, fileByPath, loadAll, markGuide, noteByPath, refreshGit, saveNote, scheduleGitRefresh, state, upsertFile } from "./state.js";
import { sourceKey } from "./views.js";
import { closeDrawer, openNote } from "./drawer.js";
import { showWelcome } from "./welcome.js";
import { render, renderSidebarFoot, renderView, setView } from "./app.js";

// ============================================================ capture, journal, commands

function journalPathFor(day) {
  return `journal/${day}.md`;
}

export async function openToday() {
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

export async function capture(text) {
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

export async function newNote(type) {
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

export async function todoToTask(source) {
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

export async function saveContext() {
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

export async function copyText(text, message) {
  try {
    await navigator.clipboard.writeText(text);
    toast(message);
  } catch {
    toast("Couldn’t reach the clipboard. Select the text and copy it instead.", { error: true });
  }
}

export async function commitBrain() {
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

export function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("cairn.theme", next);
  renderSidebarFoot();
}

export async function switchBrain() {
  if (state.info.desktop) return openBrainDialog();
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

// ============================================================ sync (commit + pull + push in one go)

export async function reloadBrain() {
  await loadAll();
  render();
  refreshGit();
}

export async function syncBrain() {
  if (state.syncing) return;
  state.syncing = true;
  renderSidebarFoot();
  try {
    const result = await state.store.gitSync();
    const done = [];
    if (result.committed) done.push("saved your edits");
    if (result.pulled) done.push(`got ${plural(result.pulled, "update")}`);
    if (result.pushed) done.push(`sent ${plural(result.pushed, "commit")}`);
    if (!result.remote) {
      toast(result.committed
        ? `Saved your edits (commit ${result.committed}). This folder isn't linked to GitHub, so there's nothing to send.`
        : "Nothing to sync: this folder isn't linked to GitHub.");
    } else {
      toast(done.length ? `Synced: ${done.join(", ")}.` : "Already up to date.");
    }
    if (result.pulled) await reloadBrain();
  } catch (error) {
    toast(error.message, { error: true });
  }
  state.syncing = false;
  refreshGit();
}

// ============================================================ desktop app: open a brain, settings

function modal(html, wire) {
  const wrap = document.createElement("div");
  wrap.className = "modal-wrap";
  wrap.innerHTML = html;
  const close = () => wrap.remove();
  wrap.addEventListener("click", (e) => {
    if (e.target === wrap || e.target.closest("[data-cancel]")) close();
  });
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
    }
  });
  document.body.appendChild(wrap);
  wire(wrap, close);
  setTimeout(() => $("button, input", wrap)?.focus(), 20);
  return wrap;
}

async function desktopAction(action, payload, busyMessage) {
  if (busyMessage) toast(busyMessage);
  try {
    const result = await state.store.desktopAction(action, payload);
    if (result.reload) location.reload();
    return result;
  } catch (error) {
    toast(error.message, { error: true });
    return null;
  }
}

export async function openBrainDialog() {
  let places = [];
  try {
    places = (await state.store.desktop()).places || [];  // already without the brain that's open
  } catch {
    /* the list is a convenience */
  }
  modal(`
    <div class="modal open-brain" role="dialog" aria-modal="true" aria-labelledby="open-brain-title">
      <h2 id="open-brain-title">Open a brain</h2>
      <p>Any folder of Markdown notes works, including an Obsidian vault. Cairn remembers your choice.</p>
      <div class="pick-list">
        <button class="pick" data-open="pick">${icon("folder")}<span><strong>Choose a folder…</strong><small>A vault or project already on this computer</small></span></button>
        <button class="pick" data-open="clone">${icon("github")}<span><strong>Get one from GitHub…</strong><small>Downloads it and keeps it in sync</small></span></button>
        <button class="pick" data-open="new">${icon("plus")}<span><strong>Start a new brain</strong><small>An empty brain in Documents\\Cairn</small></span></button>
      </div>
      ${places.length ? `<div class="nav-label">Recent and Obsidian vaults</div>
        <div class="pick-list compact">${places.map((p) => `
          <button class="pick" data-path="${esc(p.path)}">${icon("note")}<span><strong>${esc(p.name)}</strong><small>${esc(p.path)}</small></span></button>`).join("")}
        </div>` : ""}
      <div class="actions"><button type="button" class="btn ghost" data-cancel>Close</button></div>
    </div>`, (wrap, close) => {
    wrap.addEventListener("click", async (e) => {
      const pathButton = e.target.closest("[data-path]");
      const openButton = e.target.closest("[data-open]");
      if (pathButton) {
        close();
        return desktopAction("open", { path: pathButton.dataset.path });
      }
      if (!openButton) return;
      const kind = openButton.dataset.open;
      close();
      if (kind === "pick") return desktopAction("pick", {});
      if (kind === "clone") {
        const url = await ask({
          title: "Get a brain from GitHub",
          text: "Paste the repo's address (or owner/repo). Private repos work too: GitHub asks you to sign in the first time.",
          placeholder: "https://github.com/you/notes",
          confirm: "Download",
        });
        if (url) desktopAction("clone", { url }, "Downloading… this can take a moment for big vaults.");
        return;
      }
      if (kind === "new") {
        const name = await ask({ title: "Name your new brain", value: "My brain", confirm: "Create" });
        if (name) desktopAction("new", { name });
      }
    });
  });
}

export async function openSettings() {
  let info;
  try {
    info = await state.store.desktop();
  } catch (error) {
    toast(error.message, { error: true });
    return;
  }
  modal(`
    <div class="modal settings" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <h2 id="settings-title">Settings</h2>
      <p>Brain: <strong>${esc(state.info.project)}</strong><br><small class="muted">${esc(info.folder || "")}</small></p>
      <button class="btn sm" data-settings="switch">${icon("folder", "sm")} Open another brain</button>
      ${info.autostartSupported ? `<label class="check"><input type="checkbox" data-settings="autostart" ${info.autostart ? "checked" : ""}> Start with Windows <span class="muted">(opens quietly in the tray)</span></label>` : ""}
      <label class="check"><input type="checkbox" data-settings="auto-sync" ${info.autoSync ? "checked" : ""}> Sync automatically every 10 minutes <span class="muted">(when the brain is linked to GitHub)</span></label>
      ${info.updates ? `<label class="check"><input type="checkbox" data-settings="auto-update" ${info.autoUpdate !== false ? "checked" : ""}> Update automatically <span class="muted">(in the background; no restart needed)</span></label>` : ""}
      <div class="settings-update">
        <span>Cairn ${esc(info.version)}${info.build ? ` <span class="muted">· ${esc(info.build)}</span>` : ""}</span>
        ${info.updates ? `<button class="btn sm" data-settings="check-update">${icon("refresh", "sm")} Check for updates</button>` : `<span class="muted">Updates come with your package manager.</span>`}
      </div>
      <p class="hint">Closing the window keeps Cairn running in the tray. Quit it from the tray icon.</p>
      <div class="actions"><button type="button" class="btn primary" data-cancel>Done</button></div>
    </div>`, (wrap, close) => {
    wrap.addEventListener("change", async (e) => {
      const box = e.target.closest("input[data-settings]");
      if (!box) return;
      const result = await desktopAction(box.dataset.settings, { on: box.checked });
      if (!result) box.checked = !box.checked;
    });
    wrap.addEventListener("click", async (e) => {
      const button = e.target.closest("button[data-settings]");
      if (!button) return;
      if (button.dataset.settings === "switch") {
        close();
        return openBrainDialog();
      }
      if (button.dataset.settings === "check-update") {
        button.disabled = true;
        button.textContent = "Checking…";
        const result = await desktopAction("check-update", {});
        if (result?.available) {
          button.disabled = false;
          button.className = "btn sm primary";
          button.dataset.settings = "update";
          button.textContent = `Update now (${plural(result.behind, "change")})`;
        } else {
          button.textContent = result?.reason || "You're up to date";
        }
        return;
      }
      if (button.dataset.settings === "update") {
        button.disabled = true;
        button.textContent = "Updating…";
        const result = await desktopAction("update", {});
        if (result?.restarting) toast("Updated. Cairn is restarting…");
      }
    });
  });
}
