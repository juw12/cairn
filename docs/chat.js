// Chat: type, send, see the answer. Talks to the OpenCode models on the PC (your company build
// included) through the local server, so it works from a paired phone too. The Glow tab opens your
// own Glow app (Chat, Council, Arena) inside Cairn.

import { escapeHtml as esc, renderMarkdown } from "./md.js";
import { $, icon, toast } from "./ui.js";
import { state } from "./state.js";

export const GLOW_URL = "https://juw12.github.io/projects/glow/app.html";
const MODEL_KEY = "cairn.chatModel";
const POLL_MS = 1000;

let pollTimer = null;

const canChat = () => typeof state.store?.chat === "function";
const chatState = () => state.chat || (state.chat = { list: null, models: [], available: true, current: "", thread: null, showList: false });

function savedModel(models) {
  const saved = localStorage.getItem(MODEL_KEY) || "";
  if (models.includes(saved)) return saved;
  return models.find((m) => /chat/i.test(m)) || models[0] || "";
}

function timeAgo(seconds) {
  if (!seconds) return "";
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

// ============================================================ rendering

function messageHtml(message) {
  if (message.role === "user") {
    return `<div class="chat-msg user"><div class="bubble">${esc(message.text)}</div></div>`;
  }
  const body = message.text
    ? renderMarkdown(message.text, () => null)
    : `<p class="muted">Thinking…</p>`;
  const model = message.model ? `<span class="chat-model-tag">${esc(message.model.split("/").pop())}</span>` : "";
  return `<div class="chat-msg assistant${message.pending ? " pending" : ""}"><div class="bubble prose">${body}</div>${model}</div>`;
}

function logHtml(thread) {
  if (!thread || !thread.messages.length) {
    return `<div class="chat-empty">
      <h2>Ask anything</h2>
      <p class="muted">Your message goes to OpenCode on your PC and the answer appears here, on whichever device you are using.</p>
    </div>`;
  }
  return thread.messages.map(messageHtml).join("");
}

function listHtml(c) {
  const items = (c.list || []).map((item) => `
    <button type="button" class="chat-item${item.id === c.current ? " active" : ""}" data-chat-open="${esc(item.id)}">
      <span class="chat-item-title">${esc(item.title || "New chat")}</span>
      <span class="chat-item-meta">${item.status === "running" ? "answering…" : timeAgo(item.updated)}</span>
    </button>`);
  return items.join("") || `<p class="muted chat-none">No chats yet.</p>`;
}

export function chatView() {
  const c = chatState();
  if (!canChat()) {
    return `<div class="chat-unavailable"><h1>Chat</h1><p class="muted">Chat runs the AI models on your PC, so it needs the Cairn desktop app (or a phone paired to it).</p>
      <p><a class="btn" data-go="glow" href="#/glow">${icon("sparkle", "sm")} Open Glow instead</a></p></div>`;
  }
  if (c.list === null) refreshList();
  const models = c.models;
  const model = savedModel(models);
  const running = c.thread?.status === "running";
  const options = models.map((m) => `<option value="${esc(m)}"${m === model ? " selected" : ""}>${esc(m)}</option>`).join("");
  const notice = c.available
    ? ""
    : `<div class="chat-notice">OpenCode isn't installed on this PC, so there's no model to talk to yet.</div>`;
  return `
    <div class="chat-page${c.showList ? " show-list" : ""}">
      <aside class="chat-list" aria-label="Chats">
        <button type="button" class="btn primary chat-new" data-chat="new">${icon("plus", "sm")} New chat</button>
        <div class="chat-items">${listHtml(c)}</div>
      </aside>
      <section class="chat-main">
        <header class="chat-head">
          <button type="button" class="btn sm ghost chat-list-toggle" data-chat="toggle-list" aria-label="Show chats">${icon("chat", "sm")}</button>
          <h1 class="chat-title">${esc(c.thread?.title || "New chat")}</h1>
          <select id="chat-model" class="chat-model" aria-label="Model" ${models.length ? "" : "disabled"}>${options || "<option>No models</option>"}</select>
          ${c.thread ? `<button type="button" class="btn sm ghost" data-chat="delete" aria-label="Delete chat" title="Delete chat">${icon("trash", "sm")}</button>` : ""}
        </header>
        ${notice}
        <div class="chat-log" id="chat-log">${logHtml(c.thread)}</div>
        <form class="chat-form" id="chat-form">
          <textarea id="chat-input" rows="1" placeholder="Message ${esc(model.split("/").pop() || "OpenCode")}…" ${c.available ? "" : "disabled"}></textarea>
          ${running
            ? `<button type="button" class="btn" data-chat="stop">Stop</button>`
            : `<button type="submit" class="btn primary" ${c.available ? "" : "disabled"}>Send</button>`}
        </form>
      </section>
    </div>`;
}

export function glowView() {
  return `
    <div class="glow-page">
      <div class="glow-bar">
        <span><strong>Glow</strong> <span class="muted">Chat · Council · Arena</span></span>
        <a class="btn sm" href="${GLOW_URL}" target="_blank" rel="noopener">${icon("external", "sm")} Open in a new tab</a>
      </div>
      <iframe class="glow-frame" src="${GLOW_URL}" title="Glow" allow="clipboard-write; microphone"></iframe>
    </div>`;
}

// Re-render just the moving parts, so the message box keeps what you are typing.
function paint() {
  const c = chatState();
  const log = $("#chat-log");
  if (!log) return;
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  log.innerHTML = logHtml(c.thread);
  if (nearBottom) log.scrollTop = log.scrollHeight;
  const items = $(".chat-items");
  if (items) items.innerHTML = listHtml(c);
  const title = $(".chat-title");
  if (title) title.textContent = c.thread?.title || "New chat";
  const form = $("#chat-form");
  const running = c.thread?.status === "running";
  const button = form?.querySelector("button");
  if (button && (button.dataset.chat === "stop") !== running) {
    button.outerHTML = running
      ? `<button type="button" class="btn" data-chat="stop">Stop</button>`
      : `<button type="submit" class="btn primary">Send</button>`;
  }
}

// ============================================================ data

async function refreshList() {
  const c = chatState();
  try {
    const data = await state.store.chat();
    c.list = data.chats;
    c.models = data.models;
    c.available = data.available;
    if (state.view === "chat") {
      const select = $("#chat-model");
      if (select && select.options.length !== data.models.length) {
        const model = savedModel(data.models);
        select.innerHTML = data.models.map((m) => `<option value="${esc(m)}"${m === model ? " selected" : ""}>${esc(m)}</option>`).join("");
        select.disabled = !data.models.length;
      }
      paint();
    }
  } catch (error) {
    c.list = c.list || [];
    toast(error.message);
  }
}

async function openChat(id) {
  const c = chatState();
  c.current = id;
  c.showList = false;
  $(".chat-page")?.classList.remove("show-list");
  try {
    c.thread = await state.store.chatThread(id);
  } catch (error) {
    toast(error.message);
    c.thread = null;
  }
  paint();
  const log = $("#chat-log");
  if (log) log.scrollTop = log.scrollHeight;
  schedulePoll();
}

function newChat() {
  const c = chatState();
  c.current = "";
  c.thread = null;
  c.showList = false;
  $(".chat-page")?.classList.remove("show-list");
  paint();
  $("#chat-input")?.focus();
}

async function sendMessage() {
  const c = chatState();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  const model = $("#chat-model")?.value || "";
  input.value = "";
  autoGrow(input);
  try {
    c.thread = await state.store.chatAction("send", { id: c.current, text, model });
    c.current = c.thread.id;
    paint();
    const log = $("#chat-log");
    if (log) log.scrollTop = log.scrollHeight;
    refreshList();
    schedulePoll();
  } catch (error) {
    input.value = text;
    toast(error.message);
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  const c = chatState();
  if (c.thread?.status !== "running") return;
  pollTimer = setTimeout(async () => {
    if (state.view !== "chat" || !c.current) return;
    try {
      const thread = await state.store.chatThread(c.current);
      if (thread.id !== c.current) return;
      const finished = c.thread?.status === "running" && thread.status !== "running";
      c.thread = thread;
      paint();
      if (finished) refreshList();
    } catch {
      /* the PC may be asleep for a moment; try again */
    }
    schedulePoll();
  }, POLL_MS);
}

function autoGrow(input) {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
}

// ============================================================ events

export function wireChat() {
  const page = $(".chat-page");
  if (!page) return;
  const c = chatState();
  page.addEventListener("click", async (event) => {
    const open = event.target.closest("[data-chat-open]");
    if (open) return openChat(open.dataset.chatOpen);
    const action = event.target.closest("[data-chat]")?.dataset.chat;
    if (action === "new") return newChat();
    if (action === "toggle-list") {
      c.showList = !c.showList;
      page.classList.toggle("show-list", c.showList);
      return;
    }
    if (action === "stop" && c.current) {
      await state.store.chatAction("stop", { id: c.current }).catch((error) => toast(error.message));
      return;
    }
    if (action === "delete" && c.current) {
      if (!confirm("Delete this chat?")) return;
      await state.store.chatAction("delete", { id: c.current }).catch((error) => toast(error.message));
      newChat();
      refreshList();
    }
  });
  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });
  const input = $("#chat-input");
  input.addEventListener("input", () => autoGrow(input));
  input.addEventListener("keydown", (event) => {
    // Enter sends on a computer; on a phone the keyboard's return adds a line and the button sends.
    if (event.key === "Enter" && !event.shiftKey && !matchMedia("(pointer: coarse)").matches) {
      event.preventDefault();
      sendMessage();
    }
  });
  $("#chat-model")?.addEventListener("change", (event) => {
    localStorage.setItem(MODEL_KEY, event.target.value);
    input.placeholder = `Message ${event.target.value.split("/").pop()}…`;
  });
  const log = $("#chat-log");
  if (log) log.scrollTop = log.scrollHeight;
  if (c.current && !c.thread) openChat(c.current);
  else schedulePoll();
}
