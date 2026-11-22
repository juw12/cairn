// Home (the Today page): what is waiting for you, what is running, the day's numbers, an activity chart you
// can tap through, the latest dated updates with their pictures, and the computers that feed this brain.
// What the computers have left for each other is its own block, in handoffs.js.
// A block with nothing to say is left out, so a brain without agent sessions keeps its plain page.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { $, $$, STATUS_LABEL, TYPE_META, byRecent, icon, plural, relativeTime, snippetOf, today } from "./ui.js";
import { state } from "./state.js";
import { compact, minutesText, recap } from "./recap.js";
import { agentGroup, deviceList, githubAccounts, machineFromNote } from "./progress.js";
import { addDays, journalDays, prettyDay, sessionDays, daySummaries } from "./daylog.js";
import { hydrateAssets } from "./vault.js";
import { dashboardHtml, noteRecap } from "./vibelayout.js";

const NEEDS_TAG = "needs-me";
const NEEDS_SHOWN = 4;
const LIVE_SHOWN = 5;
const UPDATES_SHOWN = 8;
const GITHUB_WARN_PCT = 80;
const RANGES = [14, 30, 90];
const METRICS = {
  sessions: { label: "Sessions", unit: ["session", "sessions"] },
  shipped: { label: "Shipped", unit: ["shipped", "shipped"] },
  tokens: { label: "Tokens", unit: ["token", "tokens"] },
  entries: { label: "Journal", unit: ["journal line", "journal lines"] },
};
const CHART_KEY = "cairn.homeChart";

