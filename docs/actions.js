// Capture, journal and other commands that act on the brain.

import * as core from "./core.js";
import { FolderStore, ConflictError } from "./store.js";
import { $, TYPE_META, ask, plural, toast, today } from "./ui.js";
import { createNote, fileByPath, markGuide, noteByPath, refreshGit, saveNote, scheduleGitRefresh, state, upsertFile } from "./state.js";
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
