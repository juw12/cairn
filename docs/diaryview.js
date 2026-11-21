// The Diary page: how busy each day was (screen time, the tasks handed to the assistants, how hard the
// computer worked) for every computer that shares this brain, and the log behind each day.
// The figures come from docs/diary.js; nothing here is typed in by hand.

import * as core from "./core.js";
import { DIARY_DAYS, dayLabel, dayRows, diaryDevices } from "./diary.js";
import { escapeHtml as esc } from "./md.js";
import { $, emptyState, icon, pageHead, plural, today } from "./ui.js";
import { state } from "./state.js";
import { compact, minutesText } from "./recap.js";
import { thisComputer } from "./handoffs.js";
import { renderView } from "./app.js";

const DEVICE_KEY = "cairn.diaryDevice";
const LOG_ROWS = 12;
const DASH = "–";

function ui() {
  if (!state.diary) {
    let device = null;
    try {
      const kept = localStorage.getItem(DEVICE_KEY);
      device = kept === null ? null : JSON.parse(kept);
    } catch {
      /* storage is off or holds something else */
    }
    state.diary = { device: typeof device === "string" ? device : null, open: "", all: false };
  }
  return state.diary;
}

export function setDiaryDevice(device) {
  const view = ui();
  view.device = typeof device === "string" ? device : null;
  try {
    if (view.device === null) localStorage.removeItem(DEVICE_KEY);
    else localStorage.setItem(DEVICE_KEY, JSON.stringify(view.device));
  } catch {
    /* storage is off */
  }
  renderView();
  $(".diary-devices .chip.on")?.focus({ preventScroll: true });
}

export function toggleDiaryDay(day) {
  const view = ui();
  view.open = view.open === day ? "" : day;
  view.all = false;
  renderView();
  $(`[data-action="diary-day"][data-day="${day}"]`)?.focus({ preventScroll: true });
}

export function showAllDiaryTasks() {
  ui().all = true;
  renderView();
}

const time = (minutes) => (minutes === null || minutes === undefined ? DASH : minutesText(minutes));
const percent = (average, peak) =>
  average === null || average === undefined ? DASH : `${average}%${peak === null || peak === undefined ? "" : ` <span class="muted">(${peak}%)</span>`}`;
const stars = (count) =>
  `<span class="diary-stars" role="img" aria-label="${count} of 5 stars">${"★".repeat(count)}<span class="off">${"★".repeat(5 - count)}</span></span>`;
const tasksText = (entry) => (entry.tasks ? `${entry.finished} <span class="muted">of ${entry.tasks}</span>` : DASH);
const here = (name) => (name && name === thisComputer() ? ` <span class="vd-here">this computer</span>` : "");
const deviceName = (name) => (name ? esc(name) : "No computer named");

function deviceBar(devices, shown) {
  if (devices.length < 2) return "";
  const chip = (device, label, days) =>
    `<button class="chip${device === shown ? " on" : ""}" data-action="diary-device"${device === null ? "" : ` data-device="${esc(device)}"`} aria-pressed="${device === shown}"><span class="vd-name">${label}</span>${days === null ? "" : ` <b>${days}</b>`}</button>`;
  return `<div class="vibe-devices diary-devices" role="group" aria-label="Computer">
      ${chip(null, "All devices", null)}
      ${devices.map((device) => chip(device.name, `${deviceName(device.name)}${here(device.name)}`, device.days)).join("")}
    </div>
    <p class="muted small vibe-device-note">Every computer that shares this brain is found by itself; the number is its days with anything noted in the last ${DIARY_DAYS}. Another computer is as current as its last sync.</p>`;
}

