// Vibe: live progress of AI-assisted coding sessions, what came out of them, and what people said.
// A session is brain/sessions/YYYY-MM-DD-slug.md (status: live while you work, a log of timestamped
// steps); feedback is brain/feedback/slug.md with an optional 1-5 rating. With the local app the view
// polls the server, so steps your assistant logs from the terminal (cairn vibe log) show up as they happen.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { $, $$, ask, icon, pageHead, plural, statusPill, toast, today } from "./ui.js";
import { createNote, fileByPath, noteByPath, readOnly, saveNote, state, upsertFile } from "./state.js";
import { renderNav, renderView } from "./app.js";

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

// ============================================================ helpers

const clock = () => new Date().toTimeString().slice(0, 5);
const stamp = () => `${today()}T${clock()}`;
const oneLine = (text) => String(text).replace(/\s+/g, " ").trim();
const canPoll = () => typeof state.store?.vibe === "function";

function startedKey(note) {
  return `${note.meta.started || note.meta.created || ""} ${note.path}`;
}

// Every live session, newest first: parallel agents (one per terminal or worktree) each keep their own.
export function liveSessions() {
  return state.notes
    .filter((n) => n.type === "session" && n.status === "live")
    .sort((a, b) => (startedKey(a) < startedKey(b) ? 1 : -1));
}

