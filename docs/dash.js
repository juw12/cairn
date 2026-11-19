// Dashboards on your phone. The PC (cairn dashboards publish) puts the newest copy of each local
// dashboard on the `dashboards` branch of your private relay repo; this page reads it straight from
// GitHub, so it works on any network. Opened from the QR code, the #fragment carries the PC's GitHub
// sign-in (fragments never reach a server); it is stored like Cairn's own sign-in, then removed from
// the address bar. The relay repo stays in the address (?repo=, not secret), so an iPhone home-screen
// icon added from this page opens the right repo; its separate storage gets the sign-in from the cookie
// auth.js mirrors (copied by iOS when you add the icon) or, failing that, a pasted sign-in link.
//
// Each dashboard runs in its own sandboxed frame without same-origin rights, so its scripts can
// never read the sign-in. A tiny script added to each page keeps its scroll position and what it
// remembers (open tab, folded blocks), so a live update lands where you were reading.
//
// A dashboard whose PC half takes actions (status.json: "actions": true) can also press its buttons
// from here: its frame asks this page, this page writes the request to the relay repo, and
// `cairn dashboards actions` on the PC runs it and answers (see "buttons that run on the PC" below).

import { AuthError, authFetch, session, signInWithToken } from "./auth.js";
import { encodeBase64 } from "./github.js";

const API = "https://api.github.com";
const BRANCH = "dashboards";
const POLL_MS = 30_000;
const REPO_KEY = "cairn.dash.repo";
const TAB_KEY = "cairn.dash.tab";
const PAGE_KEY = (id) => `cairn.dash.page.${id}`;
const REPO_NAME = /^[\w.-]+\/[\w.-]+$/;

const $ = (selector) => document.querySelector(selector);
const tabsEl = $("[data-tabs]");
const statusEl = $("[data-status]");
const mainEl = $("[data-main]");
const refreshEl = $("[data-refresh]");

let repo = "";
let etag = "";
let status = null;
let current = localStorage.getItem(TAB_KEY) || "";
let lastCheck = 0;
let lastError = "";
const frames = new Map(); // id -> { iframe, sha, unseen }

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function ago(iso) {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

function message(html) {
  for (const frame of frames.values()) frame.iframe.remove();
  frames.clear();
  tabsEl.innerHTML = "";
  statusEl.hidden = true;
  mainEl.innerHTML = `<div class="message">${html}</div>`;
}

const homeScreenApp = () => navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;

// A pasted sign-in link (…dash.html?repo=…#signin=…&repo=…) or a bare token.
export function readSignIn(text) {
  const value = String(text || "").trim();
  const at = value.indexOf("#");
  const fragment = new URLSearchParams(at >= 0 ? value.slice(at + 1) : "");
  const query = new URLSearchParams(value.includes("?") ? value.slice(value.indexOf("?") + 1, at >= 0 ? at : undefined) : "");
  const found = fragment.get("signin");
  const named = fragment.get("repo") || query.get("repo") || "";
  return { token: found || (at >= 0 || value.includes("/") ? "" : value), repo: REPO_NAME.test(named) ? named : "" };
}

function signedOutView(reason = "") {
  message(`<h1>Dashboards on your phone</h1>
    ${reason ? `<p>${esc(reason)}</p>` : ""}
    ${homeScreenApp()
      ? `<p>This home-screen app keeps its own storage, so it needs the sign-in once. Paste the sign-in link
          (on your PC: <code>cairn dashboards link</code>), or open the QR code in Safari, then remove this icon and add it again.</p>`
      : `<p>Scan the QR code from your PC: Cairn &rsaquo; Settings &rsaquo; Phone &rsaquo; <strong>Dashboards on phone</strong>,
          or run <code>cairn dashboards link</code>.</p>`}
    <form class="paste" data-paste>
      <input name="link" type="text" inputmode="url" autocomplete="off" autocapitalize="off" spellcheck="false"
        placeholder="Paste the sign-in link" aria-label="Sign-in link">
      <button type="submit">Sign in</button>
    </form>
    <p><a href="./">Open Cairn</a></p>`);
  mainEl.querySelector("[data-paste]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const { token, repo: named } = readSignIn(event.target.link.value);
    if (!token) return signedOutView("That is not a sign-in link. On your PC run: cairn dashboards link");
    try {
      await signInWithToken(token, { remember: true });
    } catch (error) {
      return signedOutView(error.message);
    }
    if (named) keepRepo(named);
    start();
  });
}

// The repo lives in localStorage and in the address bar, so a home-screen icon added from here keeps it.
function keepRepo(name) {
  localStorage.setItem(REPO_KEY, name);
  const query = new URLSearchParams(location.search);
  query.set("repo", name);
  history.replaceState(null, "", `${location.pathname}?${query.toString().replace(/%2F/gi, "/")}`);
}

