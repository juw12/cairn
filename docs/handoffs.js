// Between computers: the conversation the computers that share this brain have with each other. One leaves a
// message for one, several or all of the others (what it found, what is left hanging, what only another
// computer can do); the others say "picked up", answer, and say "done". The block on Today shows what waits
// for this computer first. The rules are in core.js (shared with core.py and the `cairn handoff` command);
// every message is its own file in handoffs/, written once, so the brain's sync has nothing to merge.

import * as core from "./core.js";
import { escapeHtml as esc, renderMarkdown } from "./md.js";
import { $, icon, plural, toast, today } from "./ui.js";
import { fileByPath, readOnly, scheduleGitRefresh, state, upsertFile } from "./state.js";
import { minutesText } from "./recap.js";
import { deviceList } from "./progress.js";
import { renderView } from "./app.js";

const WHO_KEY = "cairn.handoffName";
const SETTLED_SHOWN = 30;
const TITLE_LIMIT = 80;
const SAID = { "picked-up": "Picked up", done: "Done" };
const STATE_WORDS = { waiting: "has not picked it up", "picked-up": "picked it up", done: "done" };

const ui = () => state.handoffs || (state.handoffs = { open: null, writing: false, settled: false, draft: { title: "", body: "", reply: "" } });

const pad = (n) => String(n).padStart(2, "0");
function stampNow() {
  const d = new Date();
  return `${today()}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function ago(stamp) {
  const at = core.stampMinutes(stamp);
  const now = core.stampMinutes(stampNow());
  if (at === null || now === null) return "";
  const minutes = Math.max(0, now - at);
  return minutes < 2 ? "just now" : `${minutesText(minutes)} ago`;
}

/** This computer's name in the brain, or "" where the app does not run on one (the web app, a phone). */
export function thisComputer() {
  return state.vibeHere || state.info?.device || "";
}

// Who a message written here is from: this computer, or on the web app a name the person can keep.
function writerName() {
  const here = thisComputer();
  if (here) return here;
  let kept = "";
  try {
    kept = localStorage.getItem(WHO_KEY) || "";
  } catch {
    /* storage is off */
  }
  return kept.trim() || (window.matchMedia?.("(pointer: coarse)").matches ? "Phone" : "Web app");
}

/** Every computer the brain has heard of, one spelling each: session notes, machines/ notes, handoffs. */
export function knownComputers(threads) {
  const found = new Map();
  const add = (name) => {
    const key = core.deviceKey(name);
    if (key && key !== core.HANDOFF_EVERYONE && !found.has(key)) found.set(key, String(name).trim());
  };
  for (const device of deviceList(state.notes)) add(device.name);
  for (const thread of threads) {
    add(thread.from);
    thread.to.forEach(add);
    for (const part of thread.parts) add(part.device);
  }
  return [...found.values()].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
}

const forWhom = (thread) => (thread.everyone ? "every computer" : thread.to.join(", ") || "itself");

// ------------------------------------------------------------ drawing

function messageHtml(message, thread, me) {
  const file = fileByPath(message.path);
  const body = file ? core.parseNote(file.text).body.trim() : "";
  const first = message.path === thread.path;
  const mark = SAID[message.says] ? `<span class="hr-badge${message.says === "done" ? " ho-done" : " warn"}">${SAID[message.says]}</span>` : "";
  const showTitle = !first && message.title && message.title !== SAID[message.says];
  const mine = me && core.deviceKey(message.from) === me;
  return `<li class="ho-msg${mine ? " own" : ""}">
    <p class="ho-who"><strong>${esc(message.from || "Someone")}</strong>${mine ? ` <span class="muted">this computer</span>` : ""} <span class="muted">· ${esc(ago(message.at) || message.at.replace("T", " ").slice(0, 16))}</span> ${mark}
      <button type="button" class="link-button ho-file" data-open="${esc(message.path)}">Open the note</button></p>
    ${showTitle ? `<p class="ho-line">${esc(message.title)}</p>` : ""}
    ${body ? `<div class="prose ho-body">${renderMarkdown(body, (target) => core.resolveLink(state.notes, target))}</div>` : ""}
  </li>`;
}

function threadHtml(thread, here) {
  const me = core.deviceKey(here);
  const waits = here ? core.handoffWaitsFor(thread, here) : "";
  const mineToClose = Boolean(me) && me === core.deviceKey(thread.from);
  const open = ui().open === thread.id;
  const count = thread.messages.length;
  const tone = !thread.open ? "idle" : waits === "waiting" ? "task" : waits === "picked-up" ? "warn" : "idle";
  const badge = waits === "waiting" ? `<span class="hr-badge ho-new">New</span> ` : waits === "picked-up" ? `<span class="hr-badge warn">Picked up</span> ` : "";
  // Who it is between, said as shortly as the place in the list allows: the long names of computers add up.
  const between = waits ? `from ${thread.from || "someone"}` : mineToClose ? `to ${forWhom(thread)}` : `${thread.from || "Someone"} → ${forWhom(thread)}`;
  const meta = [between, ago(thread.last), count > 1 ? `${count} messages` : ""].filter(Boolean).join(" · ");
  const row = `<button type="button" class="home-row ho-row" data-action="handoff-open" data-thread="${esc(thread.id)}" aria-expanded="${open}">
      <span class="hr-dot ${tone}" aria-hidden="true"></span>
      <span class="hr-main"><span class="hr-title">${badge}${esc(thread.title)}</span><span class="hr-meta">${esc(meta)}</span></span>
    </button>`;
  if (!open) return `<li class="ho-thread">${row}</li>`;

  const parts = thread.parts.length
    ? `<p class="ho-parts muted small">${thread.parts.map((part) => `${esc(part.device)}: ${STATE_WORDS[part.state]}`).join(" · ")}</p>`
    : thread.everyone
      ? `<p class="ho-parts muted small">For every computer. Nobody has picked it up yet.</p>`
      : "";
  let actions = "";
  if (!readOnly() && thread.open) {
    const say = (says, label, primary) =>
      `<button type="button" class="btn sm${primary ? " primary" : ""}" data-action="handoff-say" data-thread="${esc(thread.id)}" data-says="${says}">${label}</button>`;
    const buttons = [
      waits === "waiting" ? say("picked-up", "Picked up", true) : "",
      waits ? say("done", "Done", waits === "picked-up") : "",
      mineToClose ? say("done", "Settled, close it", false) : "",
    ].join("");
    actions = `<form class="ho-reply" data-handoff-reply="${esc(thread.id)}">
        <textarea class="input" id="handoff-reply" name="text" rows="2" placeholder="Answer, or say what you did" aria-label="Your answer">${esc(ui().draft.reply)}</textarea>
        <div class="ho-buttons">${buttons}<button type="submit" class="btn sm">${icon("chat", "sm")} Answer</button></div>
      </form>`;
  }
  return `<li class="ho-thread is-open">${row}
    <div class="ho-open">
      <ol class="ho-msgs">${thread.messages.map((message) => messageHtml(message, thread, me)).join("")}</ol>
      ${parts}
      ${thread.open ? "" : `<p class="ho-parts muted small">Settled.</p>`}
      ${actions}
    </div></li>`;
}

function formHtml(others) {
  const draft = ui().draft;
  const only = others.length === 1;
  const box = (value, label, checked) =>
    `<label class="ho-chip"><input type="checkbox" name="to" value="${esc(value)}"${checked ? " checked" : ""}> <span>${esc(label)}</span></label>`;
  return `<form id="handoff-form" class="ho-form">
    <div class="ho-to" role="group" aria-label="Who it is for">
      <span class="muted small">For</span>
      ${others.map((name) => box(name, name, only)).join("")}
      ${others.length > 1 || !others.length ? box(core.HANDOFF_EVERYONE, "Every computer", !others.length) : ""}
    </div>
    <input class="input" id="handoff-title" name="title" maxlength="${TITLE_LIMIT}" placeholder="What should it know, check or do?" aria-label="The message, in one line" value="${esc(draft.title)}" required>
    <textarea class="input" id="handoff-body" name="body" rows="3" placeholder="The details: what you found, what is left, where the files are. Optional." aria-label="Details">${esc(draft.body)}</textarea>
    <div class="ho-buttons">
      <button type="submit" class="btn sm primary">${icon("arrow", "sm")} Leave it for them</button>
      <button type="button" class="btn sm ghost" data-action="handoff-write">Cancel</button>
      <span class="muted small">From ${esc(writerName())}. It arrives with the brain's next sync.</span>
    </div>
  </form>`;
}

/** The "Between computers" block of Today: { html, summary }, or null in a brain only one computer writes. */
export function handoffsBlock() {
  const threads = core.handoffThreads(state.notes);
  const here = thisComputer();
  const others = knownComputers(threads).filter((name) => core.deviceKey(name) !== core.deviceKey(here));
  if (!threads.length && !others.length) return null;
  const byId = new Map(threads.map((thread) => [thread.id, thread]));
  const inbox = core.handoffInbox(threads, here);
  const list = (ids) => `<ul class="home-list ho-list">${ids.map((id) => threadHtml(byId.get(id), here)).join("")}</ul>`;
  const section = (title, ids) => (ids.length ? `<section class="ho-section"><h3 class="ho-head">${title} <span class="muted">${ids.length}</span></h3>${list(ids)}</section>` : "");

  const openIds = [...inbox.mine, ...inbox.asked, ...inbox.others];
  const parts = [
    here ? section("For this computer", inbox.mine) : "",
    section(here ? "This computer is waiting for" : "Open", inbox.asked),
    section(here ? "Between other computers" : "Open between your computers", inbox.others),
    !openIds.length ? `<p class="muted small ho-empty">${here ? "Nothing is waiting for this computer, and it is waiting for nothing." : "Nothing is open between your computers."}</p>` : "",
    ui().settled ? section("Settled", inbox.settled.slice(0, SETTLED_SHOWN)) : "",
  ];
  const tools = [
    readOnly() || ui().writing ? "" : `<button type="button" class="btn sm" data-action="handoff-write">${icon("plus", "sm")} Leave a message for another computer</button>`,
    inbox.settled.length ? `<button type="button" class="text-link" data-action="handoff-settled">${ui().settled ? "Hide" : "Show"} ${inbox.settled.length} settled</button>` : "",
  ].filter(Boolean);
  const summary = [
    here && inbox.mine.length ? `${inbox.mine.length} for this computer` : "",
    inbox.asked.length ? `waiting for ${plural(inbox.asked.length, "answer")}` : "",
    inbox.others.length ? `${inbox.others.length} between other computers` : "",
  ].filter(Boolean);
  return {
    html: `<div class="handoffs" id="handoffs">
      ${parts.join("")}
      ${!readOnly() && ui().writing ? formHtml(others) : ""}
      ${tools.length ? `<p class="ho-tools">${tools.join("")}</p>` : ""}
    </div>`,
    summary: summary.join(" · ") || "nothing open",
  };
}

// ------------------------------------------------------------ actions

export function toggleHandoff(id) {
  ui().open = ui().open === id ? null : id;
  ui().draft.reply = "";
  renderView();
}

export function toggleHandoffForm() {
  ui().writing = !ui().writing;
  renderView();
  if (ui().writing) $("#handoff-title")?.focus();
}

export function toggleSettled() {
  ui().settled = !ui().settled;
  renderView();
}

async function write(message) {
  const { mtime } = await state.store.write(message.path, message.text, null);
  upsertFile(message.path, message.text, mtime);
  scheduleGitRefresh();
}

const paths = () => state.files.map((file) => file.path);

// A message's title is its first line; a longer text is also its body.
function titleAndBody(text, fallback) {
  const whole = String(text || "").trim();
  const first = whole.split("\n", 1)[0].trim().split(/\s+/).join(" ");
  if (!first) return { title: fallback, body: "" };
  const title = first.length <= TITLE_LIMIT ? first : `${first.slice(0, TITLE_LIMIT - 3).trimEnd()}...`;
  return { title, body: whole === title ? "" : whole };
}

/** Start a conversation from the form on Today. */
export async function sendHandoff(form) {
  if (readOnly()) return;
  const to = [...form.querySelectorAll('input[name="to"]:checked')].map((box) => box.value);
  const title = form.title.value.trim();
  if (!title) return;
  if (!to.length) {
    toast("Tick who it is for.", { error: true });
    return;
  }
  try {
    await write(core.handoffMessage(writerName(), title, form.body.value, stampNow(), paths(), { to }));
  } catch (error) {
    toast(error.message || "Could not save the message.", { error: true });
    return;
  }
  Object.assign(ui(), { writing: false, draft: { title: "", body: "", reply: "" } });
  renderView();
  toast("Left for them. It arrives with the brain's next sync.");
}

/** Add to a conversation: an answer (says "note"), "picked-up" or "done". The text in the answer box goes along. */
export async function sayHandoff(threadId, says, text = null) {
  if (readOnly()) return;
  const typed = text ?? $("#handoff-reply")?.value ?? "";
  const { title, body } = titleAndBody(typed, SAID[says] || "");
  if (!title) {
    $("#handoff-reply")?.focus();
    return;
  }
  try {
    await write(core.handoffMessage(writerName(), title, body, stampNow(), paths(), { thread: threadId, says }));
  } catch (error) {
    toast(error.message || "Could not save the message.", { error: true });
    return;
  }
  ui().draft.reply = "";
  renderView();
}

// What is typed stays through a redraw of the page (a sync, a live session ticking).
document.addEventListener("input", (event) => {
  const field = event.target;
  if (field.id === "handoff-title") ui().draft.title = field.value;
  else if (field.id === "handoff-body") ui().draft.body = field.value;
  else if (field.id === "handoff-reply") ui().draft.reply = field.value;
});
