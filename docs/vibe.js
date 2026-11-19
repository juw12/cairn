// Vibe: live progress of AI-assisted coding sessions, what came out of them, and what people said.
// A session is brain/sessions/YYYY-MM-DD-slug.md (status: live while you work, a log of timestamped
// steps); feedback is brain/feedback/slug.md with an optional 1-5 rating. With the local app the view
// polls the server, so steps your assistant logs from the terminal (cairn vibe log) show up as they happen.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { $, $$, ask, icon, pageHead, plural, statusPill, toast, today } from "./ui.js";
import { createNote, fileByPath, noteByPath, readOnly, saveNote, state, upsertFile } from "./state.js";
import { renderNav, renderView } from "./app.js";
import { agentGroup, deviceList, deviceNotes, deviceOf, estimateEffort, estimateProgress, formatMinutes, githubAccounts, githubBudget, githubMoves, machineReading, movePending } from "./progress.js";
import { QUIET_MIN, compact, minutesText, recap, sessionState } from "./recap.js";
import { chatReady } from "./chat.js";
import { openShare } from "./share.js";
import { boardHtml, openPanel } from "./vibelayout.js";

const POLL_MS = 3000;
const RESULT_LABEL = { shipped: "Shipped", partial: "Partial", dropped: "Dropped", done: "Done" };
const DOT_LABEL = { shipped: "Shipped: changed or committed code", done: "No code change seen", unknown: "Outcome unknown: the folder is not a git repo", live: "Live" };
// Detected sessions in a folder without git have no evidence either way, so they get their own state.
const outcomeOf = (note) => (note.status === "done" && note.meta.git === "no" ? "unknown" : note.status);
const SOURCES = { opencode: "OpenCode", vector: "Vector", logged: "Logged" };
// Outcome chips double as the legend for the row dots; each one filters the list to that outcome.
const OUTCOMES = { live: "live", shipped: "shipped", partial: "partial", dropped: "dropped", done: "no code change", unknown: "no git" };
const PAGE = 40;
const AGENT_HOOK = [
  'Before you start:  cairn vibe start "<goal>" --agent <your name>',
  'After each step:   cairn vibe log "<what you just did>"',
  "Log my prompts:    cairn vibe log \"<prompt>\" --kind prompt",
  "After a skill:     cairn vibe skill /refine \"6.5 -> 8.7\"   (/council, /ambitious-founder, /precedent...)",
  "Run tests with:    cairn vibe check -- <test command>",
  "When finished:     cairn vibe end --result shipped|partial|dropped \"<one-line summary>\"",
  "Parallel agents:   set CAIRN_SESSION=<session slug> in each terminal (start prints it)",
].join("\n");

let pollTimer = null;
// The overview band: its one-second clock, and what it showed last so that only real changes move.
let clockTimer = null;
let ovSeen = null;
// When the band last started drawing itself in, and how long that takes (the ov-reveal animation in styles.css).
let ovEnterAt = -Infinity;
const ENTER_MS = 700;
const LANES = 6;
// With several AIs running at once, each shows this many lanes.
const LANES_EACH = 3;
const TRACK = 30;
const PEEK = 5;

// ============================================================ helpers

const clock = () => new Date().toTimeString().slice(0, 5);
const stamp = () => `${today()}T${clock()}`;
const oneLine = (text) => String(text).replace(/\s+/g, " ").trim();
const canPoll = () => typeof state.store?.vibe === "function";
// One brain can hold the sessions of several computers (each note names its own in `machine`). The page shows
// them all, or one computer alone when you pick it in the device bar. null is "all"; "" is the sessions that
// name no computer. The choice is kept in this browser.
const DEVICE_KEY = "cairn.vibeDevice";
let deviceLoaded = false;

function shownDevice() {
  if (!deviceLoaded) {
    deviceLoaded = true;
    try {
      const kept = JSON.parse(localStorage.getItem(DEVICE_KEY));
      if (typeof kept === "string" && state.vibeDevice === undefined) state.vibeDevice = kept;
    } catch {
      /* storage is off: the choice lasts until the page closes */
    }
  }
  return typeof state.vibeDevice === "string" ? state.vibeDevice : null;
}

function vibeNotes() {
  const device = shownDevice();
  return device === null ? state.notes : deviceNotes(state.notes, device);
}

export function setVibeDevice(device) {
  state.vibeDevice = typeof device === "string" ? device : null;
  deviceLoaded = true;
  try {
    if (state.vibeDevice === null) localStorage.removeItem(DEVICE_KEY);
    else localStorage.setItem(DEVICE_KEY, JSON.stringify(state.vibeDevice));
  } catch {
    /* storage is off */
  }
  state.vibeLimit = PAGE;
  renderView();
  $(".vibe-devices .chip.on")?.focus({ preventScroll: true });
}

// The computer the sessions run on: measured now by the local app, else the newest machines/ note a watcher left.
// Another computer picked in the device bar is read from its own note, never from this one's live reading.
const machine = () => {
  const device = shownDevice();
  const here = device === null || !state.vibeHere || device === state.vibeHere;
  return machineReading(here ? state.vibeMachine : null, vibeNotes());
};

// Folds inside a block (a recap section, one AI in the overview, a session row) stay as you left them through
// live updates. The section folds are also kept on this device, so the page opens the way you arranged it.
const FOLD_KEY = "cairn.vibeFolds";
const KEPT_FOLD = /^(rc-running$|rc-stale$|rc-finished-all$|ov-agent:)/;
let foldsLoaded = false;

function folds() {
  if (!foldsLoaded) {
    foldsLoaded = true;
    let kept = {};
    try {
      kept = JSON.parse(localStorage.getItem(FOLD_KEY)) || {};
    } catch {
      /* storage is off: folds last until the page closes */
    }
    state.vibeOpenBlocks = { ...kept, ...(state.vibeOpenBlocks || {}) };
  }
  return state.vibeOpenBlocks || (state.vibeOpenBlocks = {});
}

function foldOpen(key, fallback = false) {
  const saved = folds()[key];
  return typeof saved === "boolean" ? saved : fallback;
}

function setFold(key, open) {
  state.vibeOpenBlocks = { ...folds(), [key]: open };
  if (!KEPT_FOLD.test(key)) return;
  try {
    const kept = Object.entries(state.vibeOpenBlocks).filter(([name]) => KEPT_FOLD.test(name));
    localStorage.setItem(FOLD_KEY, JSON.stringify(Object.fromEntries(kept)));
  } catch {
    /* storage is off */
  }
}

function startedKey(note) {
  return `${note.meta.started || note.meta.created || ""} ${note.path}`;
}

// Every live session, newest first: parallel agents (one per terminal or worktree) each keep their own.
export function liveSessions() {
  return state.notes
    .filter((n) => n.type === "session" && n.status === "live")
    .sort((a, b) => (startedKey(a) < startedKey(b) ? 1 : -1));
}

// The newest live session of this computer (the local app's git reading is about it); any computer's when
// this one has none or is not named.
export function liveSession() {
  const live = liveSessions();
  const here = state.vibeHere ? live.find((note) => [state.vibeHere, ""].includes(deviceOf(note))) : null;
  return here || live[0] || null;
}

// The live sessions the page lists: the picked computer's, or every computer's.
function shownLive() {
  const device = shownDevice();
  return device === null ? liveSessions() : liveSessions().filter((note) => deviceOf(note) === device);
}

function elapsedMinutes(note) {
  const start = core.stampMinutes(note.meta.started);
  const now = core.stampMinutes(stamp());
  return start === null || now === null ? 0 : Math.max(0, now - start);
}

function duration(minutes) {
  if (!minutes) return "0m";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours}h ${String(rest).padStart(2, "0")}m` : `${rest}m`;
}

// Where a session came from: a watcher (`source: opencode|vector`) or logged by hand / by the assistant.
export function sessionSource(note) {
  const source = String(note.meta.source || "").toLowerCase();
  return source in SOURCES && source !== "logged" ? source : "logged";
}

// Which AI ran a session, as the filter chips and the live groups name it.
function groupOf(note) {
  const group = agentGroup(note.meta);
  return group.key === "all" ? { key: "agent:all", label: group.label } : group;
}

// Live sessions bucketed by AI. Given newest first, the AI holding the newest session leads.
function liveGroups(live) {
  const groups = new Map();
  for (const note of live) {
    const group = groupOf(note);
    if (!groups.has(group.key)) groups.set(group.key, { ...group, notes: [] });
    groups.get(group.key).notes.push(note);
  }
  return [...groups.values()];
}

function folderName(note) {
  const folder = typeof note.meta.folder === "string" ? note.meta.folder.replace(/[\\/]+$/, "") : "";
  return folder ? folder.split(/[\\/]/).pop() : "";
}

function sessionDayOf(note) {
  const key = startedKey(note);
  return /^\d{4}-\d{2}-\d{2}/.test(key) ? key.slice(0, 10) : "";
}

function dayLabel(day) {
  if (!day) return "Undated";
  if (day === today()) return "Today";
  const date = new Date(`${day}T12:00:00`);
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function sessionNotes() {
  return vibeNotes().filter((n) => n.type === "session").sort((a, b) => (startedKey(a) < startedKey(b) ? 1 : -1));
}

// The AI and outcome filters as the list applies them: one that matches nothing falls back to "all".
function vibeFilters(sessions) {
  const counts = Object.create(null);
  const labels = Object.create(null);
  counts.all = sessions.length;
  for (const note of sessions) {
    const group = groupOf(note);
    counts[group.key] = (counts[group.key] || 0) + 1;
    labels[group.key] = group.label;
  }
  // Busiest AI first; sessions that name no AI go last.
  const present = Object.keys(labels).sort((a, b) => (a === "other") - (b === "other") || counts[b] - counts[a] || (a < b ? -1 : 1));
  const filter = state.vibeFilter !== "all" && labels[state.vibeFilter] ? state.vibeFilter : "all";
  const fromSource = filter === "all" ? sessions : sessions.filter((note) => groupOf(note).key === filter);
  const outcomeCounts = {};
  for (const note of fromSource) outcomeCounts[outcomeOf(note)] = (outcomeCounts[outcomeOf(note)] || 0) + 1;
  const outcome = state.vibeOutcome in OUTCOMES && outcomeCounts[state.vibeOutcome] ? state.vibeOutcome : "all";
  return { counts, labels, present, filter, fromSource, outcomeCounts, outcome };
}

export function setVibeFilter(source) {
  state.vibeFilter = typeof source === "string" && source ? source : "all";
  state.vibeLimit = PAGE;
  renderView();
}

export function setVibeOutcome(outcome) {
  state.vibeOutcome = outcome in OUTCOMES && state.vibeOutcome !== outcome ? outcome : "all";
  state.vibeLimit = PAGE;
  renderView();
}

// An overview tile counts every source, so pressing it shows that status from every source; pressing it again clears it.
export function setOverviewOutcome(outcome) {
  const now = vibeFilters(sessionNotes());
  const on = now.outcome === outcome && now.filter === "all";
  state.vibeOutcome = on || !(outcome in OUTCOMES) ? "all" : outcome;
  if (!on) state.vibeFilter = "all";
  state.vibeLimit = PAGE;
  renderView();
  $(`.ov-tile[data-outcome="${CSS.escape(outcome)}"]`)?.focus({ preventScroll: true });
}

function flash(el) {
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1600);
}

// From a lane in the overview to that session's row in the live recap, opened to its steps.
export function jumpToLive(path) {
  const data = recap(vibeNotes(), stamp());
  setFold(`rc-row:${path}`, true);
  setFold("rc-running", true);
  if (data.stale.some((entry) => entry.note.path === path)) setFold("rc-stale", true);
  if (data.live.findIndex((entry) => entry.note.path === path) >= LIVE_SHOWN) setFold("rc-live-more", true);
  if (!openPanel("recap")) renderView();
  const row = $(`.rc-row[data-path="${CSS.escape(path)}"]`);
  if (!row) return;
  row.scrollIntoView({ block: "start" });
  $(".rc-toggle", row)?.focus({ preventScroll: true });
  const card = $(".vibe-live", row);
  if (card) flash(card);
}

// A row in the live recap, opened to its steps or closed again.
export function toggleRow(path) {
  const row = $(`.rc-row[data-path="${CSS.escape(path)}"]`);
  setFold(`rc-row:${path}`, !row?.classList.contains("open"));
  renderView();
  $(`.rc-row[data-path="${CSS.escape(path)}"] .rc-toggle`)?.focus({ preventScroll: true });
}

// From the overview's short list to the full one, with focus on the chip that is filtering it.
export function jumpToList() {
  openPanel("sessions");
  const list = $(".vibe-sessions");
  if (!list) return;
  list.scrollIntoView({ block: "start" });
  ($(".vibe-outcomes [aria-pressed=true]") || $(".vibe-sessions .day-head"))?.focus({ preventScroll: true });
  flash(list);
}

// Leaving the page forgets what the overview showed, so it draws itself in again on the next visit.
export function leaveVibe() {
  ovSeen = null;
}

// Folded days stay folded across live updates; a folded day shows its totals and costs no rows of the page.
export function toggleSessionDay(day) {
  const folded = state.vibeFolded || (state.vibeFolded = new Set());
  if (folded.has(day)) folded.delete(day);
  else folded.add(day);
  renderView();
  $(`.day-head[data-day="${CSS.escape(day)}"]`)?.focus();
}

// From the chart: open one day, make sure no filter or page limit hides it, and bring it into view.
export function jumpToDay(day) {
  const folded = state.vibeFolded || (state.vibeFolded = new Set());
  folded.delete(day);
  const onDay = vibeNotes().filter((note) => note.type === "session" && sessionDayOf(note) === day);
  const { filter } = vibeFilters(sessionNotes());
  const passes = (note) =>
    (filter === "all" || groupOf(note).key === filter) &&
    (!(state.vibeOutcome in OUTCOMES) || outcomeOf(note) === state.vibeOutcome);
  if (!onDay.some(passes)) {
    state.vibeFilter = "all";
    state.vibeOutcome = "all";
  }
  state.vibeJumped = day;
  if (!openPanel("sessions")) renderView();
  const headFor = () => $(`.day-head[data-day="${CSS.escape(day)}"]`);
  if (!headFor()) {
    state.vibeLimit = Infinity;
    renderView();
  }
  const head = headFor();
  if (!head) return;
  const group = head.closest(".day-group");
  group.scrollIntoView({ block: "start" });
  // Days above may have been skipped by content-visibility; settle once they have real sizes.
  requestAnimationFrame(() => group.scrollIntoView({ block: "start" }));
  head.focus({ preventScroll: true });
  group.classList.add("flash");
  setTimeout(() => group.classList.remove("flash"), 1600);
}

// The way back from a deep jump: scroll up to the chart and put focus on the bar you came from.
export function backToChart(day) {
  state.vibeJumped = null;
  if (!openPanel("activity")) renderView();
  const chart = $(".vibe-activity");
  if (!chart) return;
  chart.scrollIntoView({ block: "start" });
  $(`.vibe-activity [data-action=vibe-jump][data-day="${CSS.escape(day || "")}"]`)?.focus({ preventScroll: true });
}

export function toggleWhy(label) {
  state.vibeWhy = state.vibeWhy === label ? null : label;
  renderView();
  $(`.vibe-figures [data-why="${CSS.escape(label)}"]`)?.focus({ preventScroll: true });
}

export function foldAllDays(dayList) {
  const days = String(dayList || "").split(",");
  const folded = state.vibeFolded || (state.vibeFolded = new Set());
  const all = days.every((day) => folded.has(day));
  for (const day of days) {
    if (all) folded.delete(day);
    else folded.add(day);
  }
  renderView();
}

export function showMoreSessions(all = false) {
  state.vibeLimit = all ? Infinity : (state.vibeLimit || PAGE) + PAGE;
  renderView();
}

function stars(rating) {
  if (!rating) return "";
  return `<span class="stars" role="img" aria-label="${rating} out of 5">${[1, 2, 3, 4, 5].map((n) => `<span class="${n <= rating ? "on" : "off"}">${icon("star", "sm")}</span>`).join("")}</span>`;
}

function linkTarget(value) {
  const match = /^\[\[([^\]|#]+)/.exec(typeof value === "string" ? value.trim() : "");
  return match ? match[1].trim() : "";
}

function noteLink(value) {
  const target = linkTarget(value);
  if (!target) return "";
  const found = core.resolveLink(state.notes, target);
  return found
    ? `<a href="#" data-open="${esc(found)}">${esc(noteByPath(found)?.title || target)}</a>`
    : `<span class="muted">${esc(target)}</span>`;
}

// ============================================================ actions

export async function startSession() {
  const value = await ask({
    title: "Start a vibe session",
    text: "What are you building right now? Your assistant can log steps to it with cairn vibe log.",
    placeholder: "e.g. Add dark mode",
    confirm: "Start",
  });
  const title = oneLine(value || "");
  if (!title) return;
  // The commit the session starts from, so only commits made during it are counted.
  const extraMeta = { started: stamp() };
  if (state.vibeGit?.head) extraMeta.base = state.vibeGit.head;
  if (state.vibeHere) extraMeta.machine = state.vibeHere;
  const note = await createNote({
    type: "session",
    title,
    status: "live",
    extraMeta,
    body: `## Goal\n\n${title}\n\n## Log\n\n- ${clock()} start: ${title}\n`,
  });
  // A session you start by hand opens to its log form: you are the one who writes its steps.
  if (note) setFold(`rc-row:${note.path}`, true);
  state.vibeGit = null;
  renderNav();
  renderView();
}

