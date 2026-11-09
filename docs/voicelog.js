// Voice log: the centre button, the log sheet (speech or typing) and the Journal & memories view.
// Speech uses the browser's own SpeechRecognition / speechSynthesis; nothing is sent to Cairn or any API.

import * as core from "./core.js";
import * as dl from "./daylog.js";
import * as lv from "./localvoice.js";
import { escapeHtml as esc, renderMarkdown } from "./md.js";
import { $, icon, pageHead, plural, toast, today } from "./ui.js";
import { createNote, deleteNote, noteByPath, readOnly, saveNote, scheduleGitRefresh, state, upsertFile } from "./state.js";
import { openNote } from "./drawer.js";
import { render, setView } from "./app.js";

const PREFS_KEY = "cairn.voicelog";
const MIC = '<svg viewBox="0 0 24 24" aria-hidden="true" class="icon"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/></svg>';
const SPEAK = '<svg viewBox="0 0 24 24" aria-hidden="true" class="icon sm"><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/></svg>';
const KIND_LABEL = { log: "Log", task: "Task", idea: "Idea", decision: "Decision", note: "Note", mood: "Mood" };

const HINT_KEY = "cairn.voicelog.hinted";
// Languages the browsers' speech engines handle well; "" follows the browser.
export const LANGS = [
  ["", "Browser language"],
  ["en-US", "English (US)"],
  ["en-GB", "English (UK)"],
  ["id-ID", "Bahasa Indonesia"],
  ["zh-TW", "中文 (台灣)"],
  ["zh-CN", "中文 (中国)"],
  ["ja-JP", "日本語"],
  ["ko-KR", "한국어"],
  ["es-ES", "Español"],
  ["fr-FR", "Français"],
  ["de-DE", "Deutsch"],
  ["nl-NL", "Nederlands"],
  ["pt-BR", "Português (Brasil)"],
  ["hi-IN", "हिन्दी"],
];
const online = () => navigator.onLine !== false;

let recognizer = null;
let wakeRecognizer = null;
let listening = false;
let wantListening = false;
let undoStack = [];
let promptIndex = Math.floor(Math.random() * dl.PROMPTS.length);
let journalWired = null;

export function prefs() {
  try {
    return { autoListen: true, keepListening: false, review: false, lang: "", wake: false, offline: false, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") };
  } catch {
    return { autoListen: true, keepListening: false, review: false, lang: "", wake: false, offline: false };
  }
}

function setPref(key, value) {
  localStorage.setItem(PREFS_KEY, JSON.stringify({ ...prefs(), [key]: value }));
}

const NativeRecognition = () => window.SpeechRecognition || window.webkitSpeechRecognition || null;
const speechLang = () => prefs().lang || navigator.language || "en-US";
let serviceDown = false; // the browser's speech service failed this session ("network")
let offlineJob = null; // { progress } while a model downloads

// Which recognizer to use. Chrome's on-device engine when offline voice is on and installed; the Vosk engine
// when it is set up and wanted (offline voice on, no browser engine, offline, or the speech service failed);
// otherwise the browser's own engine, which usually needs a connection.
export function voiceEngine() {
  const Native = NativeRecognition();
  const p = prefs();
  if (p.offline && Native && lv.chromeLocalReady(speechLang())) return { Engine: Native, mode: "chrome-local" };
  if (lv.installed() && (p.offline || !Native || !online() || serviceDown)) return { Engine: lv.LocalRecognition, mode: "local" };
  if (Native) return { Engine: Native, mode: "browser" };
  return null;
}
export const speechSupported = () => !!voiceEngine();
const onDevice = () => ["local", "chrome-local"].includes(voiceEngine()?.mode);

function makeRecognizer() {
  const picked = voiceEngine();
  if (!picked) return null;
  const rec = new picked.Engine();
  rec.lang = speechLang();
  if (picked.mode === "chrome-local") rec.processLocally = true;
  rec.mode = picked.mode;
  return rec;
}
const canSpeak = () => typeof window.speechSynthesis !== "undefined" && typeof window.SpeechSynthesisUtterance !== "undefined";

const nowTime = () => new Date().toTimeString().slice(0, 5);
const todayPath = () => `journal/${today()}.md`;
const canWrite = () => !readOnly() && state.info?.mode !== "visitor";

function inline(text) {
  const html = renderMarkdown(text, (target) => core.resolveLink(state.notes, target));
  return html.replace(/^\s*<p>/, "").replace(/<\/p>\s*$/, "");
}

function days() {
  return dl.journalDays(state.notes);
}

// ============================================================ the centre button

// The Log button lives in the top bar on every screen size, next to search: always one tap away and
// never on top of what you're reading (a floating button always covers something). L opens it too.
function placeFab(fab) {
  const home = $(".topbar") || $("#app");
  home.appendChild(fab);
  fab.classList.toggle("in-topbar", home.classList.contains("topbar"));
}

export function initVoiceLog() {
  if ($("#log-fab")) return;
  const fab = document.createElement("button");
  fab.id = "log-fab";
  fab.className = "log-fab";
  fab.type = "button";
  fab.setAttribute("aria-label", "Log your day by voice or typing (L)");
  fab.title = "Log your day (L)";
  fab.innerHTML = `${MIC}<span class="log-fab-label">Log</span>`;
  if (!localStorage.getItem(HINT_KEY)) {
    fab.insertAdjacentHTML("beforeend", `<span class="log-fab-hint" aria-hidden="true">Tap or press <kbd>L</kbd>, then just talk</span>`);
  }
  fab.addEventListener("click", () => openLogSheet());
  placeFab(fab);
  announcer();
  const redraw = () => {
    updateVoiceUi();
    syncWake();
  };
  window.addEventListener("online", redraw);
  window.addEventListener("offline", redraw);
  document.addEventListener("visibilitychange", () => syncWake());
  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-log-open]")) openLogSheet();
    const memory = event.target.closest?.(".home-log-memory[data-jl-day]");
    if (memory) openDay(memory.dataset.jlDay);
    const review = event.target.closest?.("[data-review]");
    if (review) {
      const day = review.dataset.reviewDay || today();
      if (review.dataset.review === "month") monthlyReview(day);
      else weeklyReview(day);
    }
  });
  syncLogFab();
}

export function syncLogFab() {
  const fab = $("#log-fab");
  if (!fab) return;
  fab.hidden = !canWrite();
  const streak = dl.streaks(days(), today());
  fab.classList.toggle("logged", streak.loggedToday);
  fab.dataset.streak = streak.current ? String(streak.current) : "";
  syncWake();
}

// ============================================================ "Hey Cairn": optional background wake word

export function syncWake() {
  const want = prefs().wake && canWrite() && speechSupported() && !$(".log-sheet") && document.visibilityState !== "hidden";
  const fab = $("#log-fab");
  fab?.classList.toggle("wake-on", !!want);
  if (fab) fab.title = want ? "Log your day (L). Listening for “Hey Cairn”" : "Log your day (L)";
  if (want && !wakeRecognizer) startWake();
  if (!want && wakeRecognizer) stopWake();
}