// ------------------------------------------------------------ GitHub

async function readStatus() {
  const headers = { Accept: "application/vnd.github.raw+json" };
  if (etag) headers["If-None-Match"] = etag;
  const response = await authFetch(`${API}/repos/${repo}/contents/${encodeURIComponent("status.json")}?ref=${BRANCH}`,
    { headers, cache: "no-store" });
  if (response.status === 304) return null;
  if (response.status === 404) throw new Error(`Nothing published yet. On your PC run: cairn dashboards publish (repo ${repo}).`);
  if (!response.ok) throw new Error(`GitHub answered ${response.status}.`);
  etag = response.headers.get("ETag") || "";
  return JSON.parse(await response.text());
}

async function readPage(page) {
  try {
    const saved = JSON.parse(localStorage.getItem(PAGE_KEY(page.id)) || "null");
    if (saved && saved.sha === page.sha) return saved.html;
  } catch { /* ignore a broken cache entry */ }
  const response = await authFetch(`${API}/repos/${repo}/git/blobs/${page.sha}`, { headers: { Accept: "application/vnd.github.raw" } });
  if (!response.ok) throw new Error(`GitHub answered ${response.status} for ${page.title}.`);
  const html = new TextDecoder("utf-8").decode(await response.arrayBuffer());
  try {
    localStorage.setItem(PAGE_KEY(page.id), JSON.stringify({ sha: page.sha, html }));
  } catch { /* storage full: just keep it in memory */ }
  return html;
}

// ------------------------------------------------------------ frames

// Added to every page: links open in a new tab, and the scroll position and what the page remembers in
// localStorage/sessionStorage survive updates. The sandbox has no storage of its own, so the page gets a
// stand-in kept in window.name, which stays with the frame when a newer copy of the page is loaded into it.
const HELPER = `<base target="_blank"><script>(() => {
  let kept = {};
  try { kept = JSON.parse(window.name || "{}"); } catch { kept = {}; }
  if (!kept || typeof kept !== "object") kept = {};
  let timer = 0;
  const keep = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { try { window.name = JSON.stringify(kept); } catch { /* too big: start over next time */ } }, 120);
  };
  const memory = (name) => {
    const data = new Map(Object.entries(kept[name] && typeof kept[name] === "object" ? kept[name] : {}));
    const save = () => { kept[name] = Object.fromEntries(data); keep(); };
    return {
      getItem: (k) => (data.has(String(k)) ? data.get(String(k)) : null),
      setItem: (k, v) => { data.set(String(k), String(v)); save(); },
      removeItem: (k) => { data.delete(String(k)); save(); },
      clear: () => { data.clear(); save(); },
      key: (i) => [...data.keys()][i] ?? null,
      get length() { return data.size; },
    };
  };
  for (const name of ["localStorage", "sessionStorage"]) {
    try { window[name].length; } catch { Object.defineProperty(window, name, { value: memory(name), configurable: true }); }
  }
  const restore = () => { if (kept.y > 0) window.scrollTo(0, kept.y); };
  addEventListener("load", () => { restore(); setTimeout(restore, 300); });
  addEventListener("scroll", () => { kept.y = Math.round(scrollY); keep(); }, { passive: true });
})();<\/script>`;

function withHelper(html) {
  const head = html.search(/<head[^>]*>/i);
  if (head < 0) return HELPER + html;
  const end = html.indexOf(">", head) + 1;
  return html.slice(0, end) + HELPER + html.slice(end);
}

function frameFor(page) {
  let frame = frames.get(page.id);
  if (!frame) {
    const iframe = document.createElement("iframe");
    iframe.title = page.title;
    // No allow-same-origin: the page's scripts run in an opaque origin and can't touch this app.
    iframe.setAttribute("sandbox", "allow-scripts allow-popups allow-popups-to-escape-sandbox");
    iframe.hidden = true;
    mainEl.append(iframe);
    frame = { iframe, sha: "", unseen: false };
    frames.set(page.id, frame);
  }
  return frame;
}

async function render(page) {
  if (!page.sha) return;
  const frame = frameFor(page);
  if (frame.sha === page.sha) return;
  const html = await readPage(page);
  frame.iframe.srcdoc = withHelper(html);
  if (frame.sha && page.id !== current) frame.unseen = true;
  frame.sha = page.sha;
}

// ------------------------------------------------------------ buttons that run on the PC