async function appendLog(path, line, changes = {}) {
  const note = noteByPath(path);
  if (!note) return;
  const body = `${note.body.replace(/\s*$/, "")}\n- ${clock()} ${line}\n`;
  await saveNote(path, { ...changes, body });
}

export async function logStep(kind, text, path = "") {
  const live = path ? noteByPath(path) : liveSession();
  text = oneLine(text);
  if (!live || live.status !== "live" || !text || !core.LOG_KINDS.includes(kind)) return;
  await appendLog(live.path, `${core.logKind(text, kind)}: ${text}`);
  renderView();
  $(`.vibe-log-form[data-session="${CSS.escape(live.path)}"] input`)?.focus();
}

export async function endSession(result, path = "") {
  const live = path ? noteByPath(path) : liveSession();
  if (!live || live.status !== "live" || !core.FINISHED.includes(result)) return;
  const properties = { ended: stamp() };
  // Git churn is repo-wide, so it only belongs to the session the poller follows (the newest).
  const git = live.path === liveSession()?.path ? state.vibeGit : null;
  if (git?.available) {
    properties.commits = String(git.commits.length);
    properties.added = String(git.commits.reduce((total, c) => total + c.added, git.added));
    properties.removed = String(git.commits.reduce((total, c) => total + c.removed, git.removed));
  }
  await appendLog(live.path, `end: ${result}`, { status: result, properties });
  toast(`Session ended: ${RESULT_LABEL[result]}. It's saved in brain/${live.path}`);
  renderNav();
  renderView();
}

export async function addFeedback(form) {
  const text = form.elements.text.value.trim();
  if (!text) return;
  const rating = Number(form.elements.rating.value) || 0;
  const from = oneLine(form.elements.from.value);
  const about = form.elements.about.value;
  const first = oneLine(text.split("\n")[0]);
  const title = first.length <= 80 ? first : `${first.slice(0, 77).trimEnd()}...`;
  const extraMeta = {};
  if (from) extraMeta.from = from;
  if (rating >= 1 && rating <= 5) extraMeta.rating = String(rating);
  if (about) extraMeta.about = `[[${about}]]`;
  await createNote({ type: "feedback", title, extraMeta, body: text !== title ? `${text}\n` : "" });
  state.vibeDraft = null;
  toast("Feedback saved to brain/feedback/");
  renderNav();
  renderView();
}

// The demo starts with no sessions; this fills in a believable week so the page can be explored.
export async function loadSampleSessions() {
  const day = (offset) => {
    const d = new Date();
    d.setDate(d.getDate() - offset);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };
  const samples = [
    [6, "Offline route cache", "shipped", "09:10", "10:40", 4, 310, 40, ["prompt: cache tiles per route corridor", "fail: pytest (14s) · 2 failed", "skill: /refine app trailmix · 6.4 -> 8.6", "pass: pytest (13s) · 48 passed"]],
    [4, "Elevation chart tooltips", "partial", "20:05", "21:00", 2, 95, 12, ["prompt: tooltips follow the cursor", "skill: /council tooltips ux · 7.1", "pass: npm test (6s) · 31 passed"]],
    [3, "Try a WebGL renderer", "dropped", "14:00", "14:45", 1, 60, 60, ["skill: /precedent map renderers · MapLibre already solves this", "fail: npm test (7s) · 9 failed"]],
    [1, "Share a live trip link", "shipped", "10:00", "11:30", 5, 420, 31, ["skill: /ambitious-founder share link · would pay: 4/10 buyers", "prompt: signed link, no account", "pass: pytest (12s) · 52 passed"]],
  ];
  const slugs = [];
  for (const [offset, title, status, start, end, commits, added, removed, steps] of samples) {
    const log = [`- ${start} start: ${title}`, ...steps.map((s) => `- ${start} ${s}`), `- ${end} end: ${status}`].join("\n");
    const note = await createNote({
      type: "session",
      title,
      status,
      extraMeta: { started: `${day(offset)}T${start}`, ended: `${day(offset)}T${end}`, agent: "claude", commits: String(commits), added: String(added), removed: String(removed) },
      body: `## Goal\n\n${title}\n\n## Log\n\n${log}\n`,
    });
    slugs.push(note.slug);
  }
  const feedback = [
    ["The share link opened first time, no account needed", "5", "@ben", slugs[3], "addressed"],
    ["Tooltips jitter on Android", "3", "@dan", slugs[1], "new"],
    ["Offline maps saved me on the ridge", "5", "beta tester", slugs[0], "addressed"],
  ];
  for (const [title, rating, from, about, status] of feedback) {
    await createNote({ type: "feedback", title, status, extraMeta: { from, rating, about: `[[${about}]]` }, body: "" });
  }
  toast("Loaded a sample week: 4 sessions and 3 pieces of feedback.");
  renderNav();
  renderView();
}

// Hand a private repo to another of your GitHub accounts, so its Actions minutes count there. Nothing changes
// hands on the press: GitHub mails the new owner, and the repo moves when that link is opened.
export async function moveRepo(repo, from, to) {
  if (!repo || !from || !to || typeof state.store?.githubMove !== "function") return;
  const sure = await ask({
    title: `Move ${repo} to ${to}?`,
    text: `GitHub Actions minutes of ${repo} then count against ${to}'s free minutes instead of ${from}'s.`,
    list: [
      `GitHub mails ${to} a link. Nothing moves until it is opened, within a day.`,
      `${from} stays on the repo as a collaborator and can still push.`,
      "Issues, secrets and history go along, and the old address redirects.",
      `To take it back, ${to} moves it again from the repo's Settings on GitHub.`,
    ],
    confirm: "Ask GitHub to move it",
    noInput: true,
    danger: true,
  });
  if (!sure) return;
  try {
    const result = await state.store.githubMove(repo, from, to);
    if (result.file) upsertFile(result.file.path, result.file.text, result.file.mtime);
    toast(result.message);
    renderView();
  } catch (error) {
    toast(error.message, { error: true });
  }
}

// Make another of your GitHub accounts the one this computer pushes as. The first time, git is told to ask the
// GitHub CLI for its sign-in (one setting in git's config), so that is said before anything changes. After
// that a press is the whole switch: it is as easily pressed back.
export async function switchPusher(account) {
  const push = state.vibePush;
  if (!account || !push || typeof state.store?.githubPush !== "function") return;
  if (push.viaCli && push.sure && sameAccount(account, push.pusher)) return;
  if (!push.viaCli) {
    const sure = await ask({
      title: `Push from this computer as ${account}?`,
      text: push.pusher
        ? `git signs in to GitHub as ${push.pusher} here, with a sign-in it saved by itself. To follow this switch it has to ask the GitHub CLI instead.`
        : "git has to ask the GitHub CLI for its GitHub sign-in to follow this switch.",
      list: [
        "Cairn runs gh auth setup-git once. That adds one setting to your git config, for github.com only.",
        "From then on git push, gh and an assistant working on this computer all go out as the account picked here.",
        "Commits keep their author. Only the sign-in changes: the name GitHub shows on a push, a pull request and a release.",
        "The saved sign-in is not deleted. To go back to it: git config --global --unset-all credential.https://github.com.helper",
      ],
      confirm: `Push as ${account}`,
      noInput: true,
    });
    if (!sure) return;
  }
  for (const button of $$("[data-action=vibe-gh-push]")) button.disabled = true;
  try {
    const { message, ...now } = await state.store.githubPush(account);
    state.vibePush = now;
    state.vibePushKey = JSON.stringify(now);
    toast(message);
  } catch (error) {
    toast(error.message, { error: true });
  }
  renderView();
}

export function copyAgentHook() {
  navigator.clipboard?.writeText(AGENT_HOOK).then(
    () => toast("Copied. Paste it into AGENTS.md, CLAUDE.md or your assistant's instructions."),
    () => toast("Couldn't copy; select the text instead.", { error: true }),
  );
}

// ============================================================ live progress: percent, time left, effort

const SAME_AGENT_SAMPLES = 10;
const EFFORT_LABEL = { light: "Light", medium: "Medium", heavy: "Heavy" };
const EFFORT_MEANING = {
  light: "A small, quick change so far.",
  medium: "A solid chunk of work: some time, some code.",
  heavy: "Big or bumpy: long running, lots of changes or failing checks.",
};

const agentOf = (note) => String(note.meta.agent || note.meta.source || "").toLowerCase();

// Minutes of finished sessions to compare a live one with: the same agent's when there are enough, else all of them.
function pastDurations(live) {
  const finished = state.notes.filter((n) => n.type === "session" && n.status !== "live");
  const minutes = (notes) => notes.map((n) => core.sessionMinutes(n)).filter((m) => m > 0 && m < 1440);
  const agent = agentOf(live);
  const same = agent ? minutes(finished.filter((n) => agentOf(n) === agent)) : [];
  if (same.length >= SAME_AGENT_SAMPLES) return { durations: same, agent };
  return { durations: minutes(finished), agent: "" };
}

function clockIn(minutes) {
  const at = new Date(Date.now() + minutes * 60000);
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}

function liveEstimate(live, newest) {
  const log = core.sessionLog(live.body);
  const elapsed = elapsedMinutes(live);
  const { durations, agent } = pastDurations(live);
  const startedClock = String(live.meta.started || "").slice(11, 16);
  const progress = estimateProgress({ log, elapsed, startedClock, durations });
  // Git figures cover the whole repo since the newest session started, so only that card uses them.
  const git = newest && state.vibeGit?.available ? state.vibeGit : null;
  // The load is the whole computer's, so it only speaks for a session that is working right now, and only while the reading is recent.
  const reading = machine();
  const now = sessionState(live, core.stampMinutes(stamp()));
  const load = reading?.fresh && now.key === "working" && now.idle < QUIET_MIN ? reading : null;
  const effort = estimateEffort({ elapsed, log, git, durations, machine: load });
  return { progress, effort, agent, elapsed, git, load };
}

