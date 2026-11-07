// The editor drawer: open, edit and save one note. Write, Split (live preview) or Preview; [[ link
// suggestions; properties; outline, links out, backlinks and unlinked mentions; rename; bookmarks.

import * as core from "./core.js";
import { escapeHtml as esc, headings, toggleTask, wordCount } from "./md.js";
import { linkMention, linkTextFor, unlinkedMentions } from "./graph.js";
import { $, SPARKS, STATUS_LABEL, TYPE_META, ask, icon, plural, rowItem, toast, typePill } from "./ui.js";
import { createNote, fileByPath, markGuide, noteByPath, readOnly, saveNote, state, upsertFile } from "./state.js";
import { hydrateAssets, isBookmarked, normalizeNotePath, renameNote, renderNote, resolveFrom, toggleBookmark } from "./vault.js";
import { moveNote } from "./views.js";
import { render, renderView, setView } from "./app.js";

// ============================================================ editor drawer

let pending = null;
let pendingTimer = null;
let previewTimer = null;
let lastCaret = null;

// Which side panels are open; remembered while the app is open (module-local: state.js is not
// initialised yet when this module is first evaluated, because the two import each other).
const PANELS = { outline: true, links: false, mentions: false };
const BUILT_IN = new Set(["title", "type", "status", "tags", "created", "source"]);

export function openNote(path, { heading = null } = {}) {
  if (!noteByPath(path)) return;
  flushPending();
  state.open = path;
  state.scrollHeading = heading;
  renderDrawer();
}

export function closeDrawer() {
  flushPending();
  state.open = null;
  state.spark = null;
  closeSuggest();
  $("#drawer-layer")?.remove();
}

