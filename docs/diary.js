// Diary: how busy each day was, on every computer that shares this brain. Pure functions over the notes, so
// the Diary page and the tests read the same answer.
//
// Three kinds of note are read, and none of them is a list of devices kept by hand:
//  - session notes: the tasks handed to the assistants, how they ended, how long they worked, on which computer;
//  - machines/<computer>-activity.md, which each computer's session watcher writes about itself: one line a
//    day with screen time, awake time, processor and memory;
//  - diary notes (tag `diary`, named by their day): a day's report written by a diary tool, whose `devices`
//    list can carry the same figures from a longer history.

import * as core from "./core.js";
import { deviceList } from "./progress.js";

export const WEIGHTS = { screen: 35, tasks: 40, agent: 25 };
export const TARGETS = { screenHours: 8, tasksFinished: 200, agentHours: 100 };
export const STAR_BOUNDS = [20, 40, 60, 80];
export const DIARY_DAYS = 30;
const FINISHED = ["shipped", "done"];
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const CLOCK_RE = /^\d{2}:\d{2}$/;
const NUMBERS = ["screen", "awake", "cpu", "cpu_peak", "ram", "ram_peak", "heavy"];

const oneSpaced = (text) => String(text ?? "").trim().split(/\s+/).join(" ");
const whole = (value) => (/^\d+$/.test(String(value ?? "").trim()) ? Number(String(value).trim()) : null);
const round1 = (value) => Math.round(value * 10) / 10;

/**
 * The day's score out of 100 and its stars: screen time counts up to 8 hours, and the tasks finished and the
 * time the assistants worked count by their square root, so the first tasks of a day weigh more than the 150th.
 * A day with any work in it has at least one star.
 */
export function busyScore(screenMinutes, finished, agentMinutes) {
  const share = (value) => Math.min(1, Math.max(0, value));
  const screen = round1(WEIGHTS.screen * share((screenMinutes || 0) / 60 / TARGETS.screenHours));
  const tasks = round1(WEIGHTS.tasks * share(Math.sqrt(Math.max(0, finished || 0) / TARGETS.tasksFinished)));
  const agent = round1(WEIGHTS.agent * share(Math.sqrt(Math.max(0, agentMinutes || 0) / 60 / TARGETS.agentHours)));
  const score = round1(Math.min(100, screen + tasks + agent));
  return { score, stars: 1 + STAR_BOUNDS.filter((bound) => score >= bound).length, screen, tasks, agent };
}

/** `screen 212 | cpu 23 | first 08:12 | hours 0 0 12 ...` as figures; what is not there stays undefined. */
export function parseFigures(parts) {
  const figures = {};
  for (const part of parts) {
    const text = part.trim();
    const cut = text.indexOf(" ");
    const key = cut === -1 ? text : text.slice(0, cut);
    const value = cut === -1 ? "" : text.slice(cut + 1).trim();
    if (key === "hours") figures.hours = value.split(/\s+/).slice(0, 24).map((item) => whole(item) || 0);
    else if (key === "first" || key === "last") {
      if (CLOCK_RE.test(value)) figures[key] = value;
    } else if (key === "source") figures.source = value;
    else if (NUMBERS.includes(key) && whole(value) !== null) figures[key] = whole(value);
  }
  return figures;
}

/** What each computer's watcher noted about itself: Map(device key -> { name, days: Map(day -> figures) }). */
export function activityDays(notes) {
  const found = new Map();
  for (const note of notes) {
    if (note.type === "session" || !note.tags?.includes("activity")) continue;
    const name = oneSpaced(note.meta?.machine);
    if (!name) continue;
    const key = core.deviceKey(name);
    if (!found.has(key)) found.set(key, { name, days: new Map() });
    for (const line of String(note.body || "").split("\n")) {
      const parts = line.trim().replace(/^- /, "").split("|");
      const day = parts[0].trim();
      if (line.trim().startsWith("- ") && DAY_RE.test(day)) found.get(key).days.set(day, parseFigures(parts.slice(1)));
    }
  }
  return found;
}

/** The day reports a diary tool wrote: Map(day -> { path, tokens, devices: [{ name, ...figures }] }). */
export function diaryNotes(notes) {
  const found = new Map();
  for (const note of notes) {
    if (note.type === "session" || !note.tags?.includes("diary") || !DAY_RE.test(note.slug)) continue;
    const lines = Array.isArray(note.meta.devices) ? note.meta.devices : [];
    const devices = [];
    for (const line of lines) {
      const parts = String(line).split("|");
      const name = oneSpaced(parts[0]);
      if (name) devices.push({ name, ...parseFigures(parts.slice(1)) });
    }
    found.set(note.slug, { path: note.path, tokens: whole(note.meta.ai_tokens) || 0, updated: String(note.meta.updated || ""), devices });
  }
  return found;
}

const sessionDay = (note) => String(note.meta.started || note.meta.created || note.slug || "").slice(0, 10);

/** The tasks of each day on each computer: Map(day -> Map(device key -> { name, tasks, finished, live, agent, tokens, sessions })). */
export function sessionDays(notes) {
  const found = new Map();
  for (const note of notes) {
    if (note.type !== "session") continue;
    const day = sessionDay(note);
    if (!DAY_RE.test(day)) continue;
    const name = oneSpaced(note.meta.machine);
    const key = core.deviceKey(name);
    if (!found.has(day)) found.set(day, new Map());
    const devices = found.get(day);
    if (!devices.has(key)) devices.set(key, { name, tasks: 0, finished: 0, live: 0, agent: 0, tokens: 0, sessions: [] });
    const entry = devices.get(key);
    entry.tasks += 1;
    entry.finished += FINISHED.includes(note.status) ? 1 : 0;
    entry.live += note.status === "live" ? 1 : 0;
    entry.agent += core.sessionMinutes(note);
    entry.tokens += (whole(note.meta.tokens_in) || 0) + (whole(note.meta.tokens_out) || 0) || whole(note.meta.tokens) || 0;
    entry.sessions.push(note);
  }
  return found;
}