function timeLeftText(progress) {
  if (progress.pct === 100) return "wrapping up";
  if (progress.remaining !== null) return `~${formatMinutes(progress.remaining)} left · done around ${clockIn(progress.remaining)}`;
  if (progress.basis === "long") return "running longer than almost all your past sessions";
  return "no finished sessions yet to estimate from";
}

function basisText(progress, agent) {
  if (progress.basis === "reported") {
    const step = progress.step ? `: “${esc(progress.step)}”` : "";
    return `Reported by the agent${step}`;
  }
  if (progress.basis === "history") {
    return `Estimated from ${progress.sample} past ${agent ? `${esc(agent)} ` : ""}sessions that ran this long`;
  }
  return "Agents can report exact progress by logging <code>[40%] step</code> or <code>step 3/7</code>";
}

// The bar, the time left and the effort badge for one live session. Re-rendered every poll tick.
function progressPanel(live, newest) {
  const { progress, effort, agent, git, load } = liveEstimate(live, newest);
  const known = progress.pct !== null;
  const lifted = effort.laptop && effort.level !== effort.work;
  const badgeTitle = lifted
    ? `The ${machineWord(load).toLowerCase()} is under ${effort.laptop} load right now. The work alone reads ${effort.work}.`
    : EFFORT_MEANING[effort.level];
  const pctLabel = known ? `${progress.pct}%` : "—";
  const valueAttrs = known ? ` aria-valuenow="${progress.pct}" aria-valuetext="${progress.pct}%, ${esc(timeLeftText(progress))}"` : ` aria-valuetext="unknown"`;
  const open = state.vibeEffortOpen?.[live.path] ? " open" : "";
  const factors = effort.factors
    .map((f) => `<li><span class="effort-points" aria-label="${f.points} of 2">${"●".repeat(f.points)}${"○".repeat(2 - f.points)}</span>${esc(f.text)}</li>`)
    .join("");
  const longer = effort.longerThan !== null ? `<p class="muted small">Already longer than ${effort.longerThan}% of your finished sessions.</p>` : "";
  const gitNote = git
    ? ""
    : `<p class="muted small">${newest ? "No git figures here, so code size is not counted yet." : "Code changes count only for the newest live session."}</p>`;
  return `<div class="vibe-progress${known ? "" : " unknown"}${progress.basis === "reported" ? " reported" : ""}" data-path="${esc(live.path)}" data-newest="${newest ? "1" : ""}">
    <div class="vibe-progress-head">
      <strong class="vibe-pct">${pctLabel}</strong>
      <span class="vibe-left">${esc(timeLeftText(progress))}</span>
      <span class="effort effort-${effort.level}" title="${esc(badgeTitle)}">${EFFORT_LABEL[effort.level]} effort</span>
    </div>
    <div class="vibe-bar" role="progressbar" aria-label="Progress of ${esc(live.title)}" aria-valuemin="0" aria-valuemax="100"${valueAttrs}>
      <span style="width:${known ? progress.pct : 100}%"></span>
    </div>
    <p class="vibe-basis muted small">${basisText(progress, agent)}</p>
    <details class="vibe-effort" data-effort="${esc(live.path)}"${open}>
      <summary>Why ${EFFORT_LABEL[effort.level].toLowerCase()}?</summary>
      <p class="small">${load ? `Work so far: ${effort.work}. ` : ""}${EFFORT_MEANING[effort.work]} Score ${effort.score} (medium from 2, heavy from 5).</p>
      <ul class="effort-factors">${factors}</ul>
      ${longer}${gitNote}${machineWhy(load)}
    </details>
  </div>`;
}

// ============================================================ the computer under the sessions

const machineWord = (reading) => (reading?.power ? "Laptop" : "Computer");
const loadFigures = (reading) =>
  [reading.cpu_pct !== null && reading.cpu_pct !== undefined ? `CPU ${reading.cpu_pct}% busy` : "", reading.ram_pct !== null && reading.ram_pct !== undefined ? `memory ${reading.ram_pct}% full` : ""].filter(Boolean);

// "as of 14:02", with the day when the reading is from another one.
function machineWhen(reading) {
  const day = String(reading.at).slice(0, 10);
  const time = String(reading.at).slice(11, 16);
  return day === today() ? time : `${dayLabel(day)} ${time}`;
}

// The laptop's half of "Why heavy?": what was measured, what is using it, and that it is not this session's alone.
function machineWhy(reading) {
  if (!reading) return "";
  const why = (reading.reasons?.length ? reading.reasons : loadFigures(reading)).join(" · ");
  const top = reading.top?.length ? `<p class="muted small">Using the most: ${esc(reading.top.slice(0, 3).join(" · "))}</p>` : "";
  return `<p class="small effort-machine"><strong>${machineWord(reading)}: ${reading.level}.</strong> ${esc(why)}${reading.live ? "" : ` <span class="muted">as of ${esc(machineWhen(reading))}</span>`}</p>
      ${top}
      <p class="muted small">That is the whole ${machineWord(reading).toLowerCase()}, everything running on it, not only this session. The badge shows the higher of the two.</p>`;
}

function machineMeter(label, pct, note = "") {
  if (pct === null || pct === undefined) return "";
  return `<div class="vm-meter">
      <span class="vm-label">${label}</span>
      <span class="vibe-bar vm-bar" role="meter" aria-label="${label}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><span style="width:${Math.min(100, Math.max(0, pct))}%"></span></span>
      <span class="vm-num">${pct}%${note ? ` <span class="muted">${esc(note)}</span>` : ""}</span>
    </div>`;
}

// Which computer this is and how loaded it is, above everything that runs on it. Re-rendered every poll tick.
function machineStrip() {
  const reading = machine();
  if (!reading) return "";
  const spec = [reading.cpu, reading.cores ? `${reading.cores} cores` : "", reading.ram_gb ? `${reading.ram_gb} GB memory` : ""].filter(Boolean).join(" · ");
  const free = reading.ram_free_gb !== null && reading.ram_free_gb !== undefined ? `${reading.ram_free_gb} GB free` : "";
  const when = reading.live
    ? "measured now"
    : `as of ${machineWhen(reading)}${reading.fresh ? "" : ", may have changed since"}`;
  const foot = [reading.power, reading.uptime, reading.top?.length ? `Using the most: ${reading.top.slice(0, 4).join(" · ")}` : ""].filter(Boolean);
  return `<section class="vibe-machine${reading.fresh ? "" : " stale"}" aria-label="${reading.live || !reading.name ? `This ${machineWord(reading).toLowerCase()}` : esc(reading.name)}">
    <header>
      <h2>${esc(reading.name || `This ${machineWord(reading).toLowerCase()}`)}</h2>
      ${spec ? `<span class="vm-spec">${esc(spec)}</span>` : ""}
      <span class="effort effort-${reading.level}" title="${esc((reading.reasons || []).join(" · ") || "CPU and memory have room to spare")}">${EFFORT_LABEL[reading.level]} load</span>
    </header>
    <div class="vm-meters">
      ${machineMeter("CPU", reading.cpu_pct)}
      ${machineMeter("Memory", reading.ram_pct, free)}
    </div>
    <p class="vm-foot muted small">${esc(foot.join(" · "))}${foot.length ? " · " : ""}<span class="vm-when">${esc(when)}</span></p>
  </section>`;
}

// ------------------------------------------------------------ GitHub Actions minutes

const GITHUB_LABEL = { ok: "Within the free minutes", warn: "Getting close", critical: "Almost at the limit", over: "Over the limit" };
const GITHUB_EFFORT = { ok: "light", warn: "medium", critical: "heavy", over: "heavy" };
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const monthName = (month) => `${MONTHS[Number(month.slice(5, 7)) - 1] || month} ${month.slice(0, 4)}`;
const thousands = (value) => Number(value).toLocaleString("en-US");

function githubBar(reading, label) {
  return `<span class="vibe-bar vm-bar gh-bar gh-${reading.level}" role="meter" aria-label="${esc(label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.min(100, reading.pct)}"><span style="width:${Math.min(100, Math.max(0, reading.pct))}%"></span></span>`;
}

// What the badge is warning about, in one sentence.
function githubWhy(reading) {
  if (reading.level === "over") return "Workflows in private repos stop running until the minutes start again.";
  if (reading.level === "critical") return "GitHub mails at 90% and stops private workflows at 100%.";
  if (reading.level === "warn" && reading.pct < 75) return "On course to pass the limit before the month ends.";
  if (reading.level === "warn") return "GitHub mails at 75%, 90% and 100%.";
  return "Private workflows have room to run.";
}

// The days of the running month as bars, then one row per earlier month.
function githubHistory(current, earlier) {
  const peak = Math.max(1, ...current.days);
  const days = current.days.length
    ? `<div class="gh-days" role="img" aria-label="Minutes used on each day of ${esc(monthName(current.month))}">
        ${current.days.map((minutes, index) => `<span title="${esc(`${current.month}-${String(index + 1).padStart(2, "0")}: ${thousands(minutes)} min`)}"><i style="height:${Math.round((minutes * 100) / peak)}%"></i></span>`).join("")}
      </div>
      <p class="muted small gh-days-foot">Each bar is a day of ${esc(monthName(current.month))}, 1 to ${current.days.length}${current.anchor ? ", as the billing rows itemise it" : ""}. The busiest took ${thousands(peak)} minutes.</p>`
    : "";
  const months = earlier
    .filter((reading) => reading.used || reading.public)
    .map(
      (reading) => `<div class="vm-meter gh-month">
        <span class="vm-label">${esc(monthName(reading.month))}</span>
        ${githubBar(reading, monthName(reading.month))}
        <span class="vm-num">${thousands(reading.used)} of ${thousands(reading.limit)} <span class="muted">${reading.pct}%${reading.final ? "" : ", not final"}</span></span>
      </div>`,
    )
    .join("");
  if (!days && !months) return "";
  return `<details class="gh-history">
      <summary>Usage history</summary>
      ${days}
      ${months || `<p class="muted small">No earlier month with minutes used yet.</p>`}
    </details>`;
}

// "14:51" for a stamp from today, else the day as well.
function stampLabel(at) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(at)) return "";
  return at.slice(0, 10) === today() ? at.slice(11, 16) : `${dayLabel(at.slice(0, 10))} ${at.slice(11, 16)}`;
}

// What is left and how thin to spread it, as three figures under the meter.
function githubStats(current, budget) {
  if (budget.days === null) return "";
  const stats = [
    ["Left", `${thousands(budget.left)} min`, budget.left ? "" : "private workflows are stopped"],
    ["Starts again", budget.days === 1 ? "tomorrow" : `in ${budget.days} days`, current.resets],
    ["Stays under at", `${thousands(budget.perDay)} min a day`, budget.runsOut ? `at the pace now, gone by ${dayLabel(budget.runsOut)}` : ""],
  ];
  return `<dl class="gh-stats">${stats
    .map(([label, value, hint]) => `<div><dt>${label}</dt><dd>${esc(value)}${hint ? ` <span class="muted">${esc(hint)}</span>` : ""}</dd></div>`)
    .join("")}</dl>`;
}

const sameAccount = (a, b) => String(a || "").toLowerCase() === String(b || "").toLowerCase();

// Minutes count for the account that owns a repo, whoever pushes, so the way to take load off a full account is
// to hand a repo to another of yours. Offered only where Cairn runs on this computer (the move goes through the
// GitHub CLI's sign-ins there) and only towards an account whose meter shows more room this month.
function githubMoveRow(current, accounts) {
  if (!state.info?.canMoveRepos || readOnly() || current.final || !current.account) return "";
  const repos = current.repos.filter((repo) => /^[A-Za-z0-9._-]+$/.test(repo.name)).slice(0, 4);
  const left = (reading) => Math.max(0, reading.limit - reading.used);
  const target = accounts
    .map((entry) => entry.months[0])
    .filter((reading) => reading.account && reading.month === current.month && !sameAccount(reading.account, current.account))
    .sort((a, b) => left(b) - left(a))[0];
  if (!target || !repos.length || left(target) <= left(current)) return "";
  const asked = githubMoves(state.notes).filter((move) => sameAccount(move.from, current.account) && movePending(move));
  const items = repos.map((repo) => {
    const waiting = asked.find((move) => move.repo === repo.name);
    if (waiting) {
      return `<span class="gh-moving" title="${esc(`Asked ${stampLabel(waiting.at) || waiting.at}. GitHub keeps the link for a day.`)}">${esc(repo.name)} → ${esc(waiting.to)}: waiting for ${esc(waiting.to)} to open GitHub's mail</span>`;
    }
    return `<button type="button" class="btn sm" data-action="vibe-gh-move" data-repo="${esc(repo.name)}" data-from="${esc(current.account)}" data-to="${esc(target.account)}" title="${esc(`Hand the private repo ${repo.name} (${thousands(repo.minutes)} min this month) to ${target.account}`)}">${esc(repo.name)} → ${esc(target.account)}</button>`;
  });
  return `<div class="gh-move">
      <span class="muted small">Minutes count for the account that owns a repo, whoever pushes. Move one to ${esc(target.account)} (${thousands(left(target))} min left):</span>
      ${items.join("")}
    </div>`;
}

// The accounts the GitHub CLI is signed in to on this computer, when there is a choice between them. Null on a
// phone, in a browser without the local app, and with one account.
function pushChoice() {
  const push = state.vibePush;
  return state.info?.canMoveRepos && push && Array.isArray(push.accounts) && push.accounts.length > 1 ? push : null;
}