function startWake() {
  let engine;
  try {
    engine = makeRecognizer();
  } catch {
    return;
  }
  if (!engine) return;
  wakeRecognizer = engine;
  engine.continuous = true;
  engine.interimResults = false;
  engine.onresult = (event) => {
    for (let i = event.resultIndex ?? 0; i < event.results.length; i++) {
      const result = event.results[i];
      if (!result.isFinal) continue;
      const rest = dl.matchWake(result[0]?.transcript || "");
      if (rest !== null) return wake(rest);
    }
  };
  engine.onerror = (event) => {
    if (event.error === "not-allowed" || event.error === "service-not-allowed") {
      setPref("wake", false);
      toast("Microphone blocked, so “Hey Cairn” is off. Allow the mic in the site settings to use it.");
    }
  };
  engine.onend = () => {
    if (wakeRecognizer !== engine) return;
    wakeRecognizer = null;
    setTimeout(syncWake, 400);
  };
  try {
    engine.start();
  } catch {
    wakeRecognizer = null;
  }
}

function stopWake() {
  const engine = wakeRecognizer;
  wakeRecognizer = null;
  try {
    engine?.abort ? engine.abort() : engine?.stop();
  } catch {
    /* not running */
  }
}

async function wake(rest) {
  stopWake();
  openLogSheet({ listen: !rest });
  if (rest) {
    await handleUtterance(rest);
    if (prefs().keepListening && speechSupported() && !listening) startListening();
  }
}