function todayCards(row) {
  const card = (number, label) => `<div class="stat-card"><span class="stat-num">${number}</span><span class="stat-label">${label}</span></div>`;
  const cards = [
    card(row.rest ? DASH : row.score.toFixed(1), row.rest ? "Nothing noted yet today" : `Score today ${stars(row.stars)}`),
    card(time(row.screen), "Screen time"),
    card(row.tasks ? String(row.finished) : DASH, row.tasks ? `Tasks finished of ${row.tasks} asked${row.live ? `, ${row.live} running` : ""}` : "Tasks asked"),
    card(row.tasks ? time(row.agent) : DASH, "Assistants worked"),
    card(row.cpu === null ? DASH : `${row.cpu}%`, row.cpuPeak === null ? "Processor" : `Processor, peak ${row.cpuPeak}%`),
    card(row.ram === null ? DASH : `${row.ram}%`, row.ramPeak === null ? "Memory" : `Memory, peak ${row.ramPeak}%`),
  ];
  return `<div class="memory-stats diary-stats" aria-label="Today so far">${cards.join("")}</div>`;
}

function hourStrip(hours) {
  if (!hours.some(Boolean)) return "";
  const bars = hours.map((minutes, hour) => {
    const level = Math.min(60, minutes);
    return `<span class="dh-bar" style="--h:${Math.round((level / 60) * 100)}%" title="${String(hour).padStart(2, "0")}:00, ${level} min at the screen"></span>`;
  });
  return `<div class="diary-hours" role="img" aria-label="Screen time hour by hour">${bars.join("")}</div>
    <div class="diary-hours-scale" aria-hidden="true"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>`;
}

function deviceBlock(entry) {
  const facts = [
    entry.rest ? "Rest day" : `Score ${entry.score.toFixed(1)}`,
    entry.screen === null ? "" : `${time(entry.screen)} at the screen`,
    entry.first && entry.last ? `${entry.first} to ${entry.last}` : "",
    entry.awake ? `on for ${time(entry.awake)}` : "",
    entry.tasks ? `${entry.finished} of ${plural(entry.tasks, "task")} finished` : "",
    entry.tasks ? `assistants worked ${time(entry.agent)}` : "",
    entry.cpu === null ? "" : `processor ${entry.cpu}%${entry.cpuPeak === null ? "" : `, peak ${entry.cpuPeak}%`}`,
    entry.ram === null ? "" : `memory ${entry.ram}%${entry.ramPeak === null ? "" : `, peak ${entry.ramPeak}%`}`,
    entry.heavy ? `${time(entry.heavy)} under heavy load` : "",
    entry.tokens ? `${compact(entry.tokens)} tokens` : "",
  ].filter(Boolean);
  return `<div class="diary-device">
      <h3>${deviceName(entry.name)}${here(entry.name)} ${entry.rest ? "" : stars(entry.stars)}</h3>
      <p class="muted small">${facts.join(" · ")}</p>
      ${hourStrip(entry.hours)}
    </div>`;
}

const startMinutes = (note) => core.stampMinutes(note.meta.started) ?? 0;

function taskLog(row, several, all) {
  const sessions = row.devices.flatMap((entry) => entry.sessions.map((note) => ({ note, device: entry.name })));
  if (!sessions.length) return `<p class="muted small">No task was handed to an assistant this day.</p>`;
  sessions.sort((a, b) => startMinutes(a.note) - startMinutes(b.note));
  const shown = all ? sessions : sessions.slice(-LOG_ROWS);
  const line = ({ note, device }) => {
    const clock = String(note.meta.started || "").slice(11, 16);
    const minutes = core.sessionMinutes(note);
    const meta = [note.meta.agent, several ? device : "", minutes ? minutesText(minutes) : "", note.status].filter(Boolean).map(esc).join(" · ");
    return `<button class="row-item" data-open="${esc(note.path)}"><span class="dot s-${esc(note.status)}"></span><span class="diary-clock">${esc(clock)}</span><span class="title">${esc(note.title)}</span><span class="meta">${meta}</span></button>`;
  };
  const more = sessions.length - shown.length;
  return `<div class="diary-log">
      <h3>Tasks handed to the assistants <span class="muted">${sessions.length}</span></h3>
      ${more > 0 ? `<button class="link-btn small" data-action="diary-all">Show the ${more} earlier ${more === 1 ? "one" : "ones"} too</button>` : ""}
      ${shown.map(line).join("")}
    </div>`;
}

