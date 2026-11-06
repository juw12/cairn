// The editor drawer: open, edit and save one note, sparks and promotion.

import * as core from "./core.js";
import { renderMarkdown, toggleTask, escapeHtml as esc } from "./md.js";
import { $, SPARKS, STATUS_LABEL, TYPE_META, icon, rowItem, toast, typePill } from "./ui.js";
import { createNote, fileByPath, markGuide, noteByPath, readOnly, saveNote, state, upsertFile } from "./state.js";
import { moveNote } from "./views.js";
import { render, renderView, setView } from "./app.js";

// ============================================================ editor drawer

let pending = null;
let pendingTimer = null;

export function openNote(path) {
  if (!noteByPath(path)) return;
  flushPending();
  state.open = path;
  renderDrawer();
}

export function closeDrawer() {
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

export function flushPending() {
  clearTimeout(pendingTimer);
  if (!pending) return Promise.resolve();
  const { path, changes } = pending;
  pending = null;
  return saveNote(path, changes)
    .then((saved) => {
      if (saved) setSaveState(state.info?.savedLabel || "Saved");
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

export async function useHeld() {
  const held = state.held;
  if (!held) return;
  const file = fileByPath(held.path);
  const { mtime } = await state.store.write(held.path, held.text, file ? file.mtime : null);
  state.held = null;
  upsertFile(held.path, held.text, mtime);
  if (state.open === held.path) renderDrawer();
  render();
  toast(`${state.info?.savedLabel || "Saved"}: your version`);
}

export function renderDrawer() {
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
        <span class="save-state${state.held?.path === note.path ? " unsaved" : ""}" id="save-state">${readOnly() ? "Read-only" : state.held?.path === note.path ? "Not saved" : "Saved"}</span>
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
          : `<div class="prose" id="ed-preview">${note.body.trim() ? renderMarkdown(note.body, (t) => core.resolveLink(state.notes, t, note.path)) : '<p class="muted">Nothing written yet.</p>'}</div>`}
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

export async function addSpark() {
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

export async function promote() {
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