const pad = (n) => String(n).padStart(2, "0");
const nowStamp = () => {
  const d = new Date();
  return `${today()}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const ago = (minutes) => (minutes < 2 ? "just now" : `${minutesText(minutes)} ago`);
// "2026-11-20T12:23" against now, in words; "" when the stamp cannot be read.
function stampAgo(stamp, now) {
  const at = core.stampMinutes(stamp);
  return at === null || now === null ? "" : ago(Math.max(0, now - at));
}

// ------------------------------------------------------------ dated updates and their pictures

// A note filed under its date: "2026-11-20 1148 reachpilot promotes the two lab repos", "2026-10-25 Twin 1.2 rename".
const DATED_RE = /^(\d{4}-\d{2}-\d{2})(?:[ _](\d{2})(\d{2})(?=[ _]))?[ _-]+(\S.*)$/;
// A name that carries a job number ("lab_squares_261120_35") is one of a run of automatic notes.
const JOB_RE = /[a-z]_\w*\d/i;
const IMAGE_RE = /!\[\[([^\]|#\n]+\.(?:png|jpe?g|gif|webp|avif|svg))(?:[|#][^\]\n]*)?\]\]|!\[[^\]\n]*\]\(([^)\s]+\.(?:png|jpe?g|gif|webp|avif|svg))(?:\s+"[^"]*")?\)/i;
const plainName = (text) => String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "");

function datedUpdate(note) {
  if (note.type !== "note" || core.isHandoff(note)) return null;
  const match = DATED_RE.exec(note.slug);
  if (!match) return null;
  const [, day, hour, minute, rest] = match;
  const title = note.title === note.slug ? rest : note.title;
  const series = JOB_RE.test(rest) ? rest.split(/[\s_]+/).slice(0, 2).join(" ").toLowerCase() : "";
  return { note, day, time: hour ? `${hour}:${minute}` : "", key: `${day}T${hour || "00"}:${minute || "00"}`, title, series };
}

function firstImage(note) {
  const match = IMAGE_RE.exec(String(note.body || "").replace(/```[\s\S]*?```/g, ""));
  if (!match) return null;
  const target = match[1] || match[2];
  if (/^https:\/\//i.test(target)) return { src: target };
  if (/^[a-z][a-z0-9+.-]*:/i.test(target)) return null;
  let asset = target.trim();
  try {
    asset = decodeURIComponent(asset);
  } catch {
    /* keep it as written */
  }
  return { asset, from: note.path };
}

// The projects a note names: `projects:` in its properties, its tags, and links to a project note.
function namedProjects(note) {
  const listed = note.meta?.projects;
  const names = Array.isArray(listed) ? [...listed] : typeof listed === "string" ? listed.split(",") : [];
  names.push(...(note.tags || []));
  for (const match of String(note.body || "").matchAll(/\[\[(?:GitHub|Projects?)\/([^\]|#\n]+)/gi)) names.push(match[1]);
  return [...new Set(names.map(plainName).filter(Boolean))];
}

// The picture that goes with an update: its own first one, else the first picture of a project it names
// (a project's pictures live in the folder that carries its name).
function pictureOf(update, shelf) {
  const own = firstImage(update.note);
  if (own) return own;
  for (const name of namedProjects(update.note)) if (shelf.has(name)) return shelf.get(name);
  return null;
}

function pictureShelf(notes) {
  const shelf = new Map();
  for (const note of notes) {
    // A dated update's picture belongs to that update, not to the folder the updates are kept in.
    if (!note.path.includes("/") || note.type === "session" || DATED_RE.test(note.slug)) continue;
    const folder = plainName(note.path.split("/")[0]);
    if (!folder || (shelf.has(folder) && !/project home/i.test(note.title))) continue;
    const image = firstImage(note);
    if (image) shelf.set(folder, image);
  }
  return shelf;
}

// Newest first, a run of automatic notes folded into its newest one: [{ ...update, more }].
function foldRuns(updates) {
  const out = [];
  const runs = new Map();
  for (const update of updates) {
    if (update.series && runs.has(update.series)) {
      runs.get(update.series).more += 1;
      continue;
    }
    const row = { ...update, more: 0 };
    if (update.series) runs.set(update.series, row);
    out.push(row);
  }
  return out;
}

// ------------------------------------------------------------ everything Home shows, worked out once per change

let memo = null;

export function homeData() {
  const stamp = nowStamp();
  if (memo && memo.notes === state.notes && memo.stamp === stamp) return memo.data;
  const notes = state.notes;
  const now = core.stampMinutes(stamp);
  const day = today();
  const sessions = notes.filter((note) => note.type === "session");
  const tasks = notes.filter((note) => note.type === "task");
  const open = tasks.filter((note) => note.status !== "done");
  const newest = (a, b) => String(b.meta.created || "").localeCompare(String(a.meta.created || "")) || (b.mtime || 0) - (a.mtime || 0);
  const running = recap(notes, stamp);
  const perDay = sessionDays(notes);
  const journal = journalDays(notes);
  // A note stamped later than now ("2026-11-20 2350 ..." written at three in the morning) has a wrong clock
  // in its name: it keeps its day but shows no time and no longer sits above everything that did happen.
  const updates = notes
    .map(datedUpdate)
    .filter(Boolean)
    .map((update) => (update.day === day && update.key > stamp ? { ...update, time: "", key: `${update.day}T00:00` } : update))
    .sort((a, b) => (a.key < b.key ? 1 : a.key > b.key ? -1 : 0));
  const month = day.slice(0, 7);
  const data = {
    stamp,
    now,
    day,
    sessions,
    running,
    errors: running.live.filter((entry) => entry.key === "error"),
    needs: open.filter((note) => note.tags.includes(NEEDS_TAG)).sort(newest),
    openTasks: open.length,
    doing: tasks.filter((note) => note.status === "doing").sort(byRecent),
    done: tasks.filter((note) => note.status === "done").length,
    proposed: notes.filter((note) => note.type === "decision" && note.status === "proposed").sort(byRecent),
    ideasInPlay: notes.filter((note) => note.type === "idea" && ["raw", "exploring", "ready"].includes(note.status)),
    fresh: notes.filter((note) => note.type === "idea" && note.status === "raw").sort(byRecent),
    github: githubAccounts(notes).map((entry) => entry.months[0]).filter((reading) => reading && reading.month === month && !reading.final && reading.pct >= GITHUB_WARN_PCT),
    perDay,
    journal,
    updates,
    shelf: pictureShelf(notes),
    devices: deviceList(notes).filter((device) => device.name),
    machines: new Map(notes.map(machineFromNote).filter(Boolean).map((reading) => [reading.name.trim(), reading])),
  };
  memo = { notes, stamp, data };
  return data;
}

const sumDays = (perDay, from, to, field) => {
  let total = 0;
  for (const [key, info] of perDay) if (key >= from && key <= to) total += info[field] || 0;
  return total;
};

// The sentence under the greeting, or "" for a brain no agent has worked in.
export function homeLede() {
  const d = homeData();
  if (!d.sessions.length) return "";
  const working = d.running.live.length;
  const computers = new Set(d.running.live.map((entry) => String(entry.note.meta.machine || "").trim()).filter(Boolean)).size;
  const waiting = d.needs.length + d.errors.length;
  const shipped = d.perDay.get(d.day)?.shipped || 0;
  const parts = [
    working ? `<strong>${plural(working, "session")}</strong> working${computers > 1 ? ` on ${computers} computers` : ""}` : "Nothing is running",
    waiting ? `<strong>${plural(waiting, "thing")}</strong> waiting for you` : "nothing is waiting for you",
    `${shipped ? `<strong>${shipped}</strong>` : "none"} shipped today`,
  ];
  return `${parts.join(" · ")}.`;
}

// ------------------------------------------------------------ rows

function homeRow({ path = "", go = "", tone = "", title, meta = "", badge = "" }) {
  const target = path ? `data-open="${esc(path)}"` : go ? `data-go="${esc(go)}"` : "";
  return `<li><button type="button" class="home-row" ${target}>
    <span class="hr-dot${tone ? ` ${tone}` : ""}" aria-hidden="true"></span>
    <span class="hr-main"><span class="hr-title">${badge ? `<span class="hr-badge${tone ? ` ${tone}` : ""}">${esc(badge)}</span> ` : ""}${esc(title)}</span>${meta ? `<span class="hr-meta">${esc(meta)}</span>` : ""}</span>
  </button></li>`;
}

const moreLine = (html) => `<p class="recap-more">${html}</p>`;

// ------------------------------------------------------------ blocks

function needsBlock(d) {
  const rows = [];
  for (const entry of d.errors) {
    rows.push(homeRow({ path: entry.note.path, tone: "bad", badge: "Stopped", title: entry.note.title, meta: `${entry.label}${entry.idle ? ` · ${ago(entry.idle)}` : ""}` }));
  }
  for (const reading of d.github) {
    const over = reading.pct >= 100;
    rows.push(homeRow({
      go: "vibe",
      tone: reading.pct >= 90 ? "bad" : "warn",
      badge: "GitHub minutes",
      title: `${reading.used.toLocaleString()} of ${reading.limit.toLocaleString()} used (${reading.pct}%)${reading.account ? ` on ${reading.account}` : ""}`,
      meta: over ? "Over the limit: workflows stop until the month turns." : `${(reading.limit - reading.used).toLocaleString()} left${reading.resets ? ` until ${prettyDay(reading.resets, "short")}` : ""}`,
    }));
  }
  for (const note of d.needs.slice(0, NEEDS_SHOWN)) {
    rows.push(homeRow({ path: note.path, tone: "task", title: note.title, meta: `${note.status === "doing" ? "In progress · " : ""}${note.meta.created ? `asked ${prettyDay(String(note.meta.created).slice(0, 10), "short")}` : relativeTime(note.mtime)}` }));
  }
  const few = d.proposed.length <= 3;
  if (few) for (const note of d.proposed) rows.push(homeRow({ path: note.path, tone: "decision", badge: "Decide", title: note.title, meta: relativeTime(note.mtime) }));
  else rows.push(homeRow({ go: "decisions", tone: "decision", badge: "Decide", title: `${d.proposed.length} decisions wait for a yes or no`, meta: `newest: ${d.proposed[0].title}` }));
  if (!rows.length) return null;
  const count = d.errors.length + d.github.length + d.needs.length + d.proposed.length;
  const rest = d.needs.length - NEEDS_SHOWN;
  const parts = [
    d.errors.length ? `${d.errors.length} stopped` : "",
    d.needs.length ? plural(d.needs.length, "task") : "",
    d.proposed.length ? plural(d.proposed.length, "decision") : "",
    d.github.length ? "GitHub minutes low" : "",
  ].filter(Boolean);
  return {
    html: `<ul class="home-list">${rows.join("")}</ul>${
      rest > 0 ? moreLine(`<button class="text-link" data-action="home-needs-all">All ${d.needs.length} tasks that need you ${icon("arrow", "sm")}</button>`) : ""
    }`,
    summary: parts.join(" · ") || plural(count, "thing"),
  };
}

function nowBlock(d) {
  if (!d.sessions.length) return null;
  const live = d.running.live;
  const rows = live.slice(0, LIVE_SHOWN).map((entry) => {
    const meta = [
      String(entry.note.meta.machine || "").trim(),
      agentGroup(entry.note.meta).label,
      entry.key === "working" ? `last step ${ago(entry.idle)}` : `${entry.label.toLowerCase()} ${ago(entry.idle)}`,
      entry.tokens ? `${compact(entry.tokens)} tokens` : "",
    ].filter(Boolean);
    return homeRow({ path: entry.note.path, tone: entry.key === "error" ? "bad" : entry.key === "working" ? "live" : "idle", title: entry.note.title, meta: meta.join(" · ") });
  });
  const last = d.running.finished[0];
  const idle = last
    ? `<p class="muted small home-idle">Nothing is running. Last finished: <button class="link-button" data-open="${esc(last.note.path)}">${esc(last.note.title)}</button> · ${esc(last.label.toLowerCase())}${last.at !== null && d.now !== null ? `, ${ago(Math.max(0, d.now - last.at))}` : ""}</p>`
    : `<p class="muted small home-idle">Nothing is running, and nothing finished in the last day.</p>`;
  const more = live.length > LIVE_SHOWN ? `${live.length - LIVE_SHOWN} more · ` : "";
  return {
    html: `${rows.length ? `<ul class="home-list">${rows.join("")}</ul>` : idle}
      ${moreLine(`<button class="text-link" data-go="vibe">${more}Every session, live, in Vibe ${icon("arrow", "sm")}</button>`)}`,
    summary: live.length ? `${plural(live.length, "session")}${d.running.tokens ? ` · ${compact(d.running.tokens)} tokens` : ""}` : "nothing running",
  };
}

function dashboardBlock(d) {
  if (!d.sessions.length) {
    const todos = state.todos === null ? "–" : state.todos.length;
    return {
      html: dashboardHtml([
        { value: d.doing.length, label: "in progress", go: "board" },
        { value: d.ideasInPlay.length, label: "ideas in play", go: "ideas" },
        { value: d.proposed.length, label: "open decisions", go: "decisions" },
        { value: todos, label: "code TODOs", go: "code" },
      ]),
      summary: `${d.doing.length} in progress · ${plural(d.ideasInPlay.length, "idea")} in play · ${plural(d.proposed.length, "open decision")}`,
    };
  }
  const todayInfo = d.perDay.get(d.day) || { sessions: 0, tokens: 0, shipped: 0 };
  const yesterday = d.perDay.get(addDays(d.day, -1)) || { sessions: 0, tokens: 0, shipped: 0 };
  const weekFrom = addDays(d.day, -6);
  const week = (field) => sumDays(d.perDay, weekFrom, d.day, field);
  const waiting = d.needs.length + d.errors.length;
  return {
    html: dashboardHtml([
      { value: d.running.live.length, label: "working now", sub: d.running.tokens ? `${compact(d.running.tokens)} tokens so far` : "", go: "vibe" },
      { value: todayInfo.shipped, label: "shipped today", sub: `${week("shipped")} in 7 days`, go: "vibe" },
      { value: todayInfo.sessions, label: "sessions today", sub: `${yesterday.sessions} yesterday`, go: "vibe" },
      { value: compact(todayInfo.tokens), label: "tokens today", sub: `${compact(week("tokens"))} in 7 days`, go: "vibe" },
      { value: waiting, label: "waiting for you", sub: plural(d.proposed.length, "open decision"), go: waiting ? "board" : "decisions" },
      { value: d.openTasks, label: "open tasks", sub: `${d.done} finished`, go: "board" },
    ]),
    summary: `${d.running.live.length} working · ${todayInfo.shipped} shipped today · ${waiting} waiting for you`,
  };
}

// ---- the activity chart

function chartPrefs(d) {
  if (!state.homeChart) {
    let kept = {};
    try {
      kept = JSON.parse(localStorage.getItem(CHART_KEY)) || {};
    } catch {
      /* the default view */
    }
    state.homeChart = { range: RANGES.includes(kept.range) ? kept.range : 30, metric: kept.metric, day: null };
  }
  const prefs = state.homeChart;
  const has = chartMetrics(d);
  if (!has.includes(prefs.metric)) prefs.metric = has[0];
  const from = addDays(d.day, -(prefs.range - 1));
  if (!prefs.day || prefs.day > d.day || prefs.day < from) prefs.day = d.day;
  return prefs;
}

// The series this brain has anything in: a brain without sessions only charts its journal.
function chartMetrics(d) {
  const any = (field) => [...d.perDay.values()].some((info) => info[field]);
  const out = [];
  if (d.perDay.size) out.push("sessions");
  if (any("shipped")) out.push("shipped");
  if (any("tokens")) out.push("tokens");
  if ([...d.journal.values()].some((info) => info.entries.length)) out.push("entries");
  return out;
}

function dayNumbers(d, day) {
  const info = d.perDay.get(day) || { sessions: 0, tokens: 0, shipped: 0 };
  return { day, sessions: info.sessions, shipped: info.shipped, tokens: info.tokens, entries: d.journal.get(day)?.entries.length || 0 };
}

const countText = (value, metric) => `${metric === "tokens" ? compact(value) : value.toLocaleString()} ${METRICS[metric].unit[value === 1 ? 0 : 1]}`;

function chartBlock(d) {
  const metrics = chartMetrics(d);
  if (!metrics.length) return null;
  const prefs = chartPrefs(d);
  const { range, metric } = prefs;
  const days = Array.from({ length: range }, (_, index) => dayNumbers(d, addDays(d.day, index - range + 1)));
  const top = Math.max(1, ...days.map((row) => row[metric]));
  const total = days.reduce((sum, row) => sum + row[metric], 0);
  const active = days.filter((row) => row[metric]).length;
  const bars = days
    .map((row) => {
      const value = row[metric];
      const height = value ? Math.max(3, Math.round((value / top) * 100)) : 0;
      const part = metric === "sessions" && value ? Math.round((row.shipped / value) * 100) : 0;
      const weekend = [0, 6].includes(new Date(`${row.day}T12:00:00`).getDay());
      return `<button type="button" class="hc-bar${weekend ? " weekend" : ""}${row.day === d.day ? " is-today" : ""}" data-action="home-chart-day" data-day="${row.day}" aria-pressed="${row.day === prefs.day}" aria-label="${esc(prettyDay(row.day, "short"))}: ${esc(countText(value, metric))}"><i style="height:${height}%">${part ? `<b style="height:${part}%"></b>` : ""}</i></button>`;
    })
    .join("");
  const toggle = (action, attr, value, label, on) =>
    `<button type="button" class="hc-opt" data-action="${action}" data-${attr}="${value}" aria-pressed="${on}">${label}</button>`;
  const ticks = [days[0], days[Math.floor((range - 1) / 2)], days[range - 1]].map((row, index) => `<span>${index === 2 ? "Today" : esc(prettyDay(row.day, "short").replace(/^\w+, /, ""))}</span>`).join("");
  return {
    html: `<div class="home-chart" id="home-chart">
      <div class="hc-head">
        <div class="hc-opts" role="group" aria-label="What to chart">${metrics.map((key) => toggle("home-chart-metric", "metric", key, METRICS[key].label, key === metric)).join("")}</div>
        <div class="hc-opts" role="group" aria-label="How far back">${RANGES.map((value) => toggle("home-chart-range", "range", value, `${value}d`, value === range)).join("")}</div>
      </div>
      <p class="hc-total"><strong>${esc(countText(total, metric))}</strong> in ${range} days <span class="muted">· ${active} active ${active === 1 ? "day" : "days"} · most in a day ${esc(metric === "tokens" ? compact(top) : String(top))}</span></p>
      <div class="hc-scroll" style="--bars:${range}"${range > 14 ? " data-wide" : ""}>
        <div class="hc-plot" style="--bars:${range}" aria-label="${esc(METRICS[metric].label)} per day, last ${range} days">${bars}</div>
        <div class="hc-axis" aria-hidden="true">${ticks}</div>
      </div>
      ${metric === "sessions" ? `<p class="hc-key muted small"><i class="k-all"></i> sessions <i class="k-part"></i> of them shipped · tap or drag across the bars for a day</p>` : `<p class="hc-key muted small">Tap or drag across the bars for a day</p>`}
      <div class="home-day" id="home-day" aria-live="polite">${dayDetail(d, prefs.day)}</div>
    </div>`,
    summary: `${countText(total, metric)} in ${range} days`,
  };
}

function updateRow(update, d, { showDay = true } = {}) {
  const picture = pictureOf(update, d.shelf);
  const when = !showDay || update.day === d.day ? update.time || "today" : update.day === addDays(d.day, -1) ? "yesterday" : prettyDay(update.day, "short").replace(/^\w+, /, "");
  const detail = update.more ? `${update.more} more like it` : snippetOf(update.note);
  const thumb = picture
    ? `<button type="button" class="recap-thumb" data-open="${esc(update.note.path)}" ${picture.src ? "" : `data-embed="${esc(picture.from)}"`} aria-label="Picture for ${esc(update.title)}"><img ${picture.src ? `src="${esc(picture.src)}"` : `data-asset="${esc(picture.asset)}"`} alt=""></button>`
    : "";
  return `<li${thumb ? ' class="has-thumb"' : ""}><span class="t">${esc(when)}</span><span class="txt"><button class="link-button" data-open="${esc(update.note.path)}">${esc(update.title)}</button>${detail ? `<span class="recap-detail">${esc(detail)}</span>` : ""}</span>${thumb}</li>`;
}

function dayDetail(d, day) {
  const row = dayNumbers(d, day);
  const summary = daySummaries(d.journal, d.perDay, day, day)[0];
  const updates = foldRuns(d.updates.filter((update) => update.day === day));
  const shipped = d.sessions
    .filter((note) => note.status === "shipped" && String(note.meta.started || note.meta.created || "").slice(0, 10) === day)
    .sort((a, b) => String(b.meta.ended || b.meta.started || "").localeCompare(String(a.meta.ended || a.meta.started || "")));
  const numbers = [
    ["sessions", row.sessions],
    ["shipped", row.shipped],
    ["tokens", row.tokens],
    ["entries", row.entries],
  ].filter(([key, value]) => value || key === state.homeChart?.metric);
  const journalPage = d.journal.get(day);
  const topics = (summary?.topics || []).slice(0, 4).map((topic) => topic.topic);
  const quiet = !row.sessions && !row.entries && !updates.length;
  return `<div class="hd-head"><strong>${esc(prettyDay(day))}</strong>${day === d.day ? ` <span class="muted">today</span>` : ""}</div>
    ${quiet ? `<p class="muted small">Nothing recorded on this day.</p>` : `<p class="hd-nums">${numbers.map(([key, value]) => `<span><b>${key === "tokens" ? compact(value) : value}</b> ${METRICS[key].unit[value === 1 ? 0 : 1]}</span>`).join("")}</p>`}
    ${summary?.mood ? `<p class="muted small hd-mood">Mood ${esc(summary.mood.label)}${summary.mood.auto ? " (read from the day)" : ""}${topics.length ? ` · ${esc(topics.join(" "))}` : ""}</p>` : ""}
    ${updates.length ? `<ol class="page-recap hd-list">${updates.slice(0, 5).map((update) => updateRow(update, d, { showDay: false })).join("")}</ol>${updates.length > 5 ? `<p class="muted small">${updates.length - 5} more updates that day.</p>` : ""}` : ""}
    ${shipped.length ? `<p class="hd-sub">Shipped</p><ul class="home-list">${shipped.slice(0, 4).map((note) => homeRow({ path: note.path, tone: "live", title: note.title, meta: [String(note.meta.machine || "").trim(), agentGroup(note.meta).label].filter(Boolean).join(" · ") })).join("")}</ul>${shipped.length > 4 ? `<p class="muted small">${shipped.length - 4} more shipped that day.</p>` : ""}` : ""}
    ${journalPage ? `<p class="recap-more"><button class="text-link" data-open="${esc(journalPage.path)}">Open the day's journal page ${icon("arrow", "sm")}</button></p>` : ""}`;
}

// ---- latest updates

const noteLabel = (note) => `${TYPE_META[note.type].label}${note.status ? ` · ${STATUS_LABEL[note.status] || note.status}` : ""}`;

function recapBlock(d) {
  const all = `<p class="recap-more"><button class="text-link" data-go="notes">All notes ${icon("arrow", "sm")}</button></p>`;
  if (d.updates.length) {
    const rows = foldRuns(d.updates.slice(0, 400)).slice(0, UPDATES_SHOWN);
    return {
      label: "Latest updates",
      html: `<ol class="page-recap home-updates">${rows.map((update) => updateRow(update, d)).join("")}</ol>${all}`,
      summary: `newest: ${rows[0].title}`,
    };
  }
  // No dated notes: the notes you touched last. Sessions are left out, their place is Working now.
  const own = state.notes.filter((note) => note.type !== "session" && !machineFromNote(note) && !core.isHandoff(note));
  const recent = [...own].sort(byRecent);
  return {
    html: `${noteRecap(own, noteLabel, "Nothing yet. Capture a thought in the bar above.")}${recent.length ? all : ""}`,
    summary: recent.length ? `last touched: ${recent[0].title}, ${relativeTime(recent[0].mtime)}` : "nothing yet",
  };
}

// ---- the computers that write into this brain

function devicesBlock(d) {
  if (d.devices.length < 2) return null;
  const rows = d.devices.map((device) => {
    const reading = d.machines.get(device.name);
    const here = state.vibeHere && state.vibeHere === device.name;
    const seen = stampAgo(device.last, d.now);
    const load = reading ? `${reading.level} load${reading.cpu_pct !== null ? `, processor ${reading.cpu_pct}%` : ""}${reading.ram_pct !== null ? `, memory ${reading.ram_pct}%` : ""} (${stampAgo(reading.at, d.now) || reading.at})` : "";
    const meta = [device.live ? `${device.live} running now` : "nothing running", plural(device.sessions, "session"), seen ? `last heard ${seen}` : "", load].filter(Boolean);
    return `<li class="home-device${device.live ? " live" : ""}">
      <span class="hr-dot ${device.live ? "live" : "idle"}" aria-hidden="true"></span>
      <span class="hr-main"><span class="hr-title">${esc(device.name)}${here ? ` <span class="hr-badge">this computer</span>` : ""}</span><span class="hr-meta">${esc(meta.join(" · "))}</span></span>
    </li>`;
  });
  return {
    html: `<ul class="home-list">${rows.join("")}</ul>
      <p class="muted small home-devices-note">Each computer writes into this one brain and it travels through its git remote, so another computer's lines are as fresh as its last sync, a few minutes at most.</p>`,
    summary: `${d.devices.length} computers · ${d.devices.filter((device) => device.live).length} with sessions running`,
  };
}