function dayDetail(row, all) {
  const blocks = row.devices.length ? row.devices.map(deviceBlock).join("") : `<p class="muted small">Nothing was noted this day.</p>`;
  const report = row.note ? `<button class="btn sm" data-open="${esc(row.note)}">${icon("journal")} Open the day's full report</button>` : "";
  return `<tr class="diary-detail"><td colspan="8">
      <div class="diary-detail-body">
        ${blocks}
        ${taskLog(row, row.devices.length > 1, all)}
        ${report}
      </div>
    </td></tr>`;
}

function dayRow(row, open, now) {
  const label = `${dayLabel(row.day)}${row.day === now ? ` <span class="muted">today</span>` : ""}`;
  const button = `<button class="diary-day-btn" data-action="diary-day" data-day="${row.day}" aria-expanded="${open}">${icon("chevron", open ? "open" : "")}<span>${label}</span></button>`;
  if (row.rest) {
    return `<tr class="diary-row rest"><th scope="row">${button}</th><td colspan="3" class="muted">Rest day</td><td class="dc-agent muted">${DASH}</td><td class="dc-load">${percent(row.cpu, row.cpuPeak)}</td><td class="dc-load">${percent(row.ram, row.ramPeak)}</td><td class="dc-tokens muted">${DASH}</td></tr>`;
  }
  return `<tr class="diary-row${open ? " open" : ""}">
      <th scope="row">${button}</th>
      <td class="dc-score"><b>${row.score.toFixed(1)}</b> ${stars(row.stars)}</td>
      <td>${time(row.screen)}</td>
      <td>${tasksText(row)}</td>
      <td class="dc-agent">${row.tasks ? time(row.agent) : DASH}</td>
      <td class="dc-load">${percent(row.cpu, row.cpuPeak)}</td>
      <td class="dc-load">${percent(row.ram, row.ramPeak)}</td>
      <td class="dc-tokens">${row.tokens ? compact(row.tokens) : DASH}</td>
    </tr>`;
}

export function diaryView() {
  const view = ui();
  const now = today();
  const devices = diaryDevices(state.notes, { today: now });
  // A computer picked earlier that the brain no longer knows: back to all of them.
  if (view.device !== null && !devices.some((device) => core.deviceKey(device.name) === core.deviceKey(view.device))) view.device = null;
  const rows = dayRows(state.notes, { device: view.device, today: now });
  const head = pageHead(
    "Diary",
    "How busy each day was: time at the screen, the tasks handed to the assistants and how hard the computer worked. It fills itself in from the session watcher on each computer; open a day for its log.",
    "",
    "Every day, every computer",
  );
  if (rows.every((row) => row.rest && !row.devices.length && !row.note)) {
    return `${head}${emptyState("activity", "Nothing noted yet", "Once the session watcher has run on a computer for a few minutes, its screen time, load and the tasks handed to the assistants appear here day by day.")}`;
  }
  const worked = rows.filter((row) => !row.rest);
  const average = worked.length ? (worked.reduce((sum, row) => sum + row.score, 0) / worked.length).toFixed(1) : DASH;
  const body = rows.map((row) => dayRow(row, view.open === row.day, now) + (view.open === row.day ? dayDetail(row, view.all) : "")).join("");
  return `${head}
    ${deviceBar(devices, view.device)}
    ${todayCards(rows[0])}
    <p class="muted small diary-sum">Last ${rows.length} days: ${plural(worked.length, "day")} worked, average score ${average}. Score is screen time (35), tasks finished (40) and the time the assistants worked (25); a day with nothing in it is a rest day and is left out of the average.</p>
    <div class="diary-table-wrap">
      <table class="diary-table">
        <thead><tr><th scope="col">Day</th><th scope="col">Score</th><th scope="col">Screen</th><th scope="col">Tasks</th><th scope="col" class="dc-agent">Assistants</th><th scope="col" class="dc-load">Processor</th><th scope="col" class="dc-load">Memory</th><th scope="col" class="dc-tokens">Tokens</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}