// A frame asks with postMessage({ cairnDash: "action", id, name }); it hears back
// { cairnDash: "reply", id, state: "sending" | "sent" | "running" | "done", ok, message }. Which dashboard asked is
// decided here from the frame the message came from, never from what the message says, and only this
// page (which holds the sign-in) talks to GitHub:
//   dashboards/<id>/requests/<request>.json   written here; the PC deletes it when it takes it
//   dashboards/<id>/results/<request>.json    written by the PC: { state: "running" | "done", ok, message }
// What a name may do is decided on the PC by that dashboard's own command.
const ACTION_POLL_MS = 3000;
const ACTION_PICKUP_MS = 90_000; // not taken by then: take it back, so it can't run later by surprise
const ACTION_ANSWER_MS = 12 * 60_000;
const ACTION_NAME_MAX = 600;
const ACTIONS_AT_ONCE = 3;
let actionsOpen = 0;
let actionNote = "";
let actionNoteTimer = 0;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const randomHex = (bytes) =>
  Array.from(crypto.getRandomValues(new Uint8Array(bytes)), (b) => b.toString(16).padStart(2, "0")).join("");

function noteAction(text, clearAfter = 0) {
  clearTimeout(actionNoteTimer);
  actionNote = text;
  if (clearAfter) actionNoteTimer = setTimeout(() => noteAction(""), clearAfter);
  drawStatus();
}

async function runAction(page, name, tell) {
  const id = `${Date.now()}-${randomHex(4)}`;
  const folder = `${API}/repos/${repo}/contents/dashboards/${encodeURIComponent(page.id)}`;
  const request = { id, page: page.id, name, at: Date.now() };
  const put = await authFetch(`${folder}/requests/${id}.json`, {
    method: "PUT",
    body: JSON.stringify({ message: `dashboard ${page.id}: a button on the phone`, content: encodeBase64(JSON.stringify(request)) }),
  });
  if (!put.ok) {
    throw new Error(put.status === 403 || put.status === 404
      ? "This sign-in can't write to the relay repo. Scan the QR code on your PC again."
      : `GitHub answered ${put.status}. Nothing was sent.`);
  }
  const sha = (await put.json().catch(() => null))?.content?.sha || "";
  tell("sent", "Sent to your PC\u2026");
  const started = Date.now();
  let taken = false;
  while (Date.now() - started < ACTION_ANSWER_MS) {
    await sleep(taken && Date.now() - started > 60_000 ? ACTION_POLL_MS * 2 : ACTION_POLL_MS);
    let answer = null;
    try {
      const response = await authFetch(`${folder}/results/${id}.json`,
        { headers: { Accept: "application/vnd.github.raw+json" }, cache: "no-store" });
      if (response.ok) answer = JSON.parse(await response.text());
    } catch { /* offline for a moment, or half-written: ask again */ }
    if (answer?.state === "done") {
      return { ok: Boolean(answer.ok), message: String(answer.message || (answer.ok ? "Done." : "That didn't work.")) };
    }
    if (answer?.state === "running" && !taken) {
      taken = true;
      tell("running", "Your PC is working on it\u2026");
    }
    if (!taken && Date.now() - started > ACTION_PICKUP_MS) {
      let back = null;
      try {
        back = await authFetch(`${folder}/requests/${id}.json`, {
          method: "DELETE",
          body: JSON.stringify({ message: `dashboard ${page.id}: taken back`, sha }),
        });
      } catch { /* can't tell: keep waiting for an answer */ }
      if (back?.ok) return { ok: false, message: "Your PC didn't pick this up, so nothing was run. Is it on?" };
      taken = true; // the request is gone (the PC has it) or GitHub can't be reached: wait for the answer
    }
  }
  return { ok: false, message: "No answer from your PC yet. Look again in a few minutes." };
}

addEventListener("message", async (event) => {
  const asked = event.data;
  if (!asked || asked.cairnDash !== "action") return;
  const from = [...frames].find(([, frame]) => frame.iframe.contentWindow === event.source);
  if (!from) return; // not one of the dashboards shown here
  const page = status?.pages.find((p) => p.id === from[0]);
  const title = page?.title || from[0];
  const tell = (state, message, ok) => {
    try {
      event.source.postMessage({ cairnDash: "reply", id: asked.id, state, ok, message }, "*");
    } catch { /* the frame is gone; the status line still tells */ }
    noteAction(`${title}: ${message}`, state === "done" ? 20_000 : 0);
  };
  const name = typeof asked.name === "string" ? asked.name : "";
  if (!page?.actions) return tell("done", "This dashboard's buttons only work on your PC.", false);
  if (!name || name.length > ACTION_NAME_MAX) return tell("done", "That button can't be sent from the phone.", false);
  if (actionsOpen >= ACTIONS_AT_ONCE) return tell("done", "Wait for the earlier buttons to finish first.", false);
  tell("sending", "Sending to your PC\u2026"); // heard: the frame stops waiting for a page that can't send
  actionsOpen += 1;
  try {
    const result = await runAction(page, name, tell);
    tell("done", result.message, result.ok);
    for (const ms of [6000, 15_000, 30_000, 60_000]) setTimeout(() => check(), ms); // the PC publishes a fresh page next
  } catch (error) {
    tell("done", error.message || "Nothing was sent.", false);
  } finally {
    actionsOpen -= 1;
  }
});