/** The blocks of the Today page that are worked out here: { needs, now, dashboard, chart, recap, devices }. */
export function homeBlocks() {
  const d = homeData();
  return { needs: needsBlock(d), now: nowBlock(d), dashboard: dashboardBlock(d), chart: chartBlock(d), recap: recapBlock(d), devices: devicesBlock(d) };
}

// ------------------------------------------------------------ actions and wiring

function saveChart() {
  try {
    localStorage.setItem(CHART_KEY, JSON.stringify({ range: state.homeChart.range, metric: state.homeChart.metric }));
  } catch {
    /* storage is off: the choice lasts until the page closes */
  }
}

export function setChartRange(value) {
  const range = Number(value);
  if (!state.homeChart || !RANGES.includes(range)) return false;
  state.homeChart.range = range;
  saveChart();
  return true;
}

export function setChartMetric(value) {
  if (!state.homeChart || !METRICS[value]) return false;
  state.homeChart.metric = value;
  saveChart();
  return true;
}

// Picking a day redraws only the detail under the chart, so dragging across the bars stays smooth.
export function pickChartDay(day) {
  const detail = $("#home-day");
  if (!state.homeChart || !detail || !day || state.homeChart.day === day) return;
  state.homeChart.day = day;
  for (const bar of $$("#home-chart .hc-bar")) bar.setAttribute("aria-pressed", String(bar.dataset.day === day));
  detail.innerHTML = dayDetail(homeData(), day);
  hydrateAssets(detail, "");
}