const most = (...values) => {
  const known = values.filter((value) => typeof value === "number");
  return known.length ? Math.max(...known) : null;
};
const first = (...values) => values.find((value) => value !== undefined && value !== null) ?? null;

function scored(entry) {
  const rest = !(entry.screen || entry.tasks || entry.tokens);
  const { score, stars } = busyScore(entry.screen, entry.finished, entry.agent);
  return { ...entry, rest, score: rest ? null : score, stars: rest ? 0 : stars };
}

function dayBefore(day, back) {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date - back)).toISOString().slice(0, 10);
}

/**
 * One row a day, newest first, for the last `days` days up to `today`:
 * { day, rest, score, stars, screen, tasks, finished, live, agent, tokens, cpu, cpuPeak, ram, ramPeak, note, devices }.
 * `devices` holds the same figures for each computer that left any that day (with `hours`, `first`, `last`,
 * `awake`, `source` and its `sessions`). `device` picks one computer by name; null is all of them together.
 * Screen time is the most any recorder saw for that computer; minutes throughout; null where nothing was measured.
 */
export function dayRows(notes, { device = null, days = DIARY_DAYS, today } = {}) {
  const watched = activityDays(notes);
  const reports = diaryNotes(notes);
  const sessions = sessionDays(notes);
  const only = device === null ? null : core.deviceKey(device);
  const rows = [];
  for (let back = 0; back < days; back += 1) {
    const day = dayBefore(today, back);
    const report = reports.get(day);
    const names = new Map();
    for (const [key, entry] of sessions.get(day) || []) names.set(key, entry.name);
    for (const [key, entry] of watched) if (entry.days.has(day)) names.set(key, names.get(key) || entry.name);
    for (const entry of report?.devices || []) names.set(core.deviceKey(entry.name), names.get(core.deviceKey(entry.name)) || entry.name);
    const devices = [];
    for (const [key, name] of names) {
      if (only !== null && key !== only) continue;
      const work = sessions.get(day)?.get(key) || { tasks: 0, finished: 0, live: 0, agent: 0, tokens: 0, sessions: [] };
      const seen = watched.get(key)?.days.get(day) || {};
      const told = report?.devices.find((entry) => core.deviceKey(entry.name) === key) || {};
      devices.push(scored({
        name,
        screen: most(told.screen, seen.screen),
        awake: first(seen.awake, told.awake),
        cpu: first(told.cpu, seen.cpu),
        cpuPeak: first(told.cpu_peak, seen.cpu_peak),
        ram: first(told.ram, seen.ram),
        ramPeak: first(told.ram_peak, seen.ram_peak),
        heavy: first(seen.heavy, told.heavy),
        first: first(seen.first, told.first),
        last: first(seen.last, told.last),
        hours: seen.hours || told.hours || [],
        source: told.source || (seen.screen !== undefined ? "watcher" : ""),
        tasks: work.tasks,
        finished: work.finished,
        live: work.live,
        agent: work.agent,
        tokens: work.tokens,
        sessions: work.sessions,
      }));
    }
    devices.sort((a, b) => (b.score ?? -1) - (a.score ?? -1) || (a.name < b.name ? -1 : 1));
    const sum = (key) => devices.reduce((total, entry) => total + (entry[key] || 0), 0);
    const known = (key) => devices.map((entry) => entry[key]).filter((value) => typeof value === "number");
    const mean = (key) => (known(key).length ? Math.round(known(key).reduce((a, b) => a + b, 0) / known(key).length) : null);
    const total = scored({
      day,
      screen: known("screen").length ? Math.min(1440, sum("screen")) : null,
      tasks: sum("tasks"),
      finished: sum("finished"),
      live: sum("live"),
      agent: sum("agent"),
      tokens: only === null ? Math.max(sum("tokens"), report?.tokens || 0) : sum("tokens"),
      cpu: mean("cpu"),
      cpuPeak: most(...known("cpuPeak")),
      ram: mean("ram"),
      ramPeak: most(...known("ramPeak")),
      note: report?.path || "",
      devices,
    });
    rows.push(total);
  }
  return rows;
}

/**
 * Every computer the brain has heard of, the busiest in the last `days` days first:
 * [{ name, days }] where `days` is how many of those days it left anything. Found from the notes alone.
 */
export function diaryDevices(notes, { days = DIARY_DAYS, today } = {}) {
  const found = new Map();
  const add = (name) => {
    const key = core.deviceKey(name);
    if (key && !found.has(key)) found.set(key, { name: oneSpaced(name), days: 0 });
    return found.get(key);
  };
  for (const row of dayRows(notes, { days, today })) {
    for (const entry of row.devices) {
      const item = add(entry.name);
      if (item && !entry.rest) item.days += 1;
    }
  }
  for (const entry of deviceList(notes)) add(entry.name);
  for (const entry of activityDays(notes).values()) add(entry.name);
  return [...found.values()].sort((a, b) => b.days - a.days || (a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1));
}

/** "Sat 21 Nov" for a day, without leaning on the browser's language. */
export function dayLabel(day) {
  const [year, month, date] = day.split("-").map(Number);
  const when = new Date(Date.UTC(year, month - 1, date));
  const week = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][when.getUTCDay()];
  const name = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][month - 1];
  return `${week} ${date} ${name}`;
}