// Which of your GitHub accounts this computer pushes as, with a button for each. git, the GitHub CLI and an
// assistant working here all follow it, so the choice is made in one place.
function githubPushSection() {
  const push = pushChoice();
  if (!push) return "";
  const follows = push.viaCli && push.sure;
  const chips = push.accounts
    .map((name) => {
      const on = sameAccount(name, push.pusher);
      const title = on ? `Pushes go out as ${name}` : `Push from this computer as ${name}`;
      return `<button type="button" class="chip${on ? " on" : ""}" data-action="vibe-gh-push" data-account="${esc(name)}" aria-pressed="${on}" title="${esc(title)}">${esc(name)}</button>`;
    })
    .join("");
  const how = follows
    ? "git push, the GitHub CLI and an assistant working on this computer follow this choice. Commits keep their author."
    : push.pusher
      ? `git signs in as ${push.pusher} with a sign-in it saved by itself. Pick an account and git asks the GitHub CLI instead, so every tool follows the one choice.`
      : "git has no sign-in for GitHub here yet. Pick an account and git asks the GitHub CLI for it.";
  return `<section class="vibe-ghpush" aria-label="GitHub account this computer pushes as">
    <header>
      <h2>Pushes from this computer</h2>
      <span class="vm-spec">${push.pusher ? `go to GitHub as ${esc(push.pusher)}` : "no account picked yet"}</span>
    </header>
    <div class="gh-push" role="group" aria-label="Push as">${chips}</div>
    <p class="muted small gh-note">${esc(how)}</p>
  </section>`;
}

// How much of the month's free GitHub Actions minutes are gone, from the notes the watcher leaves in github-usage/:
// one meter for the main account and one for every other account that has notes of its own. Under them, where
// this computer is signed in to several accounts, the one it pushes as.
function githubStrip() {
  const accounts = githubAccounts(state.notes);
  return accounts.map((entry) => githubSection(entry, accounts)).join("") + githubPushSection();
}

function githubSection({ months, extra }, accounts) {
  const [current, ...earlier] = months;
  const budget = githubBudget(current);
  const command = `cairn vibe github <minutes>${extra && current.account ? ` --account ${current.account}` : ""}`;
  const anchor = current.anchor;
  const repos = current.repos.slice(0, 4).map((repo) => `${repo.name} ${thousands(repo.minutes)}`).join(" · ");
  let foot;
  if (anchor) {
    const scale = current.scale !== 1 ? `, billing rows ×${current.scale.toFixed(2)} to match it` : "";
    foot = [
      `GitHub's own count: ${thousands(anchor.used)} at ${stampLabel(anchor.at) || anchor.at}, plus ${thousands(current.since)} since${scale}`,
      !current.final && current.pace !== null ? `about ${thousands(current.projected)} by the end of the month at ${thousands(Math.round(current.pace))} a day since that count (${current.projectedPct}%)` : "",
      repos ? `Billing rows itemise ${thousands(current.apiUsed)}: ${repos}` : "",
    ];
  } else {
    foot = [
      !current.final ? `about ${thousands(current.projected)} by the end of the month at the last 7 days' pace (${current.projectedPct}%)` : "",
      !current.final && current.resets && budget.days === null ? `Starts again from zero on ${current.resets}` : "",
      repos ? `Most: ${repos}` : "",
    ];
  }
  foot = [...foot, current.public ? `public repos ${thousands(current.public)} min, free` : ""].filter(Boolean);
  const read = stampLabel(current.at);
  const when = read ? `${anchor ? "checked" : "as of"} ${read}` : "";
  const note = anchor
    ? `Counts on from the number GitHub gave. When GitHub shows or mails a new one, run: ${command}`
    : `Summed from GitHub's billing rows, which can read lower than GitHub's own meter. To count on from the number GitHub shows or mails, run: ${command}`;
  return `<section class="vibe-github" aria-label="${esc(`GitHub Actions minutes${current.account ? ` of ${current.account}` : ""}`)}">
    <header>
      <h2>GitHub Actions${current.account ? ` · ${esc(current.account)}` : ""}</h2>
      <span class="vm-spec">${thousands(current.used)} of ${thousands(current.limit)} free minutes · private repos · ${esc(monthName(current.month))}</span>
      <span class="effort effort-${GITHUB_EFFORT[current.level]}" title="${esc(githubWhy(current))}">${GITHUB_LABEL[current.level]}</span>
    </header>
    <div class="vm-meter gh-meter">
      <span class="vm-label">Used</span>
      ${githubBar(current, "Free minutes used")}
      <span class="vm-num">${current.pct}%</span>
    </div>
    ${githubStats(current, budget)}
    <p class="vm-foot muted small">${esc(githubWhy(current))} ${esc(foot.join(" · "))}${foot.length && when ? " · " : ""}<span class="vm-when">${esc(when)}</span></p>
    ${githubMoveRow(current, accounts)}
    ${githubHistory(current, earlier)}
    <p class="muted small gh-note">${esc(note)}</p>
  </section>`;
}

// One line under a lane in the overview: a thin bar, the percent, the time left and the effort.
function laneProgress(live) {
  const newest = liveSessions()[0]?.path === live.path;
  const { progress, effort } = liveEstimate(live, newest);
  const known = progress.pct !== null;
  const left = progress.remaining !== null && progress.pct !== 100 ? `~${formatMinutes(progress.remaining)} left` : progress.basis === "long" ? "longer than usual" : "";
  return `<div class="ov-progress${known ? "" : " unknown"}" data-path="${esc(live.path)}">
    <span class="vibe-bar ov-bar" role="progressbar" aria-label="Progress" aria-valuemin="0" aria-valuemax="100"${known ? ` aria-valuenow="${progress.pct}"` : ""}><span style="width:${known ? progress.pct : 100}%"></span></span>
    <span class="ov-pct">${known ? `${progress.pct}%` : "—"}</span>
    ${left ? `<span class="ov-left">${esc(left)}</span>` : ""}
    <span class="effort effort-${effort.level}">${EFFORT_LABEL[effort.level]}</span>
  </div>`;
}

// Live tick: refresh every bar in place, keeping an opened "why" block open.
function refreshProgress() {
  const strip = $(".vibe-machine");
  if (strip) strip.outerHTML = machineStrip();
  for (const panel of $$(".vibe-progress")) {
    const note = noteByPath(panel.dataset.path);
    if (note) panel.outerHTML = progressPanel(note, panel.dataset.newest === "1");
  }
  for (const row of $$(".ov-progress")) {
    const note = noteByPath(row.dataset.path);
    if (note) row.outerHTML = laneProgress(note);
  }
}

// Remember an opened "why" block across live re-renders (recorded on click, see the vibe-fold note in wireVibe).
let effortClicksWired = false;
function wireEffortBlocks() {
  if (effortClicksWired) return;
  effortClicksWired = true;
  document.addEventListener("click", (event) => {
    const summary = event.target.closest?.(".vibe-effort > summary");
    if (!summary) return;
    const block = summary.parentElement;
    state.vibeEffortOpen = { ...(state.vibeEffortOpen || {}), [block.dataset.effort]: !block.open };
  });
}

// ============================================================ live updates (local app)

