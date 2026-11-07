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
const RESULT_LABEL = { shipped: "Shipped", partial: "Partial", dropped: "Dropped" };
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

function stars(rating) {
  if (!rating) return "";
  return `<span class="stars" aria-label="${rating} out of 5">${"★".repeat(rating)}<span>${"☆".repeat(5 - rating)}</span></span>`;
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
  const mark = entry.kind === "pass" ? icon("check", "sm") : entry.kind === "fail" ? icon("x", "sm") : entry.kind === "skill" ? icon("sparkle", "sm") : "";
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

function liveCards() {
  const live = liveSessions();
  if (!live.length) {
    const sample = !readOnly() && state.info?.canLoadSample && !state.notes.some((n) => n.type === "session")
      ? ` <button class="btn sm" data-action="vibe-sample">${icon("play", "sm")} Load a sample week</button>`
      : "";
    return `<section class="vibe-live idle">
      <p>${icon("zap")} <strong>Nothing live.</strong> ${readOnly() ? "" : `Start a session here, or from your terminal: <code>cairn vibe start "add dark mode"</code>.`}${sample}</p>
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
  const figure = (value, label, title = "") => `<div class="figure"${title ? ` title="${esc(title)}"` : ""}><span class="value">${value}</span><span class="label">${label}</span></div>`;
  const dash = "–";
  return `<div class="figures vibe-figures">
    ${figure(stats.sessions, stats.sessions === 1 ? "session" : "sessions", `${stats.shipped} shipped · ${stats.partial} partial · ${stats.dropped} dropped`)}
    ${figure(duration(stats.minutes), "time vibing", "Sum of finished sessions")}
    ${figure(stats.ship_rate === null ? dash : `${stats.ship_rate}%`, "shipped", "Finished sessions that shipped")}
    ${figure(stats.pass_rate === null ? dash : `${stats.pass_rate}%`, "checks green", `${stats.passed} passed · ${stats.failed} failed`)}
    ${figure(stats.feedback_open, "feedback open", `${stats.feedback} total · ${stats.feedback_addressed} addressed`)}
    ${figure(stats.rating === null ? dash : stats.rating.toFixed(1), "avg rating", "Out of 5")}
  </div>`;
}

function activity(stats) {
  const days = stats.daily;
  const byMinutes = days.some((d) => d.minutes > 0);
  const value = (d) => (byMinutes ? d.minutes : d.sessions);
  const max = Math.max(1, ...days.map(value));
  const width = 100 / days.length;
  const bars = days
    .map((d, i) => {
      const h = Math.round((value(d) / max) * 54);
      const shippedH = d.sessions ? Math.round(h * (d.shipped / d.sessions)) : 0;
      const tip = `${d.day}: ${plural(d.sessions, "session")}${byMinutes ? `, ${duration(d.minutes)}` : ""}${d.shipped ? `, ${d.shipped} shipped` : ""}`;
      return `<g><title>${tip}</title>
        <rect x="${(i * width + width * 0.18).toFixed(2)}" y="${60 - h}" width="${(width * 0.64).toFixed(2)}" height="${Math.max(h, 1)}" class="${h ? "bar" : "bar empty"}"/>
        ${shippedH ? `<rect x="${(i * width + width * 0.18).toFixed(2)}" y="${60 - shippedH}" width="${(width * 0.64).toFixed(2)}" height="${shippedH}" class="bar shipped"/>` : ""}
      </g>`;
    })
    .join("");
  const active = days.filter((d) => d.sessions).length;
  return `<section class="column-block vibe-activity">
    <header><h2>Last ${days.length} days</h2><span class="muted small">${plural(active, "day")} with sessions · <i class="key shipped"></i> shipped</span></header>
    <svg viewBox="0 0 100 62" preserveAspectRatio="none" role="img" aria-label="${byMinutes ? "Minutes" : "Sessions"} per day for the last ${days.length} days">${bars}</svg>
    <div class="axis"><span>${days[0].day.slice(5)}</span><span>today</span></div>
  </section>`;
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

function sessionRow(note) {
  const log = core.sessionLog(note.body);
  const passed = log.filter((e) => e.kind === "pass").length;
  const failed = log.filter((e) => e.kind === "fail").length;
  const minutes = note.status === "live" ? elapsedMinutes(note) : core.sessionMinutes(note);
  const added = Number(note.meta.added) || 0;
  const removed = Number(note.meta.removed) || 0;
  const skillRuns = log.filter((e) => e.kind === "skill").length;
  const bits = [
    duration(minutes),
    skillRuns ? `${icon("sparkle", "sm")}${skillRuns}` : "",
    passed || failed ? `<span class="check-pass">✓${passed}</span> <span class="check-fail">✗${failed}</span>` : "",
    added || removed ? `<b class="plus">+${added}</b> <b class="minus">−${removed}</b>` : "",
  ].filter(Boolean);
  return `<button class="row-item session-row" data-open="${esc(note.path)}">
    <span class="dot t-session"></span>
    <span class="title">${esc(note.title)}</span>
    ${statusPill(note.status)}
    <span class="meta">${bits.join(" · ")}</span>
  </button>`;
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
    .map((n) => `<div class="dist-row"><span>${n}★</span><i style="--w:${Math.round((stats.ratings[n - 1] / most) * 100)}%"></i><span>${stats.ratings[n - 1]}</span></div>`)
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
        ${[5, 4, 3, 2, 1].map((n) => `<option value="${n}" ${String(n) === String(draft.rating) ? "selected" : ""}>${"★".repeat(n)} ${n}</option>`).join("")}
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
    `<button class="btn${liveSession() ? "" : " primary"}" data-action="vibe-start">${icon("zap")} ${liveSession() ? "Start another" : "Start session"}</button>`,
  );
  return `${head}
    ${liveCards()}
    ${figures(stats)}
    <div class="vibe-cols">
      <div>
        ${activity(stats)}
        ${skillsBlock(stats)}
        <section class="column-block">
          <header><h2>Sessions</h2><span class="muted small">${stats.shipped} shipped · ${stats.partial} partial · ${stats.dropped} dropped</span></header>
          ${sessions.length ? `<div class="rows">${sessions.slice(0, 30).map(sessionRow).join("")}</div>` : `<p class="empty">No sessions yet. Each one becomes a file in brain/sessions/.</p>`}
        </section>
      </div>
      <section class="column-block vibe-feedback">
        <header><h2>Feedback</h2><span class="muted small">${stats.feedback_open} open · ${stats.feedback_addressed} addressed</span></header>
        ${ratingSummary(stats)}
        ${feedbackForm(sessions)}
        ${feedback.length ? `<div class="fb-list">${feedback.slice(0, 40).map(feedbackRow).join("")}</div>` : `<p class="empty">No feedback yet. Capture it here, with <code>feedback:</code> in the bar above, or <code>cairn feedback "…" --rating 4</code>.</p>`}
      </section>
    </div>
    ${readOnly() ? "" : `<section class="callout vibe-hook">${icon("terminal")}<div>
      <p><strong>Let your assistant report its own progress.</strong> Paste this into <code>AGENTS.md</code>, <code>CLAUDE.md</code> or <code>.github/copilot-instructions.md</code>; every step lands in the live session above.</p>
      <pre class="pre-box">${esc(AGENT_HOOK)}</pre>
      <button class="btn sm" data-action="vibe-copy-hook">${icon("copy", "sm")} Copy instructions</button>
    </div></section>`}`;
}