export function liveSession() {
  return liveSessions()[0] || null;
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

export function setVibeFilter(source) {
  state.vibeFilter = source in SOURCES ? source : "all";
  state.vibeLimit = PAGE;
  renderView();
}

export function setVibeOutcome(outcome) {
  state.vibeOutcome = outcome in OUTCOMES && state.vibeOutcome !== outcome ? outcome : "all";
  state.vibeLimit = PAGE;
  renderView();
}

// Folded days stay folded across live updates; a folded day shows its totals and costs no rows of the page.
export function toggleSessionDay(day) {
  const folded = state.vibeFolded || (state.vibeFolded = new Set());
  if (folded.has(day)) folded.delete(day);
  else folded.add(day);
  renderView();
  $(`.day-head[data-day="${CSS.escape(day)}"]`)?.focus();
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
  await createNote({
    type: "session",
    title,
    status: "live",
    extraMeta,
    body: `## Goal\n\n${title}\n\n## Log\n\n- ${clock()} start: ${title}\n`,
  });
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

export function copyAgentHook() {
  navigator.clipboard?.writeText(AGENT_HOOK).then(
    () => toast("Copied. Paste it into AGENTS.md, CLAUDE.md or your assistant's instructions."),
    () => toast("Couldn't copy; select the text instead.", { error: true }),
  );
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
  if (changed && state.view === "vibe") {
    renderNav();
    renderView();
  } else {
    for (const clockEl of $$(".vibe-elapsed")) {
      const note = noteByPath(clockEl.dataset.path);
      if (note) clockEl.textContent = duration(elapsedMinutes(note));
    }
  }
}

export function wireVibe() {
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
  return state.notes
    .filter((n) => n.type === "session" && n.status !== "live")
    .sort((a, b) => (key(a) < key(b) ? 1 : -1))[0] || null;
}

function liveCards() {
  const live = liveSessions();
  if (!live.length) {
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
  return live.map((session, index) => liveCard(session, index === 0, live.length)).join("");
}

function liveCard(live, newest = true, total = 1) {
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
  return `<section class="vibe-live${newest ? "" : " also"}" aria-label="Live session: ${esc(live.title)}">
    <header>
      <div>
        <p class="eyebrow"><span class="live-dot" aria-hidden="true"></span>Live · <span class="vibe-elapsed" data-path="${esc(live.path)}">${duration(elapsedMinutes(live))}</span>${lastCheck ? ` · last check <span class="check-${lastCheck.kind}">${lastCheck.kind === "pass" ? "passed" : "failed"}</span>` : ""}</p>
        <h2><a href="#" data-open="${esc(live.path)}">${esc(live.title)}</a></h2>
        <p class="muted small">${[agent, task ? `for ${task}` : "", skills.length ? `skills ${skills.map((s) => `<code>${esc(s)}</code>`).join(" ")}` : "", total > 1 ? `<code>CAIRN_SESSION=${esc(live.slug)}</code>` : ""].filter(Boolean).join(" · ")}</p>
      </div>
      ${controls}
    </header>
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

function figures(stats) {
  const figure = (value, label, title = "", sub = "") =>
    `<div class="figure"${title ? ` title="${esc(title)}"` : ""}><span class="value">${value}</span><span class="label">${label}</span>${sub ? `<span class="sub">${sub}</span>` : ""}</div>`;
  const dash = "–";
  const parallel = stats.parallel && stats.parallel >= 1.1;
  return `<div class="figures vibe-figures">
    ${figure(stats.sessions, stats.sessions === 1 ? "session" : "sessions", `${stats.shipped} shipped · ${stats.partial} partial · ${stats.dropped} dropped${stats.done ? ` · ${stats.done} with no code change` : ""}`)}
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
    ${figure(stats.feedback_open, "feedback open", `${stats.feedback} total · ${stats.feedback_addressed} addressed`)}
    ${stats.rating === null
      ? figure(dash, "avg rating", "No rated feedback yet. Add feedback with a 1-5 rating below.", "no ratings yet")
      : figure(stats.rating.toFixed(1), "avg rating", "Out of 5")}
  </div>`;
}

// Detected sessions only ever end as shipped (commits or line changes seen) or done (none seen), so a
// ship rate over "finished" sessions would always read 100%. Show the count against every ended session.
function shippedFigure(stats, figure, dash) {
  const ended = stats.shipped + stats.partial + stats.dropped + stats.done;
  if (!ended) return figure(dash, "shipped", "No finished sessions yet.");
  const share = Math.round((stats.shipped / ended) * 100);
  const handRated = stats.partial + stats.dropped;
  const title = handRated
    ? `Ship rate ${stats.ship_rate}% of sessions you ended with a result (shipped, partial or dropped).`
    : "Sessions that committed or changed code in their folder.";
  return figure(stats.shipped, "shipped", title, stats.shipped ? `${share}% of ${ended} sessions` : "no code changes seen yet");
}

function activity(stats, sessions = []) {
  const days = stats.daily;
  // Sessions with no git have no evidence either way, so the chart keeps them apart from "no code change".
  const noGit = {};
  for (const note of sessions) {
    if (outcomeOf(note) === "unknown") noGit[sessionDayOf(note)] = (noGit[sessionDayOf(note)] || 0) + 1;
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
      const tip = `${d.day}: ${plural(d.sessions, "session")}${byMinutes ? `, ${duration(d.minutes)}` : ""}${d.shipped ? `, ${d.shipped} shipped` : ""}${unknown ? `, ${unknown} no git` : ""}`;
      return `<g><title>${tip}</title>
        <rect x="${x}" y="${60 - h}" width="${w}" height="${Math.max(h, 1)}" class="${h ? "bar" : "bar empty"}"/>
        ${shippedH ? `<rect x="${x}" y="${60 - shippedH}" width="${w}" height="${shippedH}" class="bar shipped"/>` : ""}
        ${unknownH ? `<rect x="${x}" y="${60 - h}" width="${w}" height="${unknownH}" class="bar nogit"/>` : ""}
      </g>`;
    })
    .join("");
  const active = days.filter((d) => d.sessions).length;
  return `<section class="column-block vibe-activity">
    <header><h2>Last ${days.length} days</h2><span class="muted small">${plural(active, "day")} with sessions · <i class="key shipped"></i> shipped <i class="key none"></i> no code change${Object.keys(noGit).length ? ` <i class="key nogit"></i> no git` : ""}</span></header>
    <svg viewBox="0 0 100 62" preserveAspectRatio="none" role="img" aria-label="${byMinutes ? "Minutes" : "Sessions"} per day for the last ${days.length} days">${bars}</svg>
    <div class="axis"><span>${days[0].day.slice(5)}</span>${peakLabel(days, byMinutes, value)}<span>today</span></div>
  </section>`;
}

// Gives the bars a scale: the tallest day, by clock time (or sessions when no times are known).
function peakLabel(days, byMinutes, value) {
  const peak = days.reduce((best, d) => (value(d) > value(best) ? d : best), days[0]);
  if (!value(peak)) return "";
  const amount = byMinutes ? duration(peak.minutes) : plural(peak.sessions, "session");
  return `<span class="peak">peak ${esc(amount)} · ${esc(dayLabel(peak.day))}</span>`;
}

function skillsBlock(stats) {
  const entries = Object.entries(stats.skills).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
  const body = entries.length
    ? `<div class="skill-chips">${entries.map(([name, count]) => `<span class="skill-chip"><code>${esc(name)}</code><b>${count}</b></span>`).join("")}</div>`
    : `<p class="empty">No skill runs yet. Log one with <code>cairn vibe skill /refine "6.5 -> 8.7"</code> or type <code>/council …</code> in a session's log.</p>`;
  return `<section class="column-block vibe-skills">
    <header><h2>Skills</h2><span class="muted small">${plural(entries.reduce((t, [, c]) => t + c, 0), "run")}</span></header>
    ${body}
  </section>`;
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
    showSource ? `<span class="src">${SOURCES[sessionSource(note)]}</span>` : "",
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
  const counts = { all: sessions.length };
  for (const note of sessions) counts[sessionSource(note)] = (counts[sessionSource(note)] || 0) + 1;
  const present = Object.keys(SOURCES).filter((source) => counts[source]);
  const filter = state.vibeFilter in SOURCES && counts[state.vibeFilter] ? state.vibeFilter : "all";
  const fromSource = filter === "all" ? sessions : sessions.filter((note) => sessionSource(note) === filter);
  const outcomeCounts = {};
  for (const note of fromSource) outcomeCounts[outcomeOf(note)] = (outcomeCounts[outcomeOf(note)] || 0) + 1;
  const outcome = state.vibeOutcome in OUTCOMES && outcomeCounts[state.vibeOutcome] ? state.vibeOutcome : "all";
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
      <button type="button" class="day-head" data-action="vibe-day" data-day="${esc(day)}" aria-expanded="${!isFolded}">
        <span class="day-name">${icon("chevron", "sm")}${esc(dayLabel(day))}</span>
        <span class="muted small">${plural(notes.length, "session")}${shipped && isFolded ? ` · ${shipped} shipped` : ""}${minutes ? ` · <span title="Clock time: sessions that overlap count once">${duration(minutes)}</span>` : ""}</span>
      </button>
      ${isFolded ? "" : `<div class="rows">${visible.map((note) => sessionRow(note, filter === "all" && present.length > 1)).join("")}</div>`}
    </div>`);
  }
  const rest = shown.length - accounted;
  const sourceChips = present.length > 1
    ? `<div class="vibe-chip-group" role="group" aria-label="Show sessions from">
        ${["all", ...present].map((source) => `<button class="chip${source === filter ? " on" : ""}" data-action="vibe-filter" data-source="${source}" aria-pressed="${source === filter}">${source === "all" ? "All" : SOURCES[source]} <b>${counts[source]}</b></button>`).join("")}
      </div>`
    : present.length
      ? `<span class="chip static" title="${present[0] === "logged" ? "Every session was started by hand with cairn vibe start." : "Every session here was detected automatically. Sessions you start with cairn vibe start show up as Logged."}">All from ${SOURCES[present[0]]} <b>${counts[present[0]]}</b></span>${present[0] === "logged" ? "" : `<span class="muted small vibe-src-note">0 logged by hand</span>`}`
      : "";
  const outcomesHere = Object.keys(OUTCOMES).filter((key) => outcomeCounts[key]);
  const outcomeChips = outcomesHere.length
    ? `<div class="vibe-chip-group vibe-outcomes" role="group" aria-label="Show sessions by outcome">
        ${outcomesHere.map((key) => `<button class="chip outcome${key === outcome ? " on" : ""}" data-action="vibe-outcome" data-outcome="${key}" aria-pressed="${key === outcome}" title="${esc(DOT_LABEL[key] || OUTCOMES[key])}"><i class="dot t-session s-${key}" aria-hidden="true"></i>${OUTCOMES[key]} <b>${outcomeCounts[key]}</b></button>`).join("")}
      </div>`
    : "";
  const chips = sourceChips || outcomeChips ? `<div class="vibe-filters">${sourceChips}${outcomeChips}</div>` : "";
  const unknown = sessions.filter((note) => outcomeOf(note) === "unknown").length;
  const results = [["shipped", stats.shipped], ["partial", stats.partial], ["dropped", stats.dropped], ["no code change", stats.done - unknown], ["no git", unknown]]
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

export function vibeView() {
  const stats = core.vibeStats(state.notes, today());
  const sessions = state.notes.filter((n) => n.type === "session").sort((a, b) => (startedKey(a) < startedKey(b) ? 1 : -1));
  const order = { new: 0, planned: 1, addressed: 2, wontfix: 3 };
  const feedback = state.notes
    .filter((n) => n.type === "feedback")
    .sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9) || String(b.meta.created || "").localeCompare(String(a.meta.created || "")) || b.mtime - a.mtime);
  const head = pageHead(
    "Vibe",
    "Live progress of your AI coding sessions, what came out of them, and what people said about it. Every session and every piece of feedback is a Markdown file in <code>brain/</code>.",
    `<button class="btn${liveSession() ? "" : " primary"}" data-action="vibe-start">${icon("play")} ${liveSession() ? "Start another" : "Start session"}</button>`,
  );
  return `${head}
    ${liveCards()}
    ${figures(stats)}
    <div class="vibe-cols">
      <div>
        ${activity(stats, sessions)}
        ${sessionsBlock(sessions, stats)}
      </div>
      <div>
      <section class="column-block vibe-feedback">
        <header><h2>Feedback</h2><span class="muted small">${stats.feedback_open} open · ${stats.feedback_addressed} addressed</span></header>
        ${ratingSummary(stats)}
        ${feedbackForm(sessions)}
        ${feedback.length ? `<div class="fb-list">${feedback.slice(0, 40).map(feedbackRow).join("")}</div>` : `<p class="empty">No feedback yet. Capture it here, with <code>feedback:</code> in the bar above, or <code>cairn feedback "…" --rating 4</code>.</p>`}
      </section>
      ${skillsBlock(stats)}
      </div>
    </div>
    ${readOnly() ? "" : `<section class="callout vibe-hook">${icon("terminal")}<div>
      <p><strong>Let your assistant report its own progress.</strong> Paste this into <code>AGENTS.md</code>, <code>CLAUDE.md</code> or <code>.github/copilot-instructions.md</code>; every step lands in the live session above.</p>
      <pre class="pre-box">${esc(AGENT_HOOK)}</pre>
      <button class="btn sm" data-action="vibe-copy-hook">${icon("copy", "sm")} Copy instructions</button>
    </div></section>`}`;
}