function pollMs() {
  return window.CAIRN_TEST_POLL_MS || state.store?.vibePollMs || POLL_MS;
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

async function poll() {
  if (state.view !== "vibe" || !canPoll()) {
    stopPolling();
    return;
  }
  const live = liveSession();
  let data;
  try {
    data = await state.store.vibe(live ? String(live.meta.started || "") : "", live ? String(live.meta.base || "") : "");
  } catch {
    return; // the local server is restarting; try again on the next tick
  }
  let changed = false;
  for (const file of data.files) {
    const known = fileByPath(file.path);
    if (!known || known.mtime !== file.mtime) {
      upsertFile(file.path, file.text, file.mtime);
      changed = true;
    }
  }
  const gitKey = JSON.stringify(data.git);
  if (gitKey !== state.vibeGitKey) {
    state.vibeGit = data.git;
    state.vibeGitKey = gitKey;
    changed = true;
  }
  // An app that just gained (or lost) the reading needs the strip drawn; after that it updates in place.
  if (Boolean(data.machine) !== Boolean(state.vibeMachine)) changed = true;
  state.vibeMachine = data.machine || null;
  // Which GitHub account this computer pushes as: a switch made elsewhere (a terminal, an assistant) shows here too.
  const pushKey = JSON.stringify(data.push || null);
  if (pushKey !== (state.vibePushKey || "null")) {
    state.vibePush = data.push || null;
    state.vibePushKey = pushKey;
    changed = true;
  }
  // Which computer this is, by the name its sessions carry: the device bar marks it.
  if ((data.device || "") !== (state.vibeHere || "") && severalDevices) changed = true;
  state.vibeHere = data.device || "";
  if (changed && state.view === "vibe") {
    renderNav();
    renderView();
  } else {
    for (const clockEl of $$(".vibe-elapsed")) {
      const note = noteByPath(clockEl.dataset.path);
      if (note) clockEl.textContent = duration(elapsedMinutes(note));
    }
    refreshProgress();
  }
}

export function wireVibe() {
  wireEffortBlocks();
  if (canPoll() && !pollTimer) {
    pollTimer = setInterval(poll, pollMs());
    poll();
  }
  for (const logForm of $$(".vibe-log-form")) {
    logForm.addEventListener("submit", (event) => {
      event.preventDefault();
      logStep(logForm.elements.kind.value, logForm.elements.text.value, logForm.dataset.session).catch((error) => toast(error.message, { error: true }));
    });
  }
  const bar = $(".topbar");
  if (bar) document.documentElement.style.setProperty("--topbar-h", `${bar.offsetHeight}px`);
  wireOverview();
  $(".vibe-activity svg")?.addEventListener("keydown", (event) => {
    const day = event.target.closest?.("[data-action=vibe-jump]");
    if (!day || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    jumpToDay(day.dataset.day);
  });
  $(".vibe-figures")?.addEventListener("keydown", (event) => {
    const tile = event.target.closest?.("[data-action=vibe-why]");
    if (!tile || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    toggleWhy(tile.dataset.why);
  });
  // Empty side blocks fold on phones; remember when someone opens one so live updates keep it open.
  // Recorded on click (which also fires for Enter/Space on the summary), not on "toggle": toggle fires a task
  // later, and a live re-render in between would close the block again.
  for (const fold of $$("details.vibe-fold, details.rc-fold, details.ov-fold")) {
    fold.querySelector("summary")?.addEventListener("click", () => setFold(fold.dataset.block, !fold.open));
  }
  // The last message is clamped to three lines; a tap opens it in full and live updates keep it open.
  for (const last of $$(".rc-last[data-more]")) {
    last.addEventListener("click", () => {
      if (String(getSelection?.() || "")) return;
      setFold(`rc-last:${last.dataset.more}`, last.classList.toggle("open"));
    });
  }
  const feedbackForm = $("#fb-form");
  if (feedbackForm) {
    // Keep what's typed if a live update re-renders the view mid-sentence.
    feedbackForm.addEventListener("input", () => {
      const f = feedbackForm.elements;
      state.vibeDraft = { text: f.text.value, rating: f.rating.value, from: f.from.value, about: f.about.value };
    });
    feedbackForm.addEventListener("submit", (event) => {
      event.preventDefault();
      addFeedback(feedbackForm).catch((error) => toast(error.message, { error: true }));
    });
  }
  for (const select of $$("[data-fb-status]")) {
    select.addEventListener("change", async () => {
      await saveNote(select.dataset.fbStatus, { status: select.value });
      renderNav();
      renderView();
    });
  }
}

// ============================================================ view

function logRow(entry) {
  const mark = entry.kind === "pass" ? icon("check", "sm") : entry.kind === "fail" ? icon("x", "sm") : entry.kind === "skill" ? icon("terminal", "sm") : "";
  return `<li class="log-row k-${entry.kind}"><span class="t">${esc(entry.time)}</span><span class="kind">${mark}${entry.kind}</span><span class="txt">${esc(entry.text)}</span></li>`;
}

function gitPanel(git) {
  if (!canPoll()) {
    return `<p class="muted small">Steps your assistant logs update live when Cairn runs on this computer (<code>python -m cairn</code> or the desktop app), from a folder you opened in Chrome or Edge, or from GitHub when you're signed in.</p>`;
  }
  if (!git) return `<p class="muted small">Connecting…</p>`;
  if (git.browser) return `<p class="muted small">${icon("refresh", "sm")} Following the folder: new steps appear within seconds. Commits and changed files need Cairn running on this computer.</p>`;
  if (git.github && git.signedOut) return `<p class="muted small">Sign in to follow this brain live: Cairn checks GitHub every 20 seconds, and unchanged checks don't count against your limit.</p>`;
  if (git.github) return `<p class="muted small">${icon("refresh", "sm")} Following GitHub: steps appear about 20 seconds after they're pushed.</p>`;
  if (!git.available) return `<p class="muted small">Not a git repository, so there are no commits to show.</p>`;
  const added = git.commits.reduce((t, c) => t + c.added, git.added);
  const removed = git.commits.reduce((t, c) => t + c.removed, git.removed);
  const commits = git.commits.length
    ? `<ul class="commit-list">${git.commits
        .map((c) => `<li><code>${esc(c.hash)}</code><span class="txt">${esc(c.subject)}</span><span class="churn"><b class="plus">+${c.added}</b> <b class="minus">−${c.removed}</b></span></li>`)
        .join("")}</ul>`
    : `<p class="muted small">No commits yet this session.</p>`;
  return `
    <div class="git-figures">
      <div><span class="value">${git.commits.length}</span><span class="label">${git.commits.length === 1 ? "commit" : "commits"}</span></div>
      <div><span class="value">${git.changedCount ?? git.changed.length}</span><span class="label">files changed now</span></div>
      <div><span class="value"><b class="plus">+${added}</b> <b class="minus">−${removed}</b></span><span class="label">lines this session</span></div>
    </div>
    <p class="muted small">${icon("branch", "sm")} ${esc(git.branch)}</p>
    ${commits}`;
}

// The most recently finished session, by when it ended (falls back to when it started).
function lastSession() {
  const key = (note) => String(note.meta.ended || note.meta.started || note.meta.created || "");
  return vibeNotes()
    .filter((n) => n.type === "session" && n.status !== "live")
    .sort((a, b) => (key(a) < key(b) ? 1 : -1))[0] || null;
}

// Nothing running: one line saying so, with the last session to pick up from.
function idleNote() {
  const sample = !readOnly() && state.info?.canLoadSample && !state.notes.some((n) => n.type === "session")
    ? ` <button class="btn sm" data-action="vibe-sample">${icon("play", "sm")} Load a sample week</button>`
    : "";
  const last = lastSession();
  if (last) {
    const ended = String(last.meta.ended || last.meta.started || "");
    const when = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(ended) ? `${dayLabel(ended.slice(0, 10))} ${ended.slice(11, 16)}` : "";
    return `<section class="vibe-live idle compact">
      <p>${icon("activity")} <strong>Nothing live.</strong> Last session: <a href="#" data-open="${esc(last.path)}">${esc(last.title)}</a><span class="idle-meta muted">${[when ? `ended ${esc(when)}` : "", readOnly() ? "" : `next: Start session above, or <code>cairn vibe start</code>`].filter(Boolean).join(" · ")}</span></p>
    </section>`;
  }
  return `<section class="vibe-live idle">
    <p>${icon("activity")} <strong>Nothing live.</strong> ${readOnly() ? "" : `Start a session here or from your terminal with <code>cairn vibe start "add dark mode"</code>. <code>cairn vibe watch</code> picks up OpenCode and Vector sessions on its own.`}${sample}</p>
  </section>`;
}

// A live session opened inside its recap row: progress, its steps, the form to log one, and the buttons that end it.
function liveDetail(live, newest = true, total = 1) {
  const log = core.sessionLog(live.body);
  const checks = log.filter((e) => e.kind === "pass" || e.kind === "fail");
  const lastCheck = checks.at(-1);
  const agent = typeof live.meta.agent === "string" && live.meta.agent ? `with <strong>${esc(live.meta.agent)}</strong>` : "";
  const task = noteLink(live.meta.task);
  const controls = readOnly()
    ? ""
    : `<div class="vibe-end" role="group" aria-label="End the session">
        <span class="muted small">End as</span>
        ${core.FINISHED.map((r) => `<button class="btn sm${r === "shipped" ? " primary" : ""}" data-action="vibe-end" data-result="${r}" data-path="${esc(live.path)}">${RESULT_LABEL[r]}</button>`).join("")}
      </div>`;
  const logForm = readOnly()
    ? ""
    : `<form class="vibe-log-form" data-session="${esc(live.path)}" autocomplete="off">
        <select class="select" name="kind" aria-label="Kind of step">
          ${["note", "prompt", "skill", "pass", "fail", "commit"].map((k) => `<option value="${k}">${k}</option>`).join("")}
        </select>
        <input class="input" name="text" aria-label="Step" placeholder="Log a step or a /skill">
        <button class="btn" type="submit">Log</button>
      </form>`;
  const skills = [...new Set(log.filter((e) => e.kind === "skill").map((e) => core.skillName(e.text)).filter(Boolean))];
  const code = newest
    ? `<div><h3>Code${total > 1 ? " <span class=\"muted small\">(whole repo, since this session started)</span>" : ""}</h3>${gitPanel(state.vibeGit)}</div>`
    : "";
  const facts = [agent, task ? `for ${task}` : "", skills.length ? `skills ${skills.map((s) => `<code>${esc(s)}</code>`).join(" ")}` : "", total > 1 ? `<code>CAIRN_SESSION=${esc(live.slug)}</code>` : ""].filter(Boolean);
  return `<section class="vibe-live${newest ? "" : " also"}" aria-label="Live session: ${esc(live.title)}">
    <header>
      <div>
        <p class="eyebrow"><span class="live-dot" aria-hidden="true"></span>Live · <span class="vibe-elapsed" data-path="${esc(live.path)}">${duration(elapsedMinutes(live))}</span>${lastCheck ? ` · last check <span class="check-${lastCheck.kind}">${lastCheck.kind === "pass" ? "passed" : "failed"}</span>` : ""}</p>
        ${facts.length ? `<p class="muted small">${facts.join(" · ")}</p>` : ""}
      </div>
      ${controls}
    </header>
    ${progressPanel(live, newest)}
    <div class="${newest ? "vibe-live-grid" : ""}">
      <div>
        <h3>Progress</h3>
        <ol class="vibe-log" reversed>${log.slice(newest ? -14 : -6).reverse().map(logRow).join("") || `<li class="muted small">No steps yet.</li>`}</ol>
        ${logForm}
      </div>
      ${code}
    </div>
  </section>`;
}

// A session that ended, opened inside its recap row: its last steps.
const ENDED_STEPS = 8;
function endedDetail(note) {
  const log = core.sessionLog(note.body);
  return `<div class="rc-detail">
    <ol class="vibe-log" reversed>${log.slice(-ENDED_STEPS).reverse().map(logRow).join("") || `<li class="muted small">No steps were logged.</li>`}</ol>
    ${log.length > ENDED_STEPS ? `<p class="muted small">Newest ${ENDED_STEPS} of ${log.length} steps. <a href="#" data-open="${esc(note.path)}">Open the session</a> for all of them.</p>` : ""}
  </div>`;
}

// ============================================================ overview band
// Every session by status (a proportion bar with its tiles as the legend), and one lane per session running now.

const two = (n) => String(n).padStart(2, "0");

// A status Cairn does not know (a hand-edited file) is still counted, so the tiles add up to the session total.
const bucketOf = (note) => (outcomeOf(note) in OUTCOMES ? outcomeOf(note) : "other");
const bucketLabel = (key) => OUTCOMES[key] || "other";

// Whole percentages that add up to exactly 100 (largest remainder), so the tiles never total 99% or 101%.
function shares(values) {
  const total = values.reduce((sum, n) => sum + n, 0);
  if (!total) return values.map(() => 0);
  const whole = values.map((n) => Math.floor((n * 100) / total));
  let left = 100 - whole.reduce((sum, n) => sum + n, 0);
  const order = values.map((n, index) => [(n * 100) % total, index]).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (const [, index] of order) {
    if (left <= 0) break;
    whole[index] += 1;
    left -= 1;
  }
  return whole;
}

// Start times are kept to the minute, so the seconds count from the top of that minute.
function liveSeconds(started, now = new Date()) {
  const start = core.stampMinutes(started);
  const current = core.stampMinutes(`${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}T${two(now.getHours())}:${two(now.getMinutes())}`);
  return start === null || current === null ? 0 : Math.max(0, (current - start) * 60 + now.getSeconds());
}

function clockText(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours ? `${hours}:${two(minutes)}:${two(seconds % 60)}` : `${minutes}:${two(seconds % 60)}`;
}

function peekRow(note) {
  const started = startedKey(note);
  const when = [dayLabel(sessionDayOf(note)), /T\d{2}:\d{2}/.test(started) ? started.slice(11, 16) : ""].join(" ").trim();
  const minutes = note.status === "live" ? elapsedMinutes(note) : core.sessionMinutes(note);
  return `<li><a href="#" data-open="${esc(note.path)}">${esc(note.title)}</a><span class="ov-meta">${esc(when)} · ${duration(minutes)}</span></li>`;
}

// Whole days since a live session started; 0 inside its first day. Steps carry only a clock time, so the start is all there is to go on.
function staleDays(note) {
  return Math.floor(liveSeconds(note.meta.started) / 86400);
}

function lane(note) {
  const log = core.sessionLog(note.body);
  const passed = log.filter((e) => e.kind === "pass").length;
  const failed = log.filter((e) => e.kind === "fail").length;
  const last = log.at(-1);
  const who = folderName(note);
  const days = staleDays(note);
  const checks = passed || failed
    ? `<span class="ov-checks"><span class="check-pass" title="${passed} checks passed">${icon("check", "sm")}${passed}</span> <span class="check-fail" title="${failed} checks failed">${icon("x", "sm")}${failed}</span></span>`
    : "";
  const step = last
    ? `<span class="ov-step k-${last.kind}"><span class="t">${esc(last.time)}</span><span class="kind">${last.kind}</span><span class="txt">${esc(last.text)}</span></span>`
    : `<span class="ov-step muted">No steps yet</span>`;
  return `<li class="ov-lane${days ? " stale" : ""}" data-path="${esc(note.path)}" data-steps="${log.length}">
      <div class="ov-lane-head">
        <i class="ov-pulse" aria-hidden="true"></i>
        <a class="ov-title" href="#" data-open="${esc(note.path)}">${esc(note.title)}</a>
        ${who ? `<span class="ov-who">${esc(who)}</span>` : ""}
        ${days ? `<span class="ov-stale" title="Still marked live. Open it and end it if the work is over.">started ${plural(days, "day")} ago</span>` : ""}
        ${checks}
        <span class="sr-only">running for </span><span class="ov-clock" data-started="${esc(String(note.meta.started || ""))}" title="Time since it started">${clockText(liveSeconds(note.meta.started))}</span>
      </div>
      ${laneProgress(note)}
      <button type="button" class="ov-lane-log" data-action="vibe-lane" data-path="${esc(note.path)}" title="Jump to this session's log">
        <span class="ov-track" aria-hidden="true">${log.slice(-TRACK).map((e) => `<i class="k-${e.kind}" title="${esc(e.time)} ${e.kind}"></i>`).join("")}</span>
        ${step}
      </button>
    </li>`;
}

// What is running, one fold per AI: a few lanes each so one busy AI cannot push the others off the screen.
function liveLanes(live) {
  if (!live.length) return "";
  const groups = liveGroups(live);
  const cap = groups.length > 1 ? LANES_EACH : LANES;
  const blocks = groups.map((group) => {
    const old = group.notes.filter(staleDays).length;
    const more = group.notes.length - cap;
    return `<details class="ov-agent ov-fold" data-agent="${esc(group.key)}" data-block="ov-agent:${esc(group.key)}"${foldOpen(`ov-agent:${group.key}`, true) ? " open" : ""}>
        <summary><h3><span class="ov-agent-name">${esc(group.label)}</span><span class="ov-agent-count">${group.notes.length} live</span>${old ? `<span class="ov-agent-old" title="Still marked live. Open one and end it if the work is over.">${old} started over a day ago</span>` : ""}</h3></summary>
        <ol class="ov-lanes" aria-label="${esc(group.label)}, running now">${group.notes.slice(0, cap).map(lane).join("")}</ol>
        ${more > 0 ? `<p class="ov-more muted small">+${more} more${groups.length > 1 ? ` in ${esc(group.label)}` : " live"}, all in Live recap</p>` : ""}
      </details>`;
  });
  return `<div class="ov-agents">${blocks.join("")}</div>`;
}

function overview(sessions) {
  if (!sessions.length) return "";
  const filters = vibeFilters(sessions);
  const counts = {};
  for (const note of sessions) counts[bucketOf(note)] = (counts[bucketOf(note)] || 0) + 1;
  const keys = [...Object.keys(OUTCOMES), ...(counts.other ? ["other"] : [])];
  const share = shares(keys.map((key) => counts[key] || 0));
  // A tile is pressed only when the list shows exactly its sessions: that status, from every source.
  const pressed = filters.filter === "all" ? filters.outcome : "all";
  const live = shownLive();
  const segments = keys.map((key, index) => {
    const count = counts[key] || 0;
    const action = count && key !== "other" ? ` data-action="vibe-overview"` : "";
    return `<span class="ov-seg s-${key}${count ? "" : " empty"}${key === pressed ? " on" : ""}"${action} data-outcome="${key}" data-count="${count}" style="flex-grow:${count}" title="${count} ${esc(bucketLabel(key))} · ${share[index]}%"></span>`;
  });
  const tiles = keys.map((key, index) => {
    const count = counts[key] || 0;
    const label = bucketLabel(key);
    const inner = `<span class="ov-name"><i class="ov-mark s-${key}" aria-hidden="true"></i>${esc(label)}</span><span class="ov-num"><b class="ov-count" data-count="${count}">${count}</b><span class="ov-share">${share[index]}%</span></span>`;
    if (key === "other") return `<div class="ov-tile static" data-outcome="other" title="Sessions with a status Cairn does not know">${inner}</div>`;
    return `<button type="button" class="ov-tile" data-action="vibe-overview" data-outcome="${key}" aria-pressed="${key === pressed}" aria-label="${esc(label)}: ${plural(count, "session")}, ${share[index]}%" title="${esc(DOT_LABEL[key] || label)}"${count ? "" : " disabled"}>${inner}</button>`;
  });
  const matching = pressed === "all" ? [] : sessions.filter((note) => outcomeOf(note) === pressed);
  const peek = matching.length
    ? `<div class="ov-peek">
        <ul>${matching.slice(0, PEEK).map(peekRow).join("")}</ul>
        <button type="button" class="btn sm ghost ov-to-list" data-action="vibe-to-list">${icon("chevron", "sm")} ${matching.length > PEEK ? `All ${matching.length} in the list` : "See in the list"}</button>
      </div>`
    : "";
  const lanes = liveLanes(live);
  return `<section class="vibe-overview" aria-label="Overview">
    <header>
      <h2>Overview</h2>
      <span class="muted small">${plural(sessions.length, "session")}${live.length ? ` · <span class="ov-live-now">${live.length} live now</span>` : ""}</span>
      ${pressed === "all" ? "" : `<button type="button" class="btn sm ghost ov-clear" data-action="vibe-overview" data-outcome="${pressed}">Clear filter</button>`}
    </header>
    <div class="ov-bar${pressed === "all" ? "" : " has-on"}" aria-hidden="true">${segments.join("")}</div>
    <div class="ov-tiles${counts.other ? " has-other" : ""}" role="group" aria-label="Sessions by status">${tiles.join("")}</div>
    ${peek}
    ${lanes}
  </section>`;
}

function tickClocks() {
  const clocks = $$(".ov-clock, .rc-clock");
  if (!clocks.length) {
    clearInterval(clockTimer);
    clockTimer = null;
    return;
  }
  for (const el of clocks) el.textContent = clockText(liveSeconds(el.dataset.started));
}

function startClocks() {
  if (!clockTimer && $(".ov-clock, .rc-clock")) clockTimer = setInterval(tickClocks, 1000);
}

// Roll a number to its new value, picking up `into` ms after the roll began.
// The timeout writes the final number even when frames are throttled.
function countTo(el, from, to, into = 0) {
  const began = performance.now() - into;
  const frame = (now) => {
    if (!el.isConnected) return;
    const part = Math.min(1, Math.max(0, (now - began) / 450));
    el.textContent = String(Math.round(from + (to - from) * (1 - (1 - part) ** 3)));
    if (part < 1) requestAnimationFrame(frame);
  };
  frame(performance.now());
  setTimeout(() => {
    if (el.isConnected) el.textContent = String(to);
  }, Math.max(0, 650 - into));
}

function wireOverview() {
  startClocks();
  const band = $(".vibe-overview");
  if (!band) {
    ovSeen = null;
    return;
  }
  // Pointing at a tile or a piece of the bar lights its partner, so the bar and its legend read as one thing.
  const light = (key) => {
    band.classList.toggle("ov-dim", Boolean(key));
    for (const el of $$(".ov-seg, .ov-tile", band)) el.classList.toggle("hot", Boolean(key) && el.dataset.outcome === key);
  };
  const keyAt = (event) => event.target.closest?.(".ov-seg:not(.empty), .ov-tile")?.dataset.outcome || "";
  band.addEventListener("pointerover", (event) => light(keyAt(event)));
  band.addEventListener("pointerleave", () => light(""));
  band.addEventListener("focusin", (event) => light(keyAt(event)));
  band.addEventListener("focusout", () => light(""));

  const counts = {};
  for (const tile of $$(".ov-tile", band)) counts[tile.dataset.outcome] = Number($(".ov-count", tile).dataset.count);
  const steps = new Map($$(".ov-lane", band).map((el) => [el.dataset.path, Number(el.dataset.steps)]));
  const before = ovSeen;
  ovSeen = { counts, steps };
  const still = document.hidden || matchMedia("(prefers-reduced-motion: reduce)").matches;
  const at = performance.now();
  if (!before) ovEnterAt = still ? -Infinity : at;
  if (still) return;
  const into = at - ovEnterAt;
  if (into < ENTER_MS) {
    // Opening the page: the bar draws in, the numbers count up, the lanes fade in. The first poll
    // redraws the page a moment after it opens, so a redraw inside the draw-in carries on with it.
    band.classList.add("ov-enter");
    band.style.setProperty("--ov-into", `${-Math.round(into)}ms`);
    for (const el of $$(".ov-count", band)) countTo(el, 0, Number(el.dataset.count), into);
    return;
  }
  // While the page stays open only what really changed moves.
  for (const tile of $$(".ov-tile", band)) {
    const was = before.counts[tile.dataset.outcome] ?? 0;
    const now = counts[tile.dataset.outcome];
    if (was === now) continue;
    tile.classList.add("ov-bump");
    countTo($(".ov-count", tile), was, now);
  }
  for (const seg of $$(".ov-seg", band)) {
    const was = before.counts[seg.dataset.outcome] ?? 0;
    const now = Number(seg.dataset.count);
    if (was === now) continue;
    seg.style.transition = "none";
    seg.style.flexGrow = String(was);
    void seg.offsetWidth; // start the slide from the old width
    seg.style.transition = "";
    seg.style.flexGrow = String(now);
  }
  for (const el of $$(".ov-lane", band)) {
    const was = before.steps.get(el.dataset.path);
    if (was === undefined) el.classList.add("ov-new");
    else if (Number(el.dataset.steps) > was) el.classList.add("ov-stepped");
  }
}

// ============================================================ live recap
// Above everything else: every session running now with its state (working, between runs, stopped on an
// error), the model and tokens behind it, and how the sessions of the last day ended. Each section folds, and
// each row opens to that session's steps, so the recap is also where you log a step or end a session.

const RECAP_CHIPS = { error: "stopped on an error", working: "working", finished: "turn finished", left: "left open" };

// `at` is on the stampMinutes clock: "14:02" today, "Yesterday 14:02" or "12 Nov 14:02" before that.
function recapWhen(at) {
  if (at === null || at === undefined) return "";
  const iso = new Date(at * 60000).toISOString();
  const day = iso.slice(0, 10);
  const time = iso.slice(11, 16);
  return day === today() ? time : `${dayLabel(day)} ${time}`;
}

function recapActions(entry) {
  if (!chatReady()) return "";
  const note = entry.note;
  const external = String(note.meta.external || "");
  const folder = folderName(note);
  const canContinue = sessionSource(note) === "opencode" && external && !external.startsWith("batch-") && entry.key !== "working";
  const buttons = [
    canContinue
      ? `<button type="button" class="btn sm${entry.key === "finished" || entry.key === "error" ? " primary" : ""}" data-action="vibe-continue" data-path="${esc(note.path)}" title="Send the next prompt to this OpenCode session">${icon("chat", "sm")} Continue</button>`
      : "",
    folder
      ? `<button type="button" class="btn sm ghost" data-action="vibe-newchat" data-path="${esc(note.path)}" title="Start a fresh chat in ${esc(folder)}">${icon("plus", "sm")} New chat</button>`
      : "",
  ].filter(Boolean);
  return buttons.length ? `<div class="rc-actions">${buttons.join("")}</div>` : "";
}

// `alone`: the only live session opens by itself, so its log form is one click away as it always was.
function recapRow(entry, alone = false) {
  const note = entry.note;
  const live = note.status === "live";
  const group = groupOf(note);
  const facts = [
    group.key === "other" ? "" : esc(group.label),
    folderName(note) ? esc(folderName(note)) : "",
    severalDevices && shownDevice() === null && deviceOf(note) ? `<span class="rc-device" title="Computer">${esc(deviceOf(note))}</span>` : "",
    entry.model ? `<span class="rc-model" title="Model">${esc(entry.model)}</span>` : "",
    entry.effort ? `<span class="rc-effort" title="Reasoning effort">${esc(entry.effort)} effort</span>` : "",
    entry.tokens ? `<span class="rc-tokens" title="${entry.tokens.toLocaleString()} tokens in and out">${compact(entry.tokens)} tokens</span>` : "",
    entry.prompts ? plural(entry.prompts, "prompt") : "",
    entry.runs ? `${entry.runs} runs` : "",
  ].filter(Boolean);
  const slow = live && entry.key === "working" && entry.idle >= QUIET_MIN;
  let when;
  if (live && entry.key === "working" && !slow) {
    when = `<span class="sr-only">running for </span><span class="rc-clock" data-started="${esc(String(note.meta.started || ""))}" title="Time since it started">${clockText(liveSeconds(note.meta.started))}</span>`;
  } else if (live) {
    const since = slow ? `last step ${minutesText(entry.idle)} ago · ` : entry.key === "left" ? "" : `for ${minutesText(entry.idle)} · `;
    when = `<span class="rc-when" title="Last activity ${esc(recapWhen(entry.at))}">${since}${duration(elapsedMinutes(note))} in</span>`;
  } else {
    when = `<span class="rc-when">ended ${esc(recapWhen(entry.at))} · ${duration(core.sessionMinutes(note))}</span>`;
  }
  const message = entry.message;
  const last = message
    ? `<p class="rc-last k-${message.kind}${foldOpen(`rc-last:${note.path}`) ? " open" : ""}" data-more="${esc(note.path)}" title="Tap to read it all">${message.time ? `<span class="t">${esc(message.time)}</span>` : ""}<span class="kind">${message.kind === "reply" ? "last reply" : message.kind}</span><span class="txt">${esc(message.text)}</span></p>`
    : "";
  const open = foldOpen(`rc-row:${note.path}`, live && alone);
  const detail = open ? (live ? liveDetail(note, note.path === liveSession()?.path, liveSessions().length) : endedDetail(note)) : "";
  return `<li class="rc-row s-${entry.key}${slow ? " slow" : ""}${open ? " open" : ""}" data-path="${esc(note.path)}" data-state="${entry.key}">
      <div class="rc-head">
        <button type="button" class="rc-toggle" data-action="vibe-row" data-path="${esc(note.path)}" aria-expanded="${open}" aria-label="${open ? "Hide" : "Show"} the steps of ${esc(note.title)}" title="${open ? "Hide" : "Show"} its steps">${icon("chevron", "sm")}</button>
        <span class="rc-state s-${entry.key}">${esc(entry.label)}</span>
        <a class="rc-title" href="#" data-open="${esc(note.path)}">${esc(note.title)}</a>
        ${when}
      </div>
      ${facts.length ? `<p class="rc-facts">${facts.join(`<span aria-hidden="true"> · </span>`)}</p>` : ""}
      ${last}
      ${recapActions(entry)}
      ${detail}
    </li>`;
}

// Rows shown before "N more": stopped sessions sort first, so the ones worth a look stay in view.
const LIVE_SHOWN = 6;
const FINISHED_SHOWN = 4;

function recapFold(key, summary, body, fallback = false, section = false) {
  return `<details class="rc-fold${section ? " rc-section" : ""}" data-block="${key}"${foldOpen(key, fallback) ? " open" : ""}>
      <summary>${summary}</summary>
      ${body}
    </details>`;
}

function recapBand() {
  const data = recap(vibeNotes(), stamp());
  const chips = Object.keys(RECAP_CHIPS)
    .filter((key) => data.counts[key])
    .map((key) => `<span class="rc-chip s-${key}"><b>${data.counts[key]}</b> ${RECAP_CHIPS[key]}</span>`)
    .join("");
  const alone = data.live.length + data.stale.length === 1;
  const rows = (entries) => entries.map((entry) => recapRow(entry, alone)).join("");
  const running = data.live.length
    ? recapFold(
        "rc-running",
        `<span class="rc-sub">Running now</span> <span class="muted small">${plural(data.live.length, "session")}</span>`,
        `<ol class="rc-rows rc-live" aria-label="In progress">${rows(data.live.slice(0, LIVE_SHOWN))}</ol>
        ${data.live.length > LIVE_SHOWN
          ? recapFold("rc-live-more", `${data.live.length - LIVE_SHOWN} more running`, `<ol class="rc-rows rc-live">${rows(data.live.slice(LIVE_SHOWN))}</ol>`)
          : ""}`,
        true,
        true,
      )
    : "";
  const stale = data.stale.length
    ? recapFold(
        "rc-stale",
        `<span class="rc-sub">Left open</span> <span class="muted small">${data.stale.length} with no step for over a day, still marked live</span>`,
        `<ol class="rc-rows">${rows(data.stale)}</ol>`,
        false,
        true,
      )
    : "";
  const finished = data.finished.length
    ? recapFold(
        "rc-finished-all",
        `<span class="rc-sub">Just finished</span> <span class="muted small">last 24 hours${data.finishedTotal > data.finished.length ? ` · newest ${data.finished.length} of ${data.finishedTotal}` : ` · ${data.finished.length}`}</span>`,
        `<ol class="rc-rows rc-finished">${rows(data.finished.slice(0, FINISHED_SHOWN))}</ol>
        ${data.finished.length > FINISHED_SHOWN
          ? recapFold("rc-finished", `${data.finished.length - FINISHED_SHOWN} more finished`, `<ol class="rc-rows rc-finished">${rows(data.finished.slice(FINISHED_SHOWN))}</ol>`)
          : ""}`,
        true,
        true,
      )
    : "";
  return `<section class="vibe-recap" aria-label="Live recap">
    <header>
      <h2>Live recap</h2>
      <span class="rc-chips">${chips || `<span class="muted small">Nothing running right now</span>`}</span>
      ${data.tokens ? `<span class="rc-total muted small" title="${data.tokens.toLocaleString()} tokens in and out across the sessions still live">${compact(data.tokens)} tokens in live sessions</span>` : ""}
    </header>
    ${data.live.length + data.stale.length ? "" : idleNote()}
    ${running}
    ${stale}
    ${finished}
  </section>`;
}

// Models with a token count; one that never returned a token (a failed probe, a mistyped name) is left out
// as soon as any other model has numbers.
function shownModels(stats) {
  return stats.models.some((m) => m.tokens) ? stats.models.filter((m) => m.tokens) : stats.models;
}

// Tokens and time per model, the heaviest first.
function modelsBlock(stats) {
  const models = shownModels(stats);
  if (!models.length) return "";
  const top = Math.max(1, ...models.map((m) => m.tokens));
  const rows = models
    .map(
      (m) => `<li>
        <span class="mu-name">${esc(m.model)}</span>
        <span class="mu-num">${m.tokens ? `${compact(m.tokens)} tokens` : "no token count"} · ${plural(m.sessions, "session")}${m.minutes ? ` · ${duration(m.minutes)}` : ""}</span>
        <span class="mu-bar" aria-hidden="true"><i style="width:${Math.round((m.tokens / top) * 100)}%"></i></span>
      </li>`,
    )
    .join("");
  return sideBlock("vibe-models", "Models", `${compact(stats.tokens)} tokens`, `<ol class="mu-list">${rows}</ol>`, false);
}

function tokensFigure(stats, figure, dash) {
  if (!stats.tokens) return figure(dash, "tokens", "No token counts yet. The OpenCode watcher records them for each session.", "no token counts yet");
  const top = stats.models[0];
  const title = `${compact(stats.tokens_in)} read · ${compact(stats.tokens_out)} written${stats.tokens_cache ? ` · ${compact(stats.tokens_cache)} cached context re-read` : ""}${top ? ` · most on ${top.model}` : ""}`;
  return figure(compact(stats.tokens), "tokens", title, `${plural(stats.prompts, "prompt")}`);
}

function figures(stats) {
  // Title tooltips never show on touch screens, so a tap (or Enter) opens the same text under the figure.
  const figure = (value, label, title = "", sub = "") => {
    if (!title) return `<div class="figure"><span class="value">${value}</span><span class="label">${label}</span>${sub ? `<span class="sub">${sub}</span>` : ""}</div>`;
    const open = state.vibeWhy === label;
    return `<div class="figure explain${open ? " open" : ""}" title="${esc(title)}" role="button" tabindex="0" aria-expanded="${open}" data-action="vibe-why" data-why="${esc(label)}"><span class="value">${value}</span><span class="label">${label}</span>${sub ? `<span class="sub">${sub}</span>` : ""}${open ? `<span class="why">${esc(title)}</span>` : ""}</div>`;
  };
  const dash = "–";
  const parallel = stats.parallel && stats.parallel >= 1.1;
  return `<div class="figures vibe-figures${stats.feedback ? "" : " few"}">
    ${figure(stats.sessions, stats.sessions === 1 ? "session" : "sessions", `${stats.shipped} ${OUTCOMES.shipped} · ${stats.partial} partial · ${stats.dropped} dropped${stats.done ? ` · ${stats.done} with no code change` : ""}`)}
    ${figure(
      duration(stats.minutes),
      "time vibing",
      "Clock time with at least one session working. Sessions that ran side by side count once.",
      parallel ? `<span title="Clock time with at least one session working. Added up session by session it is ${esc(duration(stats.agent_minutes))}.">${duration(stats.agent_minutes)} summed · ${stats.parallel}× parallel</span>` : "",
    )}
    ${shippedFigure(stats, figure, dash)}
    ${stats.pass_rate === null
      ? figure(dash, "checks green", "No test runs logged yet. Log one with: cairn vibe check -- <test command>", "no checks logged yet")
      : figure(`${stats.pass_rate}%`, "checks green", `${stats.passed} passed · ${stats.failed} failed`)}
    ${stats.feedback ? figure(stats.feedback_open, "feedback open", `${stats.feedback} total · ${stats.feedback_addressed} addressed`) : ""}
    ${stats.feedback && stats.rating !== null ? figure(stats.rating.toFixed(1), "avg rating", "Out of 5") : ""}
    ${tokensFigure(stats, figure, dash)}
  </div>`;
}

// Detected sessions only ever end as shipped (commits or line changes seen) or done (none seen), so a
// ship rate over "finished" sessions would always read 100%. Show the count against every ended session.
function shippedFigure(stats, figure, dash) {
  const ended = stats.shipped + stats.partial + stats.dropped + stats.done;
  if (!ended) return figure(dash, OUTCOMES.shipped, "No finished sessions yet.");
  const share = Math.round((stats.shipped / ended) * 100);
  const handRated = stats.partial + stats.dropped;
  const title = handRated
    ? `Ship rate ${stats.ship_rate}% of sessions you ended with a result (shipped, partial or dropped).`
    : "Sessions that committed or changed code in their folder.";
  return figure(stats.shipped, OUTCOMES.shipped, title, stats.shipped ? `${share}% of ${ended}${OUTCOMES.shipped === "shipped" ? " · code changed" : " · commits or edits seen"}` : "no code changes seen yet");
}

function activity(stats, sessions = []) {
  const days = stats.daily;
  // Sessions with no git have no evidence either way, so the chart keeps them apart from "no code change".
  const noGit = {};
  const byDay = {};
  for (const note of sessions) {
    const day = sessionDayOf(note);
    const outcome = outcomeOf(note);
    if (outcome === "unknown") noGit[day] = (noGit[day] || 0) + 1;
    byDay[day] = byDay[day] || {};
    byDay[day][outcome] = (byDay[day][outcome] || 0) + 1;
  }
  const byMinutes = days.some((d) => d.minutes > 0);
  const value = (d) => (byMinutes ? d.minutes : d.sessions);
  const max = Math.max(1, ...days.map(value));
  const width = 100 / days.length;
  const bars = days
    .map((d, i) => {
      const h = Math.round((value(d) / max) * 54);
      const unknown = noGit[d.day] || 0;
      const shippedH = d.sessions ? Math.round(h * (d.shipped / d.sessions)) : 0;
      const unknownH = d.sessions ? Math.round(h * (unknown / d.sessions)) : 0;
      const x = (i * width + width * 0.18).toFixed(2);
      const w = (width * 0.64).toFixed(2);
      const split = Object.keys(OUTCOMES)
        .filter((key) => byDay[d.day] && byDay[d.day][key])
        .map((key) => `${byDay[d.day][key]} ${OUTCOMES[key]}`)
        .join(", ");
      const tip = `${dayLabel(d.day)}: ${plural(d.sessions, "session")}${byMinutes ? ` · ${duration(d.minutes)} clock time` : ""}${split ? ` · ${split}` : ""}`;
      // A day with sessions is a button: it unfolds that day in the list below and scrolls to it.
      const open = d.sessions ? ` class="bar-day" data-action="vibe-jump" data-day="${esc(d.day)}" role="button" tabindex="0" aria-label="${esc(tip)}. Show this day's sessions."` : "";
      return `<g${open}><title>${tip}${d.sessions ? " · click to show these sessions" : ""}</title>
        ${d.sessions ? `<rect x="${(i * width).toFixed(2)}" y="0" width="${width.toFixed(2)}" height="62" class="hit"/>` : ""}
        <rect x="${x}" y="${60 - h}" width="${w}" height="${Math.max(h, 1)}" class="${h ? "bar" : "bar empty"}"/>
        ${shippedH ? `<rect x="${x}" y="${60 - shippedH}" width="${w}" height="${shippedH}" class="bar shipped"/>` : ""}
        ${unknownH ? `<rect x="${x}" y="${60 - h}" width="${w}" height="${unknownH}" class="bar nogit"/>` : ""}
      </g>`;
    })
    .join("");
  const active = days.filter((d) => d.sessions).length;
  return `<section class="column-block vibe-activity">
    <header><h2>Last ${days.length} days</h2><span class="muted small">${plural(active, "day")} with sessions · <i class="key shipped"></i> ${OUTCOMES.shipped} <i class="key none"></i> no code change${Object.keys(noGit).length ? ` <i class="key nogit"></i> no git` : ""}</span></header>
    <svg viewBox="0 0 100 62" preserveAspectRatio="none" role="img" aria-label="${byMinutes ? "Minutes" : "Sessions"} per day for the last ${days.length} days">${bars}</svg>
    <div class="axis"><span>${esc(new Date(`${days[0].day}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" }))}</span>${peakLabel(days, byMinutes, value)}<span>today</span></div>
  </section>`;
}

// Gives the bars a scale: the tallest day, by clock time (or sessions when no times are known).
function peakLabel(days, byMinutes, value) {
  const peak = days.reduce((best, d) => (value(d) > value(best) ? d : best), days[0]);
  if (!value(peak)) return "";
  const amount = byMinutes ? duration(peak.minutes) : plural(peak.sessions, "session");
  const date = new Date(`${peak.day}T12:00:00`);
  const weekday = date.toLocaleDateString(undefined, { weekday: "short" });
  const dayMonth = date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return `<span class="peak">peak ${esc(amount)} · <span class="peak-wd">${esc(weekday)}, </span>${esc(dayMonth)}</span>`;
}

function skillsBlock(stats) {
  const entries = Object.entries(stats.skills).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
  const body = entries.length
    ? `<div class="skill-chips">${entries.map(([name, count]) => `<span class="skill-chip"><code>${esc(name)}</code><b>${count}</b></span>`).join("")}</div>`
    : `<p class="empty">No skill runs yet. Log one with <code>cairn vibe skill /refine "6.5 -> 8.7"</code> or type <code>/council …</code> in a session's log.</p>`;
  const runs = plural(entries.reduce((t, [, c]) => t + c, 0), "run");
  return sideBlock("vibe-skills", "Skills", runs, body, !entries.length);
}

// On a phone an empty block is one tappable line, so the page doesn't scroll through empty forms and hints
// before the agent instructions. Desktop always shows the full block.
function sideBlock(cls, title, meta, body, empty) {
  const narrow = typeof matchMedia === "function" && matchMedia("(max-width: 600px)").matches;
  if (!(empty && narrow)) {
    return `<section class="column-block ${cls}"><header><h2>${title}</h2><span class="muted small">${meta}</span></header>${body}</section>`;
  }
  const open = foldOpen(cls);
  return `<details class="column-block ${cls} vibe-fold" data-block="${cls}"${open ? " open" : ""}>
    <summary><h2>${title}</h2><span class="muted small">${meta}</span></summary>
    ${body}
  </details>`;
}

// "–13:28" when the session ended later the same day; nothing while it is live or when it ran past midnight.
function endClock(note, started) {
  const ended = String(note.meta.ended || "");
  if (note.status === "live" || !/T\d{2}:\d{2}/.test(ended) || ended.slice(0, 10) !== started.slice(0, 10)) return "";
  const end = ended.slice(11, 16);
  return end > started.slice(11, 16) ? `–${esc(end)}` : "";
}

function sessionRow(note, showSource = true) {
  const log = core.sessionLog(note.body);
  const passed = log.filter((e) => e.kind === "pass").length;
  const failed = log.filter((e) => e.kind === "fail").length;
  const minutes = note.status === "live" ? elapsedMinutes(note) : core.sessionMinutes(note);
  const added = Number(note.meta.added) || 0;
  const removed = Number(note.meta.removed) || 0;
  const commits = Number(note.meta.commits) || 0;
  const skillRuns = log.filter((e) => e.kind === "skill").length;
  const started = startedKey(note);
  const folder = folderName(note);
  const bits = [
    /T\d{2}:\d{2}/.test(started) ? `<span class="when">${esc(started.slice(11, 16))}${endClock(note, started)}</span>` : "",
    showSource ? `<span class="src">${esc(groupOf(note).label)}</span>` : "",
    folder ? `<span class="folder" title="${esc(String(note.meta.folder))}">${icon("folder", "sm")}${esc(folder)}</span>` : "",
    duration(minutes),
    commits ? `<span title="Commits in this folder while it ran">${icon("branch", "sm")}${commits}</span>` : "",
    skillRuns ? `<span title="${skillRuns} skill runs">${icon("terminal", "sm")}${skillRuns}</span>` : "",
    passed || failed ? `<span class="check-pass" title="${passed} checks passed">${icon("check", "sm")}${passed}</span> <span class="check-fail" title="${failed} checks failed">${icon("x", "sm")}${failed}</span>` : "",
    added || removed ? `<b class="plus">+${added}</b> <b class="minus">−${removed}</b>` : "",
  ].filter(Boolean);
  return `<button class="row-item session-row" data-open="${esc(note.path)}">
    <span class="dot t-session s-${esc(outcomeOf(note))}" title="${esc(DOT_LABEL[outcomeOf(note)] || note.status)}"></span>
    <span class="title">${esc(note.title)}</span>
    ${note.status === "done" || note.status === "shipped" ? "" : statusPill(note.status)}
    <span class="meta">${bits.join(" · ")}</span>
  </button>`;
}

function sessionsBlock(sessions, stats) {
  const { counts, labels, present, filter, fromSource, outcomeCounts, outcome } = vibeFilters(sessions);
  const shown = outcome === "all" ? fromSource : fromSource.filter((note) => outcomeOf(note) === outcome);
  const limit = state.vibeLimit || PAGE;
  if (!state.vibeFolded && shown.length > PAGE) {
    state.vibeFolded = new Set([...new Set(shown.map(sessionDayOf))].slice(1));
  }
  const folded = state.vibeFolded || new Set();
  const days = new Map();
  for (const note of shown) {
    const day = sessionDayOf(note);
    if (!days.has(day)) days.set(day, []);
    days.get(day).push(note);
  }
  const allFolded = days.size > 0 && [...days.keys()].every((day) => folded.has(day));
  let left = limit;
  let accounted = 0;
  const groups = [];
  for (const [day, notes] of days) {
    if (left <= 0) break;
    const isFolded = folded.has(day);
    const visible = isFolded ? [] : notes.slice(0, left);
    left -= visible.length;
    accounted += isFolded ? notes.length : visible.length;
    const minutes = core.unionMinutes(notes.flatMap(core.sessionSpans));
    const shipped = notes.filter((note) => note.status === "shipped").length;
    groups.push(`<div class="day-group${isFolded ? " folded" : ""}">
      <div class="day-bar">
        <button type="button" class="day-head" data-action="vibe-day" data-day="${esc(day)}" aria-expanded="${!isFolded}">
          <span class="day-name">${icon("chevron", "sm")}${esc(dayLabel(day))}</span>
          <span class="muted small">${plural(notes.length, "session")}${shipped && isFolded ? ` · ${shipped} ${OUTCOMES.shipped}` : ""}${minutes ? ` · <span title="Clock time: sessions that overlap count once">${duration(minutes)}</span>` : ""}</span>
        </button>
        ${state.vibeJumped === day && !isFolded ? `<button type="button" class="btn sm ghost day-back" data-action="vibe-to-chart" data-day="${esc(day)}" title="Back to the chart">${icon("chevron", "sm")} Chart</button>` : ""}
      </div>
      ${isFolded ? "" : `<div class="rows">${visible.map((note) => sessionRow(note, filter === "all" && present.length > 1)).join("")}</div>`}
    </div>`);
  }
  const rest = shown.length - accounted;
  const sourceChips = present.length > 1
    ? `<div class="vibe-chip-group" role="group" aria-label="Show sessions by AI">
        ${["all", ...present].map((key) => `<button class="chip${key === filter ? " on" : ""}" data-action="vibe-filter" data-source="${esc(key)}" aria-pressed="${key === filter}">${key === "all" ? "All" : esc(labels[key])} <b>${counts[key]}</b></button>`).join("")}
      </div>`
    : present.length && present[0] !== "other"
      ? `<span class="chip static" title="Every session here ran in ${esc(labels[present[0]])}.">All from ${esc(labels[present[0]])} <b>${counts[present[0]]}</b></span>`
      : "";
  const outcomesHere = Object.keys(OUTCOMES).filter((key) => outcomeCounts[key]);
  const outcomeChips = outcomesHere.length
    ? `<div class="vibe-chip-group vibe-outcomes" role="group" aria-label="Show sessions by outcome">
        ${outcomesHere.map((key) => `<button class="chip outcome${key === outcome ? " on" : ""}" data-action="vibe-outcome" data-outcome="${key}" aria-pressed="${key === outcome}" title="${esc(DOT_LABEL[key] || OUTCOMES[key])}"><i class="dot t-session s-${key}" aria-hidden="true"></i>${OUTCOMES[key]} <b>${outcomeCounts[key]}</b></button>`).join("")}
      </div>`
    : "";
  const chips = sourceChips || outcomeChips ? `<div class="vibe-filters">${sourceChips}${outcomeChips}</div>` : "";
  const unknown = sessions.filter((note) => outcomeOf(note) === "unknown").length;
  const results = [[OUTCOMES.shipped, stats.shipped], ["partial", stats.partial], ["dropped", stats.dropped], ["no code change", stats.done - unknown], ["no git", unknown]]
    .filter(([, n]) => n)
    .map(([label, n]) => `${n} ${label}`)
    .join(" · ");
  const filteredOut = outcome !== "all"
    ? `<p class="muted small vibe-filter-note">Showing ${plural(shown.length, "session")} with ${OUTCOMES[outcome]}. <a href="#" data-action="vibe-outcome" data-outcome="${outcome}">Show every outcome</a></p>`
    : "";
  return `<section class="column-block vibe-sessions">
    <header><h2>Sessions</h2><span class="muted small">${results || "none yet"}</span></header>
    ${chips}
    ${shown.length ? `<p class="vibe-row-key muted small">Each row: start–end · folder · active minutes${shown.some((n) => Number(n.meta.commits)) ? " · commits" : ""}</p>` : ""}
    ${days.size > 1 ? `<div class="vibe-fold-row"><button type="button" class="btn sm ghost" data-action="vibe-fold-all" data-days="${esc([...days.keys()].join(","))}">${icon("chevron", "sm")} ${allFolded ? "Unfold all days" : `Fold all ${days.size} days`}</button></div>` : ""}
    ${filteredOut}
    ${sessions.length ? groups.join("") : `<p class="empty">No sessions yet. Each one becomes a file in brain/sessions/.</p>`}
    ${rest > 0 ? `<div class="vibe-more-row"><button class="btn sm vibe-more" data-action="vibe-more">Show ${Math.min(PAGE, rest)} more <span class="muted">· ${rest} left</span></button>${rest > PAGE ? `<button class="btn sm ghost" data-action="vibe-more" data-all="1">Show all ${shown.length}</button>` : ""}</div>` : ""}
  </section>`;
}

function feedbackRow(note) {
  const rating = core.feedbackRating(note);
  const from = typeof note.meta.from === "string" ? note.meta.from : "";
  const about = noteLink(note.meta.about);
  const status = readOnly()
    ? statusPill(note.status)
    : `<select class="select sm" data-fb-status="${esc(note.path)}" aria-label="Status of “${esc(note.title)}”">
        ${core.STATUSES.feedback.map((s) => `<option value="${s}" ${s === note.status ? "selected" : ""}>${{ new: "New", planned: "Planned", addressed: "Addressed", wontfix: "Won't fix" }[s]}</option>`).join("")}
      </select>`;
  return `<div class="fb-row">
    <div class="fb-main">
      <a href="#" class="fb-title" data-open="${esc(note.path)}">${esc(note.title)}</a>
      <span class="muted small">${[stars(rating), from ? esc(from) : "", about ? `on ${about}` : "", esc(note.meta.created || "")].filter(Boolean).join(" · ")}</span>
    </div>
    ${status}
  </div>`;
}

function ratingSummary(stats) {
  if (stats.rating === null) return "";
  const most = Math.max(1, ...stats.ratings);
  const rows = [5, 4, 3, 2, 1]
    .map((n) => `<div class="dist-row"><span class="star-label" aria-label="${n} stars">${n}${icon("star", "sm")}</span><i style="--w:${Math.round((stats.ratings[n - 1] / most) * 100)}%"></i><span>${stats.ratings[n - 1]}</span></div>`)
    .join("");
  return `<div class="rating-summary"><div class="big">${stats.rating.toFixed(1)}<span>/5</span></div><div class="dist">${rows}</div></div>`;
}

function feedbackForm(sessions) {
  if (readOnly()) {
    const { owner, repoName } = state.info || {};
    if (!owner || !repoName) return "";
    const url = `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/issues/new?title=${encodeURIComponent("[Cairn feedback] ")}&body=${encodeURIComponent("What worked, what didn't, and a rating out of 5:\n\n")}`;
    return `<a class="btn" href="${url}" target="_blank" rel="noopener">${icon("external", "sm")} Give @${esc(owner)} feedback</a>`;
  }
  const draft = state.vibeDraft || {};
  const latest = sessions[0]?.slug || "";
  const about = draft.about ?? latest;
  return `<form class="fb-form" id="fb-form" autocomplete="off">
    <label class="sr-only" for="fb-text">Feedback</label>
    <textarea class="input" id="fb-text" name="text" rows="2" placeholder="What did they say? e.g. “The toggle is hard to find on mobile”">${esc(draft.text || "")}</textarea>
    <div class="fb-form-row">
      <select class="select sm" name="rating" aria-label="Rating">
        <option value="">No rating</option>
        ${[5, 4, 3, 2, 1].map((n) => `<option value="${n}" ${String(n) === String(draft.rating) ? "selected" : ""}>${n} out of 5</option>`).join("")}
      </select>
      <input class="input sm" name="from" placeholder="From (e.g. @alice)" value="${esc(draft.from || "")}">
      <select class="select sm" name="about" aria-label="About which session">
        <option value="">Not about a session</option>
        ${sessions.slice(0, 20).map((s) => `<option value="${esc(s.slug)}" ${s.slug === about ? "selected" : ""}>${esc(s.title)}</option>`).join("")}
      </select>
      <button class="btn primary sm" type="submit">${icon("plus", "sm")} Add</button>
    </div>
  </form>`;
}

// The computers this brain holds sessions of, as chips: all of them, or one alone. Drawn only when there is a
// choice to make. A session reaches this page through the brain, so another computer is as current as its last sync.
let severalDevices = false;

function deviceBar(list) {
  if (list.length < 2) return "";
  const shown = shownDevice();
  const total = (key) => list.reduce((sum, device) => sum + device[key], 0);
  const liveText = (count) => (count ? ` <span class="vd-live">${count} live</span>` : "");
  const chip = (device, label, sessions, live) =>
    `<button class="chip${device === shown ? " on" : ""}" data-action="vibe-device"${device === null ? "" : ` data-device="${esc(device)}"`} aria-pressed="${device === shown}"><span class="vd-name">${label}</span> <b>${sessions}</b>${liveText(live)}</button>`;
  const name = (device) =>
    device.name ? `${esc(device.name)}${device.name === state.vibeHere ? ` <span class="vd-here">this computer</span>` : ""}` : "No computer named";
  const picked = list.find((device) => device.name === shown);
  const elsewhere = picked && picked.name && state.vibeHere && picked.name !== state.vibeHere;
  const lastSeen = picked ? recapWhen(core.stampMinutes(picked.last)) : "";
  const note = picked
    ? `<p class="muted small vibe-device-note">${plural(picked.sessions, "session")}${picked.tokens ? ` · ${compact(picked.tokens)} tokens` : ""}${lastSeen ? ` · last active ${esc(lastSeen)}` : ""}${elsewhere ? " · as of its last sync into this brain" : ""}</p>`
    : "";
  return `<div class="vibe-devices" role="group" aria-label="Computer">
      ${chip(null, "All devices", total("sessions"), total("live"))}
      ${list.map((device) => chip(device.name, name(device), device.sessions, device.live)).join("")}
    </div>
    ${note}`;
}

export function shareStats() {
  openShare(core.vibeStats(vibeNotes(), today()));
}

export function vibeView() {
  // A computer that is no longer in the brain (or the only one left) cannot be the one shown.
  const devices = deviceList(state.notes);
  severalDevices = devices.length > 1;
  if (shownDevice() !== null && !(severalDevices && devices.some((device) => device.name === shownDevice()))) state.vibeDevice = null;
  const stats = core.vibeStats(vibeNotes(), today());
  const sessions = sessionNotes();
  // Detected sessions only "ship" in the sense that code changed; call it that unless a person ended one as shipped.
  const handShipped = sessions.some((note) => sessionSource(note) === "logged" && note.status === "shipped");
  OUTCOMES.shipped = handShipped ? "shipped" : "code changed";
  DOT_LABEL.shipped = handShipped ? "Shipped: changed or committed code" : "Code changed: commits or line changes seen in the session's folder";
  const order = { new: 0, planned: 1, addressed: 2, wontfix: 3 };
  const feedback = state.notes
    .filter((n) => n.type === "feedback")
    .sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9) || String(b.meta.created || "").localeCompare(String(a.meta.created || "")) || b.mtime - a.mtime);
  const head = pageHead(
    "Vibe",
    "Live progress of your AI coding sessions and what came out of them. Every session is a Markdown file in <code>brain/</code>.",
    `${stats.sessions ? `<button class="btn" data-action="vibe-share" title="Your last 14 days as a picture to post">${icon("external")} Share stats</button>` : ""}
    <button class="btn${liveSession() ? "" : " primary"}" data-action="vibe-start">${icon("play")} ${liveSession() ? "Start another" : "Start session"}</button>`,
  );
  const live = shownLive();
  const running = recap(vibeNotes(), stamp());
  const reading = machine();
  const meters = githubAccounts(state.notes).map((entry) => entry.months[0]);
  const [minutes] = meters;
  const minutesSummary = meters.length > 1
    ? meters.map((meter) => `${meter.account || "main"} ${meter.pct}%`).join(" · ")
    : minutes ? `${thousands(minutes.used)} of ${thousands(minutes.limit)} free minutes · ${minutes.pct}%` : "";
  const blocks = {
    recap: {
      html: recapBand(),
      summary: Object.keys(RECAP_CHIPS).filter((key) => running.counts[key]).map((key) => `${running.counts[key]} ${RECAP_CHIPS[key]}`).join(" · ") || `nothing running${running.finishedTotal ? ` · ${running.finishedTotal} finished in the last day` : ""}`,
    },
    machine: {
      html: machineStrip(),
      summary: reading ? `${EFFORT_LABEL[reading.level]} load · CPU ${reading.cpu_pct ?? "?"}% · memory ${reading.ram_pct ?? "?"}%` : "",
      // Another computer picked in the device bar is not "This computer".
      label: shownDevice() !== null && shownDevice() !== (state.vibeHere || "") ? "Computer" : "",
    },
    github: { html: githubStrip(), summary: [minutesSummary, pushChoice()?.pusher ? `pushes as ${pushChoice().pusher}` : ""].filter(Boolean).join(" · ") },
    overview: { html: overview(sessions), summary: `${plural(sessions.length, "session")}${live.length ? ` · ${live.length} live now` : ""}` },
    figures: { html: figures(stats), summary: `${plural(stats.sessions, "session")} · ${stats.shipped} ${OUTCOMES.shipped}` },
    activity: { html: activity(stats, sessions), summary: "sessions per day" },
    sessions: { html: sessionsBlock(sessions, stats), summary: plural(sessions.length, "session") },
    // Feedback is a block only once the brain holds some (`fb: …` in the capture bar or `cairn feedback` adds the first).
    feedback: {
      html: feedback.length
        ? sideBlock(
            "vibe-feedback",
            "Feedback",
            `${stats.feedback_open} open · ${stats.feedback_addressed} addressed`,
            `${ratingSummary(stats)}
        ${feedbackForm(sessions)}
        <div class="fb-list">${feedback.slice(0, 40).map(feedbackRow).join("")}</div>`,
            false,
          )
        : "",
      summary: `${stats.feedback_open} open · ${stats.feedback_addressed} addressed`,
    },
    skills: { html: skillsBlock(stats), summary: plural(Object.values(stats.skills).reduce((total, count) => total + count, 0), "run") },
    models: { html: modelsBlock(stats), summary: plural(shownModels(stats).length, "model") },
    // The setup hint for a brain with no session yet; once sessions arrive on their own it has done its job.
    hook: {
      html: readOnly() || sessions.length ? "" : `<section class="callout vibe-hook">${icon("terminal")}<div>
      <p><strong>Let your assistant report its own progress.</strong> Paste this into <code>AGENTS.md</code>, <code>CLAUDE.md</code> or <code>.github/copilot-instructions.md</code>; every step lands in the live session above.</p>
      <pre class="pre-box">${esc(AGENT_HOOK)}</pre>
      <button class="btn sm" data-action="vibe-copy-hook">${icon("copy", "sm")} Copy instructions</button>
    </div></section>`,
      summary: "paste into AGENTS.md so your assistant reports its own steps",
    },
  };
  return `${head}
    ${deviceBar(devices)}
    ${boardHtml("vibe", blocks)}`;
}