function queueChange(changes) {
  if (!state.open) return;
  if (!pending || pending.path !== state.open) pending = { path: state.open, changes: {} };
  if (changes.properties) changes = { ...changes, properties: { ...(pending.changes.properties || {}), ...changes.properties } };
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

// ------------------------------------------------------------ properties

function propertyKind(value) {
  if (Array.isArray(value)) return "list";
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  return "text";
}

function propertyText(value) {
  if (Array.isArray(value)) return value.map(propertyText).join(", ");
  if (value && typeof value === "object") return typeof value.raw === "string" ? value.raw : JSON.stringify(value);
  return value === null || value === undefined ? "" : String(value);
}

function parseProperty(text, kind) {
  const value = text.trim();
  if (kind === "list") return value ? value.split(",").map((item) => item.trim()).filter(Boolean) : [];
  if (kind === "number" && value !== "" && !Number.isNaN(Number(value))) return Number(value);
  if (kind === "boolean" && /^(true|false)$/i.test(value)) return value.toLowerCase() === "true";
  return value;
}

function propertiesHtml(note, locked) {
  return Object.entries(note.meta)
    .filter(([key]) => !BUILT_IN.has(key))
    .map(([key, value], index) => `
      <label for="prop-${index}" title="${esc(key)}">${esc(key)}</label>
      <div class="prop-row">
        <input id="prop-${index}" class="input" data-prop="${esc(key)}" data-kind="${propertyKind(value)}" value="${esc(propertyText(value))}" ${locked}>
        ${locked ? "" : `<button class="btn ghost sm icon-only" data-prop-remove="${esc(key)}" aria-label="Remove ${esc(key)}" title="Remove property">${icon("x", "sm")}</button>`}
      </div>`)
    .join("");
}

// ------------------------------------------------------------ panels

function outlineHtml(note) {
  const list = headings(note.body);
  if (!list.length) return "";
  return panel("outline", "Outline", list.length, `<nav class="outline">${list
    .map((h) => `<button class="outline-item" style="--level:${h.level}" data-jump="${esc(h.id)}" data-line="${h.line}">${esc(h.text)}</button>`)
    .join("")}</nav>`);
}

function outgoingHtml(note) {
  const resolve = resolveFrom(note.path);
  const seen = new Set();
  const rows = [];
  for (const { target } of core.noteLinks(note)) {
    const key = target.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const found = resolve(target);
    if (found && found.path === note.path) continue;
    if (found) rows.push(rowItem(found, TYPE_META[found.type].label));
    else if (!core.isAttachment(target)) rows.push(`<div class="row-item missing-row"><span class="wikilink missing" data-missing="${esc(target)}" title="Click to create it">${esc(target)}</span><span class="meta">not created yet</span></div>`);
  }
  return panel("links", "Links out", rows.length, rows.length ? rows.join("") : `<div class="empty">This note doesn't link anywhere yet. Type <code>[[</code> to link one.</div>`);
}

function mentionsHtml() {
  return panel("mentions", "Unlinked mentions", "", `<div id="mentions-body"><div class="empty">Looking…</div></div>`);
}

function panel(id, title, count, body) {
  return `<details class="panel" data-panel="${id}" ${PANELS[id] ? "open" : ""}><summary>${title}${count === "" ? "" : `<span class="count">${count}</span>`}</summary><div class="panel-body">${body}</div></details>`;
}

function fillMentions(note) {
  const body = $("#mentions-body");
  if (!body) return;
  const found = unlinkedMentions(state.notes, core.buildGraph(state.notes)).filter((m) => m.to === note.path);
  if (!found.length) {
    body.innerHTML = `<div class="empty">No other note mentions “${esc(note.title)}” without linking it.</div>`;
    return;
  }
  const mark = (context, phrase) => esc(context).replace(new RegExp(esc(phrase).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), (hit) => `<mark>${hit}</mark>`);
  body.innerHTML = found
    .map((m) => {
      const from = noteByPath(m.from);
      return `<div class="mention-row">
        <div class="mention-main"><a class="wikilink" href="#" data-open="${esc(m.from)}">${esc(from?.title || m.from)}</a><div class="snippet">${mark(m.context, m.phrase)}</div></div>
        ${readOnly() ? "" : `<button class="btn sm" data-link-mention="${esc(m.from)}" data-phrase="${esc(m.phrase)}">${icon("link", "sm")} Link</button>`}
      </div>`;
    })
    .join("");
}

// ------------------------------------------------------------ preview

function previewHtml(note, source) {
  return source.trim() ? renderNote(note, { source }) : '<p class="muted">Nothing written yet.</p>';
}

function refreshPreview(note, source) {
  const preview = $("#ed-preview");
  if (!preview) return;
  preview.innerHTML = previewHtml(note, source);
  hydrateAssets(preview, note.path);
}

function jumpTo(id, line) {
  const body = $("#ed-body");
  const preview = $("#ed-preview");
  const target = preview?.querySelector(`[data-heading-id="${CSS.escape(id)}"]`);
  if (target) target.scrollIntoView({ block: "start", behavior: "smooth" });
  if (body && line !== undefined && line !== null) {
    const lines = body.value.split("\n");
    const offset = lines.slice(0, Number(line)).reduce((sum, text) => sum + text.length + 1, 0);
    body.focus({ preventScroll: true });
    body.setSelectionRange(offset, offset);
    const lineHeight = parseFloat(getComputedStyle(body).lineHeight) || 22;
    body.scrollTop = Math.max(0, Number(line) * lineHeight - 40);
    if (!target) body.scrollIntoView({ block: "nearest" });
  }
}

// ------------------------------------------------------------ the drawer

export function renderDrawer() {
  $("#drawer-layer")?.remove();
  closeSuggest();
  const note = noteByPath(state.open);
  if (!note) return;
  const statuses = core.STATUSES[note.type];
  const backs = core.backlinks(state.notes, note.path).map(noteByPath).filter(Boolean);
  const created = typeof note.meta.created === "string" ? note.meta.created : "";
  const source = typeof note.meta.source === "string" ? note.meta.source : "";
  const isIdea = note.type === "idea" && !readOnly();
  if (readOnly()) state.editorMode = "preview";
  if (!["write", "split", "preview"].includes(state.editorMode)) state.editorMode = "write";
  const mode = state.editorMode;
  const lockedAttr = readOnly() ? "disabled" : "";
  const starred = isBookmarked(note.path);
  const words = wordCount(note.body);
  const layer = document.createElement("div");
  layer.id = "drawer-layer";
  const textarea = `<textarea id="ed-body" class="body-input" aria-label="Note body" spellcheck="true" placeholder="Write in Markdown. Type [[ to link another note, #tag to tag it.">${esc(note.body)}</textarea>`;
  const preview = `<div class="prose" id="ed-preview">${previewHtml(note, note.body)}</div>`;
  layer.innerHTML = `
    <div class="scrim" data-action="close-drawer"></div>
    <aside class="drawer${mode === "split" ? " wide" : ""}" role="dialog" aria-modal="true" aria-label="Edit ${esc(note.title)}">
      <div class="drawer-head">
        ${typePill(note.type)}
        <span class="path" title="brain/${esc(note.path)}">brain/${esc(note.path)}</span>
        <span class="save-state${state.held?.path === note.path ? " unsaved" : ""}" id="save-state">${readOnly() ? "Read-only" : state.held?.path === note.path ? "Not saved" : "Saved"}</span>
        <button class="btn ghost sm icon-only bookmark${starred ? " on" : ""}" data-action="bookmark" aria-pressed="${starred}" aria-label="${starred ? "Remove bookmark" : "Bookmark this note"}" title="${starred ? "Bookmarked" : "Bookmark"}">${icon("star")}</button>
        ${readOnly() ? "" : `<button class="btn ghost sm icon-only" data-action="rename" aria-label="Rename or move" title="Rename or move (links are updated)">${icon("pencil")}</button>`}
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
          ${propertiesHtml(note, lockedAttr)}
          ${readOnly() ? "" : `<span></span><div><button class="btn ghost sm add-prop" data-action="prop-add">${icon("plus", "sm")} Add property</button></div>`}
        </div>
        <div class="editor-tabs">
          ${readOnly() ? "" : `<div class="segmented" role="tablist" aria-label="Editor mode">
            <button class="${mode === "write" ? "on" : ""}" data-editor-mode="write" role="tab" aria-selected="${mode === "write"}">Write</button>
            <button class="${mode === "split" ? "on" : ""}" data-editor-mode="split" role="tab" aria-selected="${mode === "split"}" title="Write with a live preview">Split</button>
            <button class="${mode === "preview" ? "on" : ""}" data-editor-mode="preview" role="tab" aria-selected="${mode === "preview"}">Preview</button>
          </div>`}
          <span class="word-count" id="word-count">${plural(words, "word")}</span>
          <span class="grow"></span>
          ${isIdea ? `<button class="btn sm" data-action="spark">${icon("zap", "sm")} Spark</button><button class="btn sm primary" data-action="promote">${icon("arrow", "sm")} Promote to task</button>` : ""}
          ${readOnly() ? "" : `<button class="btn sm ghost danger icon-only" data-action="delete" aria-label="Delete note">${icon("trash", "sm")}</button>`}
        </div>
        ${mode === "write" ? textarea : mode === "split" ? `<div class="split-editor">${textarea}${preview}</div>` : preview}
        ${outlineHtml(note)}
        <div class="backlinks">
          <h3>Linked from</h3>
          ${backs.length ? backs.map((n) => rowItem(n, TYPE_META[n.type].label)).join("") : `<div class="empty">Nothing links here yet. Link to it from another note with <code>[[${esc(note.slug)}]]</code>.</div>`}
        </div>
        ${outgoingHtml(note)}
        ${mentionsHtml()}
      </div>
    </aside>`;
  $("#layer").appendChild(layer);
  wireDrawer(layer, note);
}

function wireDrawer(layer, note) {
  $("#ed-title", layer).addEventListener("input", (e) => queueChange({ title: e.target.value.trim() || note.slug }));
  $("#ed-tags", layer).addEventListener("input", (e) =>
    queueChange({ tags: e.target.value.split(",").map((t) => t.trim().replace(/^#/, "").toLowerCase()).filter(Boolean) }),
  );
  $("#ed-status", layer)?.addEventListener("change", async (e) => {
    await flushPending();
    await moveNote(note.path, e.target.value);
  });
  for (const input of layer.querySelectorAll("[data-prop]")) {
    input.addEventListener("input", () => queueChange({ properties: { [input.dataset.prop]: parseProperty(input.value, input.dataset.kind) } }));
  }

  const body = $("#ed-body", layer);
  if (body) wireBody(body, note);
  const preview = $("#ed-preview", layer);
  if (preview) {
    hydrateAssets(preview, note.path);
    preview.addEventListener("change", async (e) => {
      const box = e.target.closest("input[data-task]");
      if (!box || box.disabled || readOnly()) return;
      await flushPending();
      const current = noteByPath(note.path);
      const next = toggleTask(current.body, Number(box.dataset.task));
      await saveNote(note.path, { body: next });
      if (body) body.value = next;
      renderView();
    });
  }

  layer.addEventListener("click", async (e) => {
    const local = e.target.closest("[data-local][data-heading]");
    if (local) {
      e.preventDefault();
      return jumpTo(local.dataset.heading);
    }
    const footnote = e.target.closest("[data-footnote]");
    if (footnote) {
      e.preventDefault();
      return $(`#ed-preview [data-footnote-id="${CSS.escape(footnote.dataset.footnote)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    const jump = e.target.closest("[data-jump]");
    if (jump) return jumpTo(jump.dataset.jump, state.editorMode === "preview" ? null : jump.dataset.line);
    const remove = e.target.closest("[data-prop-remove]");
    if (remove && !readOnly()) {
      queueChange({ properties: { [remove.dataset.propRemove]: core.REMOVE } });
      await flushPending();
      return renderDrawer();
    }
    const link = e.target.closest("[data-link-mention]");
    if (link && !readOnly()) {
      const from = noteByPath(link.dataset.linkMention);
      if (!from) return;
      await flushPending();
      const updated = linkMention(from.body, link.dataset.phrase, linkTextFor(state.notes, from.path, note.path));
      if (updated) {
        await saveNote(from.path, { body: updated });
        toast(`Linked from ${from.title}`);
      }
      renderView();
      return renderDrawer();
    }
  });
  layer.addEventListener("toggle", (e) => {
    const details = e.target.closest?.("details[data-panel]");
    if (!details) return;
    PANELS[details.dataset.panel] = details.open;
    if (details.dataset.panel === "mentions" && details.open) fillMentions(note);
  }, true);
  if (PANELS.mentions) fillMentions(note);
  layer.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "e" && !readOnly()) {
      e.preventDefault();
      flushPending().then(() => {
        state.editorMode = state.editorMode === "preview" ? "write" : "preview";
        renderDrawer();
      });
    }
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
  if (state.scrollHeading) {
    const wanted = state.scrollHeading;
    state.scrollHeading = null;
    const found = headings(note.body).find((h) => h.id === wanted);
    setTimeout(() => jumpTo(wanted, state.editorMode === "preview" ? null : found?.line), 60);
  }
}

// ------------------------------------------------------------ the Markdown textarea

function wireBody(body, note) {
  const remember = () => {
    lastCaret = { path: note.path, start: body.selectionStart, end: body.selectionEnd };
  };
  body.addEventListener("input", () => {
    queueChange({ body: body.value });
    remember();
    $("#word-count").textContent = plural(wordCount(body.value), "word");
    if ($("#ed-preview")) {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(() => refreshPreview(note, body.value), 140);
    }
    updateSuggest(body, note);
  });
  body.addEventListener("keyup", remember);
  body.addEventListener("click", () => {
    remember();
    updateSuggest(body, note);
  });
  body.addEventListener("blur", () => setTimeout(() => {
    if (!suggest?.el.matches(":hover")) closeSuggest();
  }, 120));
  body.addEventListener("keydown", (e) => {
    if (suggest && handleSuggestKey(e, body)) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && !e.shiftKey && (e.key.toLowerCase() === "b" || e.key.toLowerCase() === "i")) {
      e.preventDefault();
      const mark = e.key.toLowerCase() === "b" ? "**" : "*";
      const { selectionStart: start, selectionEnd: end } = body;
      const selected = body.value.slice(start, end);
      body.setRangeText(`${mark}${selected}${mark}`, start, end, "select");
      body.setSelectionRange(start + mark.length, end + mark.length);
      body.dispatchEvent(new Event("input"));
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      body.setRangeText("  ", body.selectionStart, body.selectionEnd, "end");
      body.dispatchEvent(new Event("input"));
      return;
    }
    if (e.key === "Enter" && !e.shiftKey && !mod && continueList(body)) e.preventDefault();
  });
}

// Enter on a list item starts the next one; Enter on an empty item ends the list (like Obsidian).
function continueList(body) {
  const { selectionStart: pos, selectionEnd: end, value } = body;
  if (pos !== end) return false;
  const lineStart = value.lastIndexOf("\n", pos - 1) + 1;
  const lineEnd = value.indexOf("\n", pos) === -1 ? value.length : value.indexOf("\n", pos);
  if (lineEnd !== pos) return false;
  const line = value.slice(lineStart, pos);
  const match = /^(\s*(?:>\s*)*)([-*+]|(\d+)([.)]))(\s+)(\[[ xX]\]\s+)?(.*)$/.exec(line);
  if (!match) return false;
  const [, indent, marker, number, dot, gap, task, content] = match;
  if (!content.trim()) {
    body.setRangeText("", lineStart, pos, "end");
    body.dispatchEvent(new Event("input"));
    return true;
  }
  const nextMarker = number ? `${Number(number) + 1}${dot}` : marker;
  body.setRangeText(`\n${indent}${nextMarker}${gap}${task ? "[ ] " : ""}`, pos, pos, "end");
  body.dispatchEvent(new Event("input"));
  return true;
}

// ------------------------------------------------------------ [[ link suggestions

let suggest = null;

function closeSuggest() {
  suggest?.el.remove();
  suggest = null;
}

function linkQuery(body) {
  const pos = body.selectionStart;
  if (pos !== body.selectionEnd) return null;
  const before = body.value.slice(0, pos);
  const open = before.lastIndexOf("[[");
  if (open === -1) return null;
  const query = before.slice(open + 2);
  if (/[\]\n|]/.test(query) || query.length > 100) return null;
  return { start: open + 2, end: pos, query };
}

function suggestions(query, note) {
  const hash = query.indexOf("#");
  if (hash !== -1) {
    const name = query.slice(0, hash).trim();
    const target = name ? resolveFrom(note.path)(name) : note;
    if (!target) return [];
    const wanted = query.slice(hash + 1).trim().toLowerCase();
    return headings(target.body)
      .filter((h) => !wanted || h.text.toLowerCase().includes(wanted))
      .slice(0, 8)
      .map((h) => ({ label: h.text, detail: `${"#".repeat(h.level)} in ${target.title}`, insert: `${name}#${h.text}` }));
  }
  const q = query.trim().toLowerCase();
  const scored = state.notes
    .filter((n) => n.path !== note.path)
    .map((n) => {
      const title = n.title.toLowerCase();
      const file = n.path.split("/").pop().slice(0, -3).toLowerCase();
      const score = !q ? 1 : title.startsWith(q) || file.startsWith(q) ? 3 : title.includes(q) || file.includes(q) ? 2 : n.path.toLowerCase().includes(q) ? 1 : 0;
      return { n, score };
    })
    .filter((r) => r.score > 0)
    .sort((a, b) => b.score - a.score || (b.n.mtime || 0) - (a.n.mtime || 0))
    .slice(0, 8)
    .map(({ n }) => {
      const text = linkTextFor(state.notes, note.path, n.path);
      const alias = n.title && n.title !== text.split("/").pop() ? `|${n.title}` : "";
      return { label: n.title, detail: n.path, insert: `${text}${alias}`, type: n.type };
    });
  if (q && !scored.some((s) => s.label.toLowerCase() === q)) {
    scored.push({ label: query.trim(), detail: "New note: link it now, create it later", insert: query.trim(), create: true });
  }
  return scored;
}

function caretPoint(body, position) {
  const style = getComputedStyle(body);
  const mirror = document.createElement("div");
  for (const prop of ["boxSizing", "width", "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "fontStyle", "fontVariant", "fontWeight", "fontSize", "lineHeight", "fontFamily", "letterSpacing", "wordSpacing", "tabSize", "textIndent"]) {
    mirror.style[prop] = style[prop];
  }
  Object.assign(mirror.style, { position: "absolute", visibility: "hidden", whiteSpace: "pre-wrap", overflowWrap: "break-word", top: "0", left: "-9999px" });
  mirror.textContent = body.value.slice(0, position);
  const marker = document.createElement("span");
  marker.textContent = "\u200b";
  mirror.appendChild(marker);
  document.body.appendChild(mirror);
  const rect = body.getBoundingClientRect();
  const lineHeight = parseFloat(style.lineHeight) || 22;
  const point = { top: rect.top + marker.offsetTop - body.scrollTop + lineHeight, left: rect.left + marker.offsetLeft - body.scrollLeft };
  mirror.remove();
  return point;
}

function updateSuggest(body, note) {
  const context = linkQuery(body);
  if (!context) return closeSuggest();
  const items = suggestions(context.query, note);
  if (!items.length) return closeSuggest();
  if (!suggest) {
    const el = document.createElement("div");
    el.className = "link-suggest";
    el.setAttribute("role", "listbox");
    el.setAttribute("aria-label", "Link suggestions");
    document.body.appendChild(el);
    el.addEventListener("mousedown", (e) => {
      const option = e.target.closest("[data-suggest]");
      if (!option) return;
      e.preventDefault();
      suggest.active = Number(option.dataset.suggest);
      acceptSuggest(body);
    });
    suggest = { el, active: 0, items: [], context };
  }
  suggest.items = items;
  suggest.context = context;
  suggest.active = Math.min(suggest.active, items.length - 1);
  suggest.el.innerHTML = items
    .map((item, index) => `<button type="button" class="suggest-item${index === suggest.active ? " on" : ""}${item.create ? " create" : ""}" data-suggest="${index}" role="option" aria-selected="${index === suggest.active}">
        ${icon(item.create ? "plus" : item.type ? TYPE_META[item.type].icon : "list", "sm")}
        <span class="label">${esc(item.label)}</span><span class="detail">${esc(item.detail)}</span></button>`)
    .join("") + `<div class="suggest-hint"><kbd>↑</kbd><kbd>↓</kbd> choose · <kbd>Enter</kbd> link · type <kbd>#</kbd> for a heading</div>`;
  const point = caretPoint(body, context.start);
  const width = Math.min(380, window.innerWidth - 16);
  const height = suggest.el.offsetHeight || 260;
  const top = point.top + height > window.innerHeight - 8 ? Math.max(8, point.top - height - 26) : point.top + 4;
  suggest.el.style.width = `${width}px`;
  suggest.el.style.left = `${Math.max(8, Math.min(point.left - 12, window.innerWidth - width - 8))}px`;
  suggest.el.style.top = `${top}px`;
}

function handleSuggestKey(e, body) {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const step = e.key === "ArrowDown" ? 1 : -1;
    suggest.active = (suggest.active + step + suggest.items.length) % suggest.items.length;
    updateSuggest(body, noteByPath(state.open));
    return true;
  }
  if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    acceptSuggest(body);
    return true;
  }
  if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    closeSuggest();
    return true;
  }
  return false;
}

function acceptSuggest(body) {
  const item = suggest?.items[suggest.active];
  const context = linkQuery(body) || suggest?.context;
  if (!item || !context) return closeSuggest();
  const closing = body.value.slice(context.end, context.end + 2) === "]]";
  body.setRangeText(`${item.insert}]]`, context.start, context.end + (closing ? 2 : 0), "end");
  closeSuggest();
  body.focus();
  body.dispatchEvent(new Event("input"));
}

// ------------------------------------------------------------ actions on the open note

export async function renameOpenNote() {
  const note = noteByPath(state.open);
  if (!note || readOnly()) return;
  const value = await ask({
    title: "Rename or move",
    text: "Type a new name, or folder/name to move it. Every link to this note is updated.",
    value: note.path.replace(/\.md$/i, ""),
    confirm: "Rename",
  });
  if (!value) return;
  let target;
  try {
    target = normalizeNotePath(value, note.path);
  } catch (error) {
    return toast(error.message, { error: true });
  }
  if (target === note.path) return;
  await flushPending();
  const result = await renameNote(note.path, target);
  state.open = result.path;
  renderDrawer();
  render();
  toast(`Renamed to brain/${result.path}${result.updated ? `. Updated links in ${plural(result.updated, "note")}.` : "."}`);
}

export function bookmarkOpenNote(path = state.open) {
  if (!noteByPath(path)) return;
  const on = toggleBookmark(path);
  toast(on ? "Bookmarked. Find it under Notes › Bookmarks." : "Bookmark removed");
  if (state.open === path) renderDrawer();
  renderView();
}

// Insert text at the caret of the open note (templates), or at the end.
export async function insertIntoOpenNote(text) {
  const note = noteByPath(state.open);
  if (!note || readOnly()) return false;
  await flushPending();
  const current = noteByPath(note.path).body;
  const at = lastCaret?.path === note.path ? Math.min(lastCaret.start, current.length) : current.length;
  const before = current.slice(0, at);
  const glue = before && !before.endsWith("\n") ? "\n" : "";
  const next = `${before}${glue}${text}${current.slice(at)}`;
  await saveNote(note.path, { body: next });
  lastCaret = { path: note.path, start: at + glue.length + text.length, end: at + glue.length + text.length };
  renderDrawer();
  return true;
}

export async function addProperty() {
  const note = noteByPath(state.open);
  if (!note || readOnly()) return;
  const name = await ask({ title: "Add a property", text: "A front-matter field, like status, due, aliases or url.", placeholder: "property name", confirm: "Add" });
  const key = (name || "").trim().replace(/\s+/g, "_");
  if (!key) return;
  if (!/^[\p{L}\p{N}_-]+$/u.test(key)) return toast("Use letters, numbers, - or _ for a property name.", { error: true });
  if (key in note.meta || BUILT_IN.has(key)) return toast(`“${key}” is already a property.`, { error: true });
  await flushPending();
  await saveNote(note.path, { properties: { [key]: "" } });
  renderDrawer();
  setTimeout(() => $(`[data-prop="${CSS.escape(key)}"]`)?.focus(), 40);
}

export async function createMissing(target) {
  if (readOnly()) return toast("This brain is read-only. Ask to join to contribute.");
  const title = target.split("/").pop().replace(/\.md$/i, "").trim();
  if (!title) return;
  await flushPending();
  const note = await createNote({ type: "note", title });
  render();
  openNote(note.path);
  toast(`Created brain/${note.path}`);
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