// Spoken names become @mentions when the person has been @mentioned before ("lunch with Sam" -> "@sam").
export function linkPeople(text) {
  const known = new Set();
  for (const info of days().values()) for (const entry of info.entries) for (const person of entry.people || []) known.add(person);
  if (!known.size) return text;
  return text.replace(/(^|[\s,(])([A-Za-z][A-Za-z.'-]*)(?=$|[\s,.!?)])/g, (whole, lead, word) =>
    known.has(word.toLowerCase()) ? `${lead}@${word.toLowerCase()}` : whole,
  );
}

// ============================================================ the log sheet

export function openLogSheet({ listen } = {}) {
  if (!canWrite()) {
    toast("This brain is read-only. Ask to join to contribute.");
    return;
  }
  if ($(".log-sheet")) {
    $("#log-text")?.focus();
    return;
  }
  localStorage.setItem(HINT_KEY, "1");
  $(".log-fab-hint")?.remove();
  stopWake();
  const wrap = document.createElement("div");
  wrap.className = "modal-wrap log-wrap";
  wrap.innerHTML = `<section class="log-sheet" role="dialog" aria-modal="true" aria-labelledby="log-title" tabindex="-1"></section>`;
  document.body.appendChild(wrap);
  document.body.classList.add("log-open");
  const onKey = (event) => {
    if (!wrap.isConnected) return;
    if (event.key === "Tab") {
      // Keep keyboard focus inside the dialog.
      const focusable = [...wrap.querySelectorAll('button:not([disabled]), textarea, input:not([type="hidden"]):not([hidden]), select, summary, [href]')]
        .filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !wrap.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !wrap.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
    if (event.key === "Escape" && !event.target.closest?.(".log-entry input")) {
      event.preventDefault();
      event.stopPropagation();
      closeLogSheet();
    }
  };
  wrap._onKey = onKey;
  document.addEventListener("keydown", onKey, true);
  wrap.addEventListener("click", onSheetClick);
  wrap.addEventListener("change", onSheetChange);
  wrap.addEventListener("keydown", onSheetKeydown);
  drawSheet();
  refreshChromeStatus();
  const auto = listen ?? prefs().autoListen;
  if (auto && speechSupported()) startListening();
  else $("#log-text")?.focus();
}

export function closeLogSheet() {
  const wrap = $(".log-wrap");
  stopListening();
  if (!wrap) return;
  document.removeEventListener("keydown", wrap._onKey, true);
  wrap.remove();
  document.body.classList.remove("log-open");
  $("#log-fab")?.focus();
  setTimeout(syncWake, 300);
}

function drawSheet() {
  const sheet = $(".log-sheet");
  if (!sheet) return;
  const draft = $("#log-text")?.value || "";
  const all = days();
  const info = all.get(today());
  const entries = info ? info.entries : [];
  const streak = dl.streaks(all, today());
  const p = prefs();
  const supported = speechSupported();
  const prompt = dl.PROMPTS[promptIndex % dl.PROMPTS.length];
  sheet.innerHTML = `
    <header class="log-head">
      <div>
        <p class="eyebrow">${esc(dl.prettyDay(today()))}</p>
        <h2 id="log-title">Today's log</h2>
      </div>
      <div class="log-head-side">
        ${streak.current ? `<span class="streak-pill" title="Days in a row with at least one entry">${streak.current}-day streak</span>` : ""}
        <button type="button" class="btn ghost icon-only" data-log="close" aria-label="Close">${icon("x", "sm")}</button>
      </div>
    </header>
    <div class="log-voice ${listening ? "is-listening" : ""}">
      <button type="button" class="log-mic" data-log="mic" ${supported ? "" : "disabled"} aria-pressed="${listening}" aria-label="${listening ? "Stop listening" : "Start listening"}">
        ${MIC}<span class="log-ring" aria-hidden="true"></span>
      </button>
      <div class="log-voice-text">
        <strong id="log-status">${statusText()}</strong>
        <p id="log-live" class="log-live" aria-live="polite">${supported ? "Try “finished the export fix”, “todo call the bank”, “mood good” or “read it back”." : "Type below instead. Chrome, Edge and Safari support voice input."}</p>
      </div>
    </div>
    <form class="log-form" data-log-form>
      <label class="sr-only" for="log-text">What happened?</label>
      <textarea id="log-text" rows="2" placeholder="Say or type what happened… (todo:, idea:, decision:, mood: work too)">${esc(draft)}</textarea>
      <button type="submit" class="btn primary" data-log="save">Save</button>
    </form>
    <p class="log-feedback" id="log-feedback"></p>
    <div class="log-mood-row">
      <div class="mood-chips" role="group" aria-label="How do you feel?">
        ${dl.MOODS.map((m) => `<button type="button" class="mood-chip" data-mood="${m.score}" title="${m.label}" aria-label="Mood: ${m.label}, ${m.score} out of 5">${m.label}</button>`).join("")}
      </div>
      <div class="log-prompt">
        <button type="button" class="link-btn" data-log="use-prompt" title="Answer this">${esc(prompt)}</button>
        <button type="button" class="btn ghost icon-only sm" data-log="next-prompt" aria-label="Another question">${icon("shuffle", "sm")}</button>
      </div>
    </div>
    <div class="log-timeline">
      <div class="log-timeline-head">
        <strong>${plural(entries.length, "entry", "entries")} today</strong>
        <span class="log-timeline-actions">
          ${entries.length && canSpeak() ? `<button type="button" class="btn ghost sm" data-log="read">${SPEAK} Read aloud</button>` : ""}
          <button type="button" class="btn ghost sm" data-log="open-page">${icon("journal", "sm")} Open page</button>
        </span>
      </div>
      ${entries.length
        ? `<ol class="log-entries">${entries.slice().reverse().map(entryRow).join("")}</ol>`
        : `<p class="muted log-empty">Nothing yet today. Your first entry starts today's page in <code>brain/${esc(todayPath())}</code>.</p>`}
    </div>
    <footer class="log-foot">
      <label class="check"><input type="checkbox" data-pref="keepListening" ${p.keepListening ? "checked" : ""}> Keep listening</label>
      <label class="check"><input type="checkbox" data-pref="review" ${p.review ? "checked" : ""}> Review before saving</label>
      <label class="check"><input type="checkbox" data-pref="autoListen" ${p.autoListen ? "checked" : ""}> Start the mic when I open this</label>
      <label class="check" title="Keeps the microphone on while Cairn is open and opens this sheet when you say “Hey Cairn …”"><input type="checkbox" data-pref="wake" ${p.wake ? "checked" : ""} ${supported ? "" : "disabled"}> Listen for “Hey Cairn”</label>
      <label class="log-lang">Language
        <select data-pref-select="lang" aria-label="Speech language">${LANGS.map(([code, name]) => `<option value="${code}" ${code === (p.lang || "") ? "selected" : ""}>${esc(name)}${code && !lv.modelFor(code) ? " (no offline model)" : ""}</option>`).join("")}</select>
      </label>
      ${offlineVoiceHtml()}
      <details class="log-help">
        <summary>Voice commands</summary>
        <ul>
          <li><b>Anything</b> → a timestamped entry in today's log</li>
          <li><b>“todo …”</b>, <b>“remind me to …”</b> → a task on the board, linked from the log</li>
          <li><b>“idea …”</b>, <b>“decision …”</b>, <b>“note …”</b> → that kind of note, linked</li>
          <li><b>“mood good”</b>, <b>“I feel tired”</b> → a mood check-in</li>
          <li><b>“undo”</b> / <b>“scratch that”</b> → remove the last entry</li>
          <li><b>“read it back”</b>, <b>“what did I do last Friday”</b>, <b>“read 3 November”</b> → read that day aloud</li>
          <li><b>“open last Monday”</b> → open that day's page; <b>“search for gpx”</b>, <b>“when did I last mention Sam”</b> → search every day</li>
          <li><b>“weekly review”</b>, <b>“monthly review”</b>, <b>“open today”</b>, <b>“open my memories”</b></li>
          <li><b>“stop”</b> → stop listening</li>
          <li><b>“Hey Cairn, …”</b> from any screen (turn on “Listen for Hey Cairn”) → opens this and logs the rest</li>
          <li>Names you have written as <b>@sam</b> before are linked when you say them</li>
        </ul>
      </details>
    </footer>`;
  const text = $("#log-text");
  if (text && document.activeElement === document.body) text.focus();
  $(".log-offline")?.addEventListener("toggle", (event) => {
    offlineOpen = event.target.open;
  });
}

// ------------------------------------------------------------ offline voice panel

let offlineOpen = false;
let chromeStatus = null;

function megabytes(bytes) {
  return `${Math.max(1, Math.round(bytes / 1048576))} MB`;
}

function offlineVoiceHtml() {
  const meta = lv.installed();
  const lang = speechLang();
  const model = lv.modelFor(lang);
  const langName = LANGS.find(([code]) => code && code === lang)?.[1] || lang;
  const p = prefs();
  let badge = "Off";
  let body = "";
  if (offlineJob) {
    badge = `${Math.round(offlineJob.progress * 100)}%`;
    body = `<p class="log-offline-text" id="log-offline-progress-text">Downloading the ${esc(offlineJob.name)} model (${offlineJob.mb} MB) once…</p>
      <progress id="log-offline-progress" max="1" value="${offlineJob.progress}" aria-label="Offline voice download"></progress>`;
  } else if (meta) {
    badge = "Ready";
    body = `<p class="log-offline-text">Ready on this device: <b>${esc(meta.name)}</b> (${megabytes(meta.bytes)}). Recognition runs in this browser, so it works without a connection and audio never leaves the device.</p>
      <div class="log-offline-actions">
        <label class="check"><input type="checkbox" data-pref="offline" ${p.offline ? "checked" : ""}> Use it even when online</label>
        <button type="button" class="btn ghost sm" data-log="offline-remove">Remove</button>
      </div>`;
  } else {
    body = `<p class="log-offline-text">Talk without a connection. A small speech model is stored in this browser once; after that, audio never leaves the device.</p>
      <div class="log-offline-actions">
        ${model ? `<button type="button" class="btn sm" data-log="offline-get">Download ${esc(model.name)} model (${model.mb} MB)</button>` : `<span class="muted">No download for ${esc(langName)} yet.</span>`}
        <label class="btn ghost sm file-btn">Use a model file<input type="file" accept=".tar.gz,.tgz,application/gzip" data-offline-file hidden></label>
      </div>`;
  }
  const chromeLine = ["available", "downloadable", "downloading"].includes(chromeStatus)
    ? `<div class="log-offline-actions"><span class="muted">This browser can also recognise ${esc(langName)} on the device.</span>
        ${p.offline && chromeStatus === "available" ? `<span class="pill">In use</span>` : `<button type="button" class="btn ghost sm" data-log="offline-chrome">${chromeStatus === "available" ? "Use it" : "Install it"}</button>`}</div>`
    : "";
  return `<details class="log-offline" ${offlineOpen || offlineJob ? "open" : ""}>
      <summary>Offline voice <span class="pill ${meta ? "ok" : ""}" id="log-offline-badge">${badge}</span></summary>
      ${body}${chromeLine}
    </details>`;
}

async function refreshChromeStatus() {
  const status = await lv.chromeLocalStatus(speechLang());
  if (status === chromeStatus) return;
  const visible = (s) => ["available", "downloadable", "downloading"].includes(s);
  const redraw = visible(status) || visible(chromeStatus);
  chromeStatus = status;
  if (redraw && $(".log-sheet")) drawSheet();
}

async function setUpOfflineVoice() {
  const model = lv.modelFor(speechLang());
  if (!model || offlineJob) return;
  offlineJob = { progress: 0, name: model.name, mb: model.mb };
  offlineOpen = true;
  drawSheet();
  let lastPaint = 0;
  try {
    await lv.downloadModel(speechLang(), (progress) => {
      offlineJob.progress = progress;
      if (Date.now() - lastPaint < 150 && progress < 1) return;
      lastPaint = Date.now();
      const bar = $("#log-offline-progress");
      if (bar) bar.value = progress;
      const badge = $("#log-offline-badge");
      if (badge) badge.textContent = `${Math.round(progress * 100)}%`;
    });
    offlineJob = null;
    setPref("offline", true);
    feedback(`Offline voice is ready: ${model.name}, on this device.`);
  } catch (error) {
    offlineJob = null;
    feedback(`Offline voice download failed: ${error.message}. Try again on a connection, or use a model file.`);
  }
  drawSheet();
  updateVoiceUi();
}

async function useModelFile(file) {
  try {
    const meta = await lv.importModel(file);
    setPref("offline", true);
    feedback(`Offline voice is ready with ${meta.name}.`);
  } catch (error) {
    feedback(error.message);
  }
  drawSheet();
  updateVoiceUi();
}

async function useChromeLocal() {
  const lang = speechLang();
  if (chromeStatus !== "available") {
    feedback("Installing the browser's on-device speech for this language…");
    const ok = await lv.installChromeLocal(lang);
    chromeStatus = ok ? "available" : await lv.chromeLocalStatus(lang);
    if (!ok) {
      drawSheet();
      return feedback("The browser couldn't install on-device speech right now.");
    }
  }
  setPref("offline", true);
  feedback("Using the browser's on-device speech. It works without a connection.");
  drawSheet();
  updateVoiceUi();
}

function entryRow(entry) {
  const mood = entry.mood ? `<span class="entry-mood" title="Mood ${entry.mood.score} out of 5">${entry.mood.label}</span>` : "";
  const body = entry.kind === "mood" ? `${esc(entry.mood ? `Feeling ${entry.mood.label}` : entry.text.replace(/^Mood:\s*/, ""))}` : inline(entry.text.replace(/^(Task|Idea|Decision|Note):\s*/, ""));
  return `<li class="log-entry kind-${entry.kind}" data-line="${entry.line}">
    <span class="entry-time">${esc(entry.time || "·")}</span>
    ${entry.kind !== "log" && entry.kind !== "mood" ? `<span class="entry-kind">${KIND_LABEL[entry.kind]}</span>` : mood}
    <span class="entry-text">${body}</span>
    <span class="entry-actions">
      <button type="button" class="btn ghost icon-only sm" data-log="edit" aria-label="Edit entry">${icon("pencil", "sm")}</button>
      <button type="button" class="btn ghost icon-only sm" data-log="remove" aria-label="Delete entry">${icon("trash", "sm")}</button>
    </span>
  </li>`;
}

function feedback(message) {
  const el = $("#log-feedback");
  if (el) el.textContent = message;
  announce(message);
}

// The sheet is redrawn after every save, so a live region inside it would be brand new each time, and a region
// created in the same tick as its text is often not read out. One region on <body> outlives the redraws.
function announcer() {
  let region = document.getElementById("log-announcer");
  if (!region) {
    region = document.createElement("div");
    region.id = "log-announcer";
    region.className = "sr-only";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    document.body.appendChild(region);
  }
  return region;
}

function announce(message) {
  const region = announcer();
  // A live region only speaks when its text changes; nudge an identical repeat so it is read again.
  region.textContent = region.textContent === message ? `${message}\u00a0` : message;
}

async function onSheetClick(event) {
  const wrap = event.currentTarget;
  if (event.target === wrap) return closeLogSheet();
  if (event.target.closest(".wikilink[data-open]")) {
    setTimeout(closeLogSheet, 0);
    return;
  }
  const mood = event.target.closest("[data-mood]");
  if (mood) {
    const m = dl.MOODS[Number(mood.dataset.mood) - 1];
    await addLine(dl.moodLine(m));
    feedback(`Mood saved: ${m.label}`);
    return;
  }
  const button = event.target.closest("[data-log]");
  if (!button) return;
  const action = button.dataset.log;
  const row = button.closest(".log-entry");
  if (action === "close") return closeLogSheet();
  if (action === "mic") return listening ? stopListening(true) : startListening();
  if (action === "read") return readAloud(0);
  if (action === "open-page") {
    closeLogSheet();
    return openDay(today(), { create: true });
  }
  if (action === "next-prompt") {
    promptIndex++;
    return drawSheet();
  }
  if (action === "use-prompt") {
    const text = $("#log-text");
    const prompt = dl.PROMPTS[promptIndex % dl.PROMPTS.length];
    text.value = `${prompt} `;
    text.focus();
    text.setSelectionRange(text.value.length, text.value.length);
    return;
  }
  if (action === "remove" && row) return removeLine(Number(row.dataset.line));
  if (action === "edit" && row) return editLine(row);
  if (action === "offline-get") return setUpOfflineVoice();
  if (action === "offline-chrome") return useChromeLocal();
  if (action === "offline-remove") {
    if (listening) stopListening(true);
    await lv.removeModel();
    setPref("offline", false);
    feedback("Offline voice removed from this browser.");
    drawSheet();
    return updateVoiceUi();
  }
}

function onSheetChange(event) {
  const file = event.target.closest("[data-offline-file]");
  if (file) {
    if (file.files?.[0]) useModelFile(file.files[0]);
    return;
  }
  const select = event.target.closest("[data-pref-select]");
  if (select) {
    setPref(select.dataset.prefSelect, select.value);
    chromeStatus = null;
    drawSheet();
    refreshChromeStatus();
    feedback(`Speech language: ${LANGS.find(([code]) => code === select.value)?.[1] || select.value}.`);
    if (listening) {
      stopListening(true);
      setTimeout(() => startListening(), 200);
    }
    return;
  }
  const pref = event.target.closest("[data-pref]");
  if (!pref) return;
  setPref(pref.dataset.pref, pref.checked);
  if (pref.dataset.pref === "wake") {
    feedback(pref.checked ? "Say “Hey Cairn, …” on any screen to log." : "No longer listening for “Hey Cairn”.");
    syncWake();
  }
  if (pref.dataset.pref === "keepListening" && !pref.checked && listening) feedback("Will stop after the next entry.");
  if (pref.dataset.pref === "offline") {
    feedback(pref.checked ? "Voice now runs on this device." : "Voice uses the browser's engine when online, and this device when offline.");
    updateVoiceUi();
    if (listening) {
      stopListening(true);
      setTimeout(() => startListening(), 500);
    }
  }
}

async function onSheetKeydown(event) {
  if (event.target.id === "log-text" && event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    await submitText();
  }
}

export async function submitText() {
  const field = $("#log-text");
  const text = field?.value.trim();
  if (!text) return;
  field.value = "";
  await handleUtterance(text, { typed: true });
  $("#log-text")?.focus();
}

// Form submit (the Save button)
document.addEventListener("submit", (event) => {
  if (!event.target.matches?.("[data-log-form]")) return;
  event.preventDefault();
  submitText();
});

// ============================================================ what a sentence does

export async function handleUtterance(raw, { typed = false } = {}) {
  if (!canWrite()) return null;
  if (!typed && prefs().review) {
    const field = $("#log-text");
    if (field) {
      field.value = `${field.value ? field.value + " " : ""}${raw}`.trim();
      field.focus();
      feedback("Check it, then press Enter to save.");
    }
    return null;
  }
  const intent = dl.voiceIntent(typed ? raw : linkPeople(raw), today());
  switch (intent.kind) {
    case "empty":
      return intent;
    case "stop":
      stopListening(true);
      feedback("Stopped listening.");
      return intent;
    case "undo":
      await undoLast();
      break;
    case "read":
      readAloud(intent.day);
      break;
    case "open-today":
      closeLogSheet();
      await openDay(today(), { create: true });
      return intent;
    case "open-journal":
      closeLogSheet();
      setView("journal");
      return intent;
    case "open-day":
      closeLogSheet();
      if (days().get(intent.date)?.entries.length || noteByPath(`journal/${intent.date}.md`)) await openDay(intent.date);
      else toast(`Nothing logged on ${dl.prettyDay(intent.date)}.`);
      return intent;
    case "search": {
      const hits = dl.searchEntries(days(), intent.query);
      const exact = hits.filter((h) => !h.fuzzy);
      const last = exact[0] || hits[0];
      const said = last
        ? `${plural(exact.length || hits.length, "entry", "entries")} ${exact.length ? "mention" : "come close to"} “${intent.query}”. The latest is ${dl.prettyDay(last.day)}: ${dl.plainText(last.entry.text)}`
        : `Nothing in the log mentions “${intent.query}” yet.`;
      closeLogSheet();
      state.journalQuery = intent.query;
      setView("journal");
      speak(said);
      toast(said);
      return intent;
    }
    case "review":
      closeLogSheet();
      await weeklyReview();
      return intent;
    case "month-review":
      closeLogSheet();
      await monthlyReview();
      return intent;
    case "task":
    case "idea":
    case "decision":
    case "note": {
      const parsed = core.parseCapture(intent.text);
      const note = await createNote({ type: intent.kind, title: parsed.title || intent.text, tags: parsed.tags });
      await addLine(`${KIND_LABEL[intent.kind]}: [[${note.slug}|${parsed.title || intent.text}]]`, { createdPath: note.path });
      feedback(`${KIND_LABEL[intent.kind]} created and linked: ${parsed.title || intent.text}`);
      break;
    }
    case "mood":
      await addLine(dl.moodLine(intent.mood));
      feedback(`Mood saved: ${intent.mood.label}`);
      break;
    default:
      await addLine(intent.text);
      feedback(`Saved at ${nowTime()}.`);
  }
  if (!typed && listening && !prefs().keepListening) stopListening(true);
  return intent;
}

async function ensureToday() {
  const path = todayPath();
  if (!noteByPath(path)) await createNote({ type: "journal", title: today(), body: "## Log\n\n" });
  return noteByPath(path);
}

export async function addLine(text, { createdPath = null } = {}) {
  const note = await ensureToday();
  const time = nowTime();
  await saveNote(note.path, { body: dl.appendEntry(note.body, time, text) });
  undoStack.push({ path: note.path, text, time, createdPath });
  afterChange();
}

async function removeLine(lineIndex) {
  const note = noteByPath(todayPath());
  if (!note) return;
  const entry = dl.parseEntries(note.body).find((e) => e.line === lineIndex);
  await saveNote(note.path, { body: dl.removeEntry(note.body, lineIndex) });
  feedback(entry ? `Deleted “${dl.plainText(entry.text).slice(0, 60)}”.` : "Deleted.");
  afterChange();
}

function editLine(row) {
  const note = noteByPath(todayPath());
  if (!note) return;
  const lineIndex = Number(row.dataset.line);
  const entry = dl.parseEntries(note.body).find((e) => e.line === lineIndex);
  if (!entry) return;
  const textEl = row.querySelector(".entry-text");
  textEl.innerHTML = `<input class="input entry-edit" value="${esc(entry.text)}" aria-label="Edit entry">`;
  const input = textEl.querySelector("input");
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const value = input.value.trim();
    if (save && value && value !== entry.text) {
      const fresh = noteByPath(todayPath());
      await saveNote(fresh.path, { body: dl.replaceEntry(fresh.body, lineIndex, entry.time, value) });
      feedback("Entry updated.");
    }
    afterChange();
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      finish(true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      finish(false);
    }
  });
  input.addEventListener("blur", () => finish(true));
}

async function undoLast() {
  const note = noteByPath(todayPath());
  if (!note) return feedback("Nothing to undo.");
  const entries = dl.parseEntries(note.body);
  const last = undoStack.pop();
  let target = null;
  if (last && last.path === note.path) target = [...entries].reverse().find((e) => e.text === last.text && e.time === last.time) || null;
  if (!target) target = entries[entries.length - 1] || null;
  if (!target) return feedback("Nothing to undo.");
  await saveNote(note.path, { body: dl.removeEntry(note.body, target.line) });
  if (last?.createdPath && noteByPath(last.createdPath)) await deleteNote(last.createdPath);
  feedback(`Removed “${dl.plainText(target.text).slice(0, 60)}”.`);
  afterChange();
}

function afterChange() {
  render();
  syncLogFab();
  drawSheet();
}

async function openDay(day, { create = false } = {}) {
  const path = `journal/${day}.md`;
  if (!noteByPath(path)) {
    if (!create || !canWrite()) return toast(`Nothing logged on ${dl.prettyDay(day)}.`);
    await createNote({ type: "journal", title: day, body: "## Log\n\n" });
    render();
  }
  openNote(path);
}

// ============================================================ speech in and out

export function startListening() {
  if (!speechSupported()) return feedback("Voice isn't available in this browser. Type instead, or set up offline voice below.");
  wantListening = true;
  stopWake();
  try {
    recognizer = makeRecognizer();
  } catch {
    return feedback("Couldn't start the microphone.");
  }
  recognizer.interimResults = true;
  recognizer.continuous = true;
  recognizer.maxAlternatives = 1;
  recognizer.onstart = () => {
    listening = true;
    updateVoiceUi();
  };
  recognizer.onresult = (event) => {
    let interim = "";
    const finals = [];
    for (let i = event.resultIndex ?? 0; i < event.results.length; i++) {
      const result = event.results[i];
      const transcript = result[0]?.transcript || "";
      if (result.isFinal) finals.push(transcript);
      else interim += transcript;
    }
    const live = $("#log-live");
    if (live) live.textContent = interim ? `“${interim.trim()}”` : "";
    for (const text of finals) if (text.trim()) handleUtterance(text.trim());
  };
  recognizer.onerror = (event) => {
    const messages = {
      "not-allowed": "Microphone blocked. Allow it in the browser's site settings, or type instead.",
      "service-not-allowed": "Microphone blocked. Allow it in the browser's site settings, or type instead.",
      "no-speech": "Didn't catch anything. Tap the mic and try again.",
      network: "Voice input needs the browser's speech service, which isn't reachable. Type instead.",
      "audio-capture": "No microphone found. Type instead.",
      "local-model": `Offline voice couldn't start (${event.message || "unknown error"}). Type instead, or set it up again below.`,
    };
    if (event.error === "network" && lv.installed() && !serviceDown) {
      serviceDown = true;
      feedback("The browser's speech service isn't reachable, so Cairn switched to offline voice on this device.");
      setTimeout(() => wantListening && $(".log-sheet") && startListening(), 300);
      return;
    }
    if (event.error !== "no-speech" && event.error !== "aborted") wantListening = false;
    if (event.error === "network" && !lv.installed()) return feedback(`${messages.network} Offline voice (below) works without it.`);
    if (messages[event.error]) feedback(messages[event.error]);
  };
  recognizer.onend = () => {
    listening = false;
    if (wantListening && prefs().keepListening && $(".log-sheet")) {
      setTimeout(() => wantListening && $(".log-sheet") && startListening(), 250);
    }
    updateVoiceUi();
  };
  try {
    recognizer.start();
    listening = true;
  } catch {
    /* already started */
  }
  updateVoiceUi();
}

export function stopListening(user = false) {
  if (user) wantListening = false;
  wantListening = false;
  listening = false;
  try {
    recognizer?.stop();
  } catch {
    /* not running */
  }
  updateVoiceUi();
}

function statusText() {
  if (!speechSupported()) return "No voice in this browser: type below";
  if (onDevice()) return listening ? "Listening on this device… speak naturally" : "Tap the mic and talk (on this device)";
  if (!online()) return "Offline: set up offline voice below, or type";
  return listening ? "Listening… speak naturally" : "Tap the mic and talk";
}

function updateVoiceUi() {
  const box = $(".log-voice");
  if (!box) return;
  box.classList.toggle("is-listening", listening);
  const mic = $(".log-mic");
  if (mic) {
    mic.setAttribute("aria-pressed", String(listening));
    mic.setAttribute("aria-label", listening ? "Stop listening" : "Start listening");
  }
  const status = $("#log-status");
  if (status) status.textContent = statusText();
  $(".log-voice")?.classList.toggle("is-offline", !online() && !onDevice());
  $(".log-voice")?.classList.toggle("is-local", onDevice());
}

export function readAloud(offset = 0) {
  const day = dl.addDays(today(), offset);
  const info = days().get(day);
  const when = offset === 0 ? "Today" : offset === -1 ? "Yesterday" : dl.prettyDay(day);
  const entries = info?.entries || [];
  const text = entries.length
    ? `${when}, you logged ${plural(entries.length, "thing")}. ${entries.map((e) => `${e.time ? `At ${e.time}, ` : ""}${dl.plainText(e.text).replace(/^Mood:\s*(?:[^\p{L}\p{N}\s(]+\s*)?/u, "mood ")}.`).join(" ")}`
    : `${when}, nothing is logged yet.`;
  feedback(text);
  speak(text);
  return text;
}

function speak(text) {
  if (!canSpeak()) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = speechLang();
  window.speechSynthesis.speak(utterance);
}

// ============================================================ weekly review

export async function weeklyReview(anyDay = today()) {
  return saveReview(dl.weeklyReview(days(), state.notes, anyDay));
}

export async function monthlyReview(anyDay = today()) {
  return saveReview(dl.monthlyReview(days(), state.notes, anyDay));
}

async function saveReview(review) {
  if (!canWrite()) return toast("This brain is read-only.");
  const path = `journal/${review.slug}.md`;
  const meta = { title: review.title, type: "journal", tags: ["review"], created: today() };
  const text = core.serializeNote(meta, review.body);
  const existing = state.files.find((f) => f.path === path);
  const { mtime } = await state.store.write(path, text, existing ? existing.mtime : null);
  upsertFile(path, text, mtime);
  scheduleGitRefresh();
  render();
  openNote(path);
  toast(`${existing ? "Updated" : "Saved"} ${review.title} review to brain/${path}`);
  return path;
}

// ============================================================ Journal & memories view

export function journalView() {
  const all = days();
  const now = today();
  const streak = dl.streaks(all, now);
  const range = state.moodRange === 90 ? 90 : 30;
  const series = dl.moodSeries(all, now, range);
  const avg = dl.averageMood(series);
  const weekFrom = dl.weekStart(now);
  let weekEntries = 0;
  for (const [day, info] of all) if (day >= weekFrom && day <= now) weekEntries += info.entries.length;
  const totalEntries = [...all.values()].reduce((n, info) => n + info.entries.length, 0);
  const pages = state.notes.filter((n) => n.type === "journal").sort((a, b) => (a.slug < b.slug ? 1 : -1));
  const editable = canWrite();
  const actions = editable
    ? `<button class="btn" data-jl="review">${icon("list")} Weekly review</button><button class="btn" data-review="month" data-review-day="${now}">${icon("list")} Monthly review</button><button class="btn" data-action="journal-today">${icon("journal")} Today's page</button><button class="btn primary" data-jl="log">${MIC} Log now</button>`
    : "";
  const recent = [...all.keys()].sort().reverse().filter((d) => all.get(d).entries.length).slice(0, 5);
  const todayInfo = all.get(now);
  const related = todayInfo ? linkedDays(all, todayInfo.entries, now) : [];
  const mindRange = [30, 90, 0].includes(state.mindRange) ? state.mindRange : 30;
  const { tags, links, people } = dl.mentions(all, mindRange ? dl.addDays(now, -(mindRange - 1)) : "0000-01-01", now);
  const due = editable ? dl.pendingReviews(all, state.notes, now) : [];
  return `${pageHead("Journal & memories", "Your daily log, one Markdown page per day. Tap the mic button (or press L) to log by voice; everything lands in brain/journal/ as plain text.", actions)}
    <section class="memory-stats">
      <div class="stat-card"><span class="stat-num">${streak.current}</span><span class="stat-label">day streak${streak.loggedToday ? "" : editable && streak.current ? " · log today to keep it" : ""}</span></div>
      <div class="stat-card"><span class="stat-num">${streak.longest}</span><span class="stat-label">longest streak</span></div>
      <div class="stat-card"><span class="stat-num">${[...all.values()].filter((i) => i.entries.length).length}</span><span class="stat-label">days logged · ${plural(totalEntries, "entry", "entries")}</span></div>
      <div class="stat-card"><span class="stat-num">${weekEntries}</span><span class="stat-label">entries this week</span></div>
      <div class="stat-card"><span class="stat-num">${avg === null ? "–" : `${avg.toFixed(1)}<small>/5</small>`}</span><span class="stat-label">mood, last ${range} days</span></div>
    </section>
    ${due.length ? reviewDueHtml(due) : ""}
    <div class="memory-grid">
      <section class="card memory-card heat-card">
        <div class="card-head"><h2>Every day</h2><span class="muted">last 12 months</span></div>
        ${heatmapHtml(all, now)}
        <div class="heat-legend"><span>Less</span><i class="heat l0"></i><i class="heat l1"></i><i class="heat l2"></i><i class="heat l3"></i><span>More</span></div>
        <div class="card-head" style="margin-top:14px"><h2>Mood</h2><span class="seg" role="group" aria-label="Mood range">${[30, 90].map((n) => `<button type="button" class="seg-btn ${range === n ? "on" : ""}" data-jl-range="${n}" aria-pressed="${range === n}">${n}d</button>`).join("")}</span></div>
        ${moodChart(series)}
      </section>
      <section class="card memory-card">
        <div class="card-head"><h2>Look back</h2></div>
        <ul class="lookbacks">${dl.lookBacks(all, now).map(lookBackRow).join("")}</ul>
        ${related.length ? `<div class="card-head" style="margin-top:12px"><h2>Linked to today</h2></div><ul class="related-days">${related.map((r) => `<li><button class="link-btn" data-jl-day="${r.day}">${esc(dl.prettyDay(r.day, "short"))}</button> <span class="muted">${r.shared.length ? r.shared.map(esc).join(", ") : `reads alike: ${r.words.map(esc).join(", ")}`}</span></li>`).join("")}</ul>` : ""}
        <div class="card-head" style="margin-top:12px"><h2>On your mind</h2><span class="seg" role="group" aria-label="On your mind range">${[[30, "30d"], [90, "90d"], [0, "All"]].map(([n, label]) => `<button type="button" class="seg-btn ${mindRange === n ? "on" : ""}" data-jl-mind="${n}" aria-pressed="${mindRange === n}">${label}</button>`).join("")}</span></div>
        ${tags.length || links.length || people.length ? `<div class="tags">${people.slice(0, 6).map(([p, n]) => `<button class="tag person" data-jl-search="@${esc(p)}">@${esc(p)} <span class="muted">&nbsp;${n}</span></button>`).join("")}${tags.slice(0, 6).map(([t, n]) => `<button class="tag" data-jl-search="#${esc(t)}">#${esc(t)} <span class="muted">&nbsp;${n}</span></button>`).join("")}${links.slice(0, 6).map(([l, n]) => `<button class="tag" data-jl-search="${esc(l)}">${esc(core.resolveLink(state.notes, l)?.title || l)} <span class="muted">&nbsp;${n}</span></button>`).join("")}</div>` : `<p class="muted">No #tags, @people or [[links]] ${mindRange ? `in the last ${mindRange} days` : "yet"}.</p>`}
      </section>
    </div>
    <section class="card memory-card">
      <div class="card-head"><h2>Search every day</h2></div>
      <input class="input" id="journal-search" type="search" placeholder="Find anything you logged… (words, #tags, note names)" autocomplete="off" value="${esc(state.journalQuery || "")}">
      <div id="journal-results">${searchResults(all, state.journalQuery || "")}</div>
    </section>
    <section class="card memory-card">
      <div class="card-head"><h2>Recent days</h2></div>
      ${recent.length ? recent.map((d) => dayBlock(d, all.get(d))).join("") : `<p class="muted">Nothing logged yet. ${editable ? "Tap the mic button at the bottom to start." : ""}</p>`}
    </section>
    <details class="card memory-card all-pages">
      <summary>All journal pages <span class="muted">(${pages.length})</span></summary>
      <div class="list">${pages.map((n) => `<button class="row-item" data-open="${esc(n.path)}">${icon("journal", "sm")}<span>${esc(n.title)}</span><span class="muted">${esc(n.path)}</span></button>`).join("")}</div>
    </details>`;
}

// Days that share #tags, @people or [[links]] with today come first; then days whose words read alike.
function linkedDays(all, entries, now, limit = 5) {
  const shared = dl.relatedDays(all, entries, now, limit).map((r) => ({ ...r, words: [] }));
  const seen = new Set(shared.map((r) => r.day));
  const alike = dl.similarDays(all, entries, now, limit).filter((r) => !seen.has(r.day)).map((r) => ({ ...r, shared: [] }));
  return shared.concat(alike).slice(0, limit);
}

function reviewDueHtml(due) {
  return `<section class="card review-due" aria-label="Reviews ready to write">
    ${due.map((r) => `<div class="review-due-row">
      <span>${icon("list")} <b>${esc(r.title)}</b> <span class="muted">has ${plural(r.entries, "entry", "entries")} and no review yet.</span></span>
      <button class="btn sm" type="button" data-review="${r.kind}" data-review-day="${r.day}">Write the ${r.kind === "month" ? "monthly" : "weekly"} review</button>
    </div>`).join("")}
  </section>`;
}

const HEAT_WEEKS = 53;

function heatmapHtml(all, now) {
  const columns = dl.heatmap(all, now, HEAT_WEEKS);
  let lastMonth = "";
  const months = columns.map((col) => {
    const month = new Date(`${col[0].day}T12:00:00`).toLocaleDateString("en-US", { month: "short" });
    const label = month !== lastMonth ? month : "";
    lastMonth = month;
    return `<span>${label}</span>`;
  }).join("");
  const cells = columns.map((col) => `<div class="heat-col">${col.map((c) => c.future
    ? `<i class="heat future"></i>`
    : `<button class="heat l${c.level}${c.day === now ? " is-today" : ""}" data-jl-day="${c.day}" title="${esc(dl.prettyDay(c.day, "short"))}: ${plural(c.count, "entry", "entries")}" aria-label="${esc(dl.prettyDay(c.day, "short"))}, ${plural(c.count, "entry", "entries")}"></button>`).join("")}</div>`).join("");
  return `<div class="heatmap" role="group" aria-label="Days logged"><div class="heat-months">${months}</div><div class="heat-grid">${cells}</div></div>`;
}

function moodChart(series) {
  const left = 28;
  const width = 300;
  const step = (width - left - 8) / Math.max(1, series.length - 1);
  const yOf = (score) => 70 - (score - 1) * 15;
  const points = series.map((p, i) => ({ x: left + i * step, y: p.score === null ? null : yOf(p.score), p }));
  const known = points.filter((pt) => pt.y !== null);
  if (!known.length) return `<p class="muted mood-empty">No moods yet. Pick a mood in the log, or say “mood good”.</p>`;
  const path = known.map((pt, i) => `${i ? "L" : "M"}${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`).join(" ");
  const area = `${path} L${known[known.length - 1].x.toFixed(1)} 70 L${known[0].x.toFixed(1)} 70 Z`;
  const rows = [1, 3, 5].map((score) => `<line x1="${left}" x2="${width}" y1="${yOf(score)}" y2="${yOf(score)}" class="grid"/>
    <text x="2" y="${yOf(score) + 3}" class="axis">${score}/5</text>`).join("");
  return `<svg class="mood-chart" viewBox="0 0 ${width} 92" role="img" aria-label="Mood over the last ${series.length} days, average ${(known.reduce((sum, pt) => sum + pt.p.score, 0) / known.length).toFixed(1)} out of 5">
    ${rows}
    <path d="${area}" class="area"/>
    <path d="${path}" class="line"/>
    ${known.map((pt) => `<circle cx="${pt.x.toFixed(1)}" cy="${pt.y.toFixed(1)}" r="3"><title>${esc(dl.prettyDay(pt.p.day, "short"))}: ${pt.p.score.toFixed(1)}/5</title></circle>`).join("")}
    <text x="${left}" y="88" class="axis">${esc(dl.prettyDay(series[0].day, "short"))}</text>
    <text x="${width}" y="88" class="axis" text-anchor="end">today</text>
  </svg>`;
}

function lookBackItem(e) {
  return `<li>${e.time ? `<span class="entry-time">${esc(e.time)}</span> ` : ""}${inline(e.text.replace(/^(Task|Idea|Decision|Note):\s*/, "$1: ").replace(/^Mood:\s*(?:[^\p{L}\p{N}\s(]+\s*)?/u, "Mood: "))}</li>`;
}

function lookBackRow({ label, day, info }) {
  const entries = info?.entries || [];
  return `<li class="lookback ${entries.length ? "" : "empty"}">
    <div class="lookback-head"><strong>${esc(label)}</strong> <button class="link-btn muted" data-jl-day="${day}">${esc(dl.prettyDay(day, "short"))}</button></div>
    ${entries.length
      ? `<ul>${entries.slice(0, 3).map(lookBackItem).join("")}</ul>${entries.length > 3 ? `<details class="lookback-more"><summary>and ${entries.length - 3} more</summary><ul>${entries.slice(3).map(lookBackItem).join("")}</ul></details>` : ""}`
      : `<p class="muted">Nothing logged.</p>`}
  </li>`;
}

function dayBlock(day, info) {
  return `<div class="day-block">
    <button class="day-title link-btn" data-jl-day="${day}">${esc(dl.prettyDay(day))}</button>
    <ul class="day-entries">${info.entries.map((e) => `<li><span class="entry-time">${esc(e.time || "·")}</span><span>${inline(e.text.replace(/^Mood:\s*(?:[^\p{L}\p{N}\s(]+\s*)?/u, "Mood: "))}</span></li>`).join("")}</ul>
  </div>`;
}

function searchResults(all, query) {
  if (!query.trim()) return `<p class="muted search-hint">Type to search across ${plural(all.size, "day")}.</p>`;
  const hits = dl.searchEntries(all, query);
  if (!hits.length) return `<p class="muted search-hint">Nothing matches “${esc(query)}”.</p>`;
  const exact = hits.filter((h) => !h.fuzzy).length;
  const close = hits.length - exact;
  const summary = exact ? `${plural(exact, "entry", "entries")} match${close ? `, ${close} close` : ""}` : `No exact match. ${plural(close, "close match", "close matches")}`;
  return `<p class="muted search-hint" role="status">${summary}</p><ul class="search-hits">${hits.map(({ day, entry, fuzzy }) => `<li${fuzzy ? ' class="is-fuzzy"' : ""}><button class="link-btn" data-jl-day="${day}">${esc(dl.prettyDay(day, "short"))}</button><span class="entry-time">${esc(entry.time)}</span><span>${inline(entry.text)}${fuzzy ? ' <span class="pill">close match</span>' : ""}</span></li>`).join("")}</ul>`;
}

// Month names wider than their week column would be cut at the scrolled edges ("pr" for Apr): hide those.
function fitMonthLabels(heat) {
  const box = heat.getBoundingClientRect();
  for (const label of heat.querySelectorAll(".heat-months span")) {
    if (!label.textContent) continue;
    const rect = label.getBoundingClientRect();
    const right = rect.left + label.scrollWidth;
    label.style.visibility = rect.left >= box.left - 1 && right <= box.right + 1 ? "" : "hidden";
  }
}

export function wireJournal() {
  journalWired?.abort();
  journalWired = new AbortController();
  const signal = journalWired.signal;
  const view = $("#view");
  const heat = $(".heatmap");
  if (heat) {
    heat.scrollLeft = heat.scrollWidth; // newest weeks first; scroll left for the rest of the year
    fitMonthLabels(heat);
    heat.addEventListener("scroll", () => fitMonthLabels(heat), { signal, passive: true });
  }
  const search = $("#journal-search");
  search?.addEventListener("input", () => {
    state.journalQuery = search.value;
    $("#journal-results").innerHTML = searchResults(days(), search.value);
  }, { signal });
  view.addEventListener("click", (event) => {
    const day = event.target.closest("[data-jl-day]");
    if (day) {
      event.preventDefault();
      return openDay(day.dataset.jlDay);
    }
    const term = event.target.closest("[data-jl-search]");
    if (term) {
      state.journalQuery = term.dataset.jlSearch;
      const input = $("#journal-search");
      input.value = state.journalQuery;
      $("#journal-results").innerHTML = searchResults(days(), state.journalQuery);
      input.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
    const mindButton = event.target.closest("[data-jl-mind]");
    if (mindButton) {
      state.mindRange = Number(mindButton.dataset.jlMind);
      return render();
    }
    const rangeButton = event.target.closest("[data-jl-range]");
    if (rangeButton) {
      state.moodRange = Number(rangeButton.dataset.jlRange);
      return render();
    }
    const action = event.target.closest("[data-jl]");
    if (!action) return;
    if (action.dataset.jl === "log") openLogSheet();
    if (action.dataset.jl === "review") weeklyReview();
  }, { signal });
}

// ============================================================ Home: today's log at a glance

export function homeLogStrip() {
  const all = days();
  const now = today();
  if (!canWrite() && !all.size) return "";
  const streak = dl.streaks(all, now);
  const entries = all.get(now)?.entries || [];
  const memory = dl.lookBacks(all, now).filter((l) => l.label !== "Yesterday" && l.info?.entries?.length).pop();
  const last = entries.slice(-2).reverse();
  const due = canWrite() ? dl.pendingReviews(all, state.notes, now)[0] : null;
  return `<section class="home-log" aria-label="Today's log">
    <div class="home-log-main">
      <div class="home-log-head">
        <h2>Today's log</h2>
        <span class="muted">${plural(entries.length, "entry", "entries")}${streak.current ? ` · ${streak.current}-day streak` : ""}</span>
      </div>
      ${last.length
        ? `<ul class="home-log-entries">${last.map((e) => `<li><span class="entry-time">${esc(e.time || "·")}</span><span>${inline(e.text.replace(/^Mood:\s*(?:[^\p{L}\p{N}\s(]+\s*)?/u, "Mood: "))}</span></li>`).join("")}</ul>`
        : `<p class="muted">${canWrite() ? "Nothing yet. Say what you did, how you feel, or a todo; Cairn files it." : "Nothing logged today."}</p>`}
    </div>
    ${memory
      ? `<button class="home-log-memory" data-jl-day="${memory.day}" type="button"><span class="eyebrow">${esc(memory.label)}</span><span>${inline(memory.info.entries[0].text.replace(/^Mood:\s*(?:[^\p{L}\p{N}\s(]+\s*)?/u, "Mood: "))}</span></button>`
      : ""}
    <div class="home-log-actions">
      ${due ? `<button class="btn" type="button" data-review="${due.kind}" data-review-day="${due.day}">${icon("list")} ${esc(due.title)} review</button>` : ""}
      ${canWrite() ? `<button class="btn primary" type="button" data-log-open>${MIC} Log by voice</button>` : ""}
      <button class="btn ghost" type="button" data-go="journal">Memories ${icon("arrow", "sm")}</button>
    </div>
  </section>`;
}