// ------------------------------------------------------------ UI

function show(id) {
  current = id;
  localStorage.setItem(TAB_KEY, id);
  const frame = frames.get(id);
  if (frame) frame.unseen = false;
  for (const [key, value] of frames) value.iframe.hidden = key !== id;
  drawTabs();
  drawStatus();
  const page = status?.pages.find((p) => p.id === id);
  if (page && !page.sha) {
    mainEl.querySelector(".message")?.remove();
    mainEl.insertAdjacentHTML("beforeend", `<div class="message"><h1>${esc(page.title)}</h1><p>${esc(page.error || "No page yet.")}</p></div>`);
  } else {
    mainEl.querySelector(".message")?.remove();
  }
}

function drawTabs() {
  if (!status) return;
  tabsEl.innerHTML = status.pages.map((page) => `<button role="tab" type="button" data-tab="${esc(page.id)}"
    aria-selected="${page.id === current}">${esc(page.title)}${frames.get(page.id)?.unseen ? '<span class="dot" aria-label="updated"></span>' : ""}</button>`).join("");
}

function drawStatus() {
  if (!status) return;
  const page = status.pages.find((p) => p.id === current);
  const heartbeat = (status.heartbeatSeconds || 600) * 1000;
  const pcQuiet = Date.now() - Date.parse(status.published) > heartbeat * 2.5;
  const parts = [];
  if (page?.updated) parts.push(`${esc(page.title)} updated ${ago(page.updated)}`);
  if (pcQuiet) parts.push(`<span class="warn">PC last checked in ${ago(status.published)}${status.pc ? ` (${esc(status.pc)})` : ""}</span>`);
  if (page?.error && page.sha) parts.push(`<span class="warn">${esc(page.error)}</span>`);
  if (lastError) parts.push(`<span class="warn">${esc(lastError)}</span>`);
  if (actionNote) parts.unshift(`<strong>${esc(actionNote)}</strong>`);
  statusEl.className = `status${lastError ? " error" : pcQuiet ? " stale" : ""}`;
  statusEl.innerHTML = `<span class="pulse" aria-hidden="true"></span><span>${parts.join(" &middot; ") || "Live"}</span>`;
  statusEl.hidden = false;
}

async function check({ manual = false } = {}) {
  if (manual) refreshEl.classList.add("spin");
  try {
    const fresh = await readStatus();
    lastError = "";
    if (fresh) {
      status = fresh;
      if (!status.pages.some((p) => p.id === current)) current = status.pages[0]?.id || "";
      // The visible page first, so it appears fast; the others load behind it.
      const ordered = [...status.pages].sort((a, b) => (a.id === current ? -1 : b.id === current ? 1 : 0));
      for (const page of ordered) {
        try {
          await render(page);
        } catch (error) {
          lastError = error.message;
        }
      }
      if (mainEl.querySelector(".message") && frames.size) mainEl.querySelector(".message").remove();
      show(current);
    }
  } catch (error) {
    if (error instanceof AuthError && !session()) return signedOutView(error.message);
    if (!status) return message(`<h1>Dashboards</h1><p>${esc(error.message)}</p>`);
    lastError = navigator.onLine ? error.message : "Offline. Showing the last copy.";
  } finally {
    lastCheck = Date.now();
    refreshEl.classList.remove("spin");
    drawStatus();
  }
}

tabsEl.addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab]");
  if (button) show(button.dataset.tab);
});
refreshEl.addEventListener("click", () => check({ manual: true }));
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && Date.now() - lastCheck > 5000) check();
});
addEventListener("online", () => check());
setInterval(() => {
  if (!document.hidden) check();
}, POLL_MS);
setInterval(drawStatus, 30_000); // keep "x min ago" honest between checks

async function start() {
  const params = new URLSearchParams(location.hash.slice(1));
  const token = params.get("signin");
  if (token) {
    history.replaceState(null, "", location.pathname + location.search);
    try {
      await signInWithToken(token, { remember: true });
    } catch (error) {
      return signedOutView(error.message);
    }
    if (REPO_NAME.test(params.get("repo") || "")) keepRepo(params.get("repo"));
    if (params.get("page")) current = params.get("page");
  } else if (params.get("page")) {
    current = params.get("page");
  }
  const named = new URLSearchParams(location.search).get("repo") || "";
  if (REPO_NAME.test(named)) localStorage.setItem(REPO_KEY, named);
  const who = session();
  if (!who) return signedOutView();
  repo = localStorage.getItem(REPO_KEY) || `${who.login}/cairn-relay`;
  if (!REPO_NAME.test(named)) keepRepo(repo);
  await check();
}

start();