/** Tasks that wait for you, as a search on the Notes page. */
export function showNeeds() {
  state.notesQuery = `tag:${NEEDS_TAG} type:task -status:done`;
  state.notesFilter = "all";
  state.notesTag = null;
}

export function wireHome() {
  const board = $('.vibe-board[data-page="home"]');
  if (!board) return;
  hydrateAssets(board, "");
  const plot = $(".hc-plot", board);
  if (!plot) return;
  // On a phone a bar is at least 24 px wide, so 30 or 90 days are wider than the screen: the chart is
  // slid sideways there, and it opens at today, its right end.
  const scroller = plot.closest(".hc-scroll");
  if (scroller) scroller.scrollLeft = scroller.scrollWidth;
  // Drag (or slide a finger) across the bars: the day under the pointer is the one shown.
  let dragging = false;
  const at = (event) => document.elementFromPoint(event.clientX, plot.getBoundingClientRect().bottom - 2)?.closest?.(".hc-bar");
  plot.addEventListener("pointerdown", (event) => {
    dragging = true;
    const bar = at(event);
    if (bar) pickChartDay(bar.dataset.day);
  });
  plot.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const bar = at(event);
    if (bar) pickChartDay(bar.dataset.day);
  });
  for (const name of ["pointerup", "pointercancel", "pointerleave"]) plot.addEventListener(name, () => (dragging = false));
}
