// Recap: what every running session is doing right now, and how the ones that just ended finished.
// Pure functions over session notes, so the Vibe view, the share card and the tests read the same answer.

import * as core from "./core.js";

// A live session is running: the watcher ends one the moment its turn finishes, and one that stops logging
// after a few hours. Past this many minutes without a step the row says when the last step was.
export const QUIET_MIN = 20;
// No step for a day and still marked live: only a brain no watcher looks after gets here. Folded away.
export const STALE_MIN = 1440;
const FINISHED_WINDOW_MIN = 1440;
const FINISHED_ROWS = 10;
const ORDER = { error: 0, working: 1, finished: 2, left: 3 };
const RESULT = { shipped: "Shipped", partial: "Partial", dropped: "Dropped", done: "Done" };

const number = (value) => (Number.isFinite(Number(value)) && Number(value) > 0 ? Math.floor(Number(value)) : 0);
const word = (value) => (typeof value === "string" ? value.trim().toLowerCase() : "");

// 950, 12.3k, 685M, 4.5B: three figures at most, so a token count fits a phone row.
export function compact(value) {
  let rest = number(value);
  if (rest < 1000) return String(rest);
  const units = ["k", "M", "B", "T"];
  let unit = -1;
  while (rest >= 1000 && unit < units.length - 1) {
    rest /= 1000;
    unit += 1;
  }
  let text = rest < 100 ? rest.toFixed(1) : String(Math.round(rest));
  if (Number(text) >= 1000 && unit < units.length - 1) {
    text = "1.0";
    unit += 1;
  }
  return `${text.replace(/\.0$/, "")}${units[unit]}`;
}

export function minutesText(minutes) {
  const total = Math.max(0, Math.round(minutes));
  if (total < 60) return `${total}m`;
  if (total < 1440) return `${Math.floor(total / 60)}h ${String(total % 60).padStart(2, "0")}m`;
  return `${Math.floor(total / 1440)}d ${Math.floor((total % 1440) / 60)}h`;
}

// When a session last did anything, in core.stampMinutes units. A watcher stamps `turn_at`; otherwise
// walk the log from the start: its lines only carry HH:MM, so a time that goes backwards is the next day.
export function lastActivity(note) {
  const stamped = core.stampMinutes(note.meta.turn_at);
  if (stamped !== null) return stamped;
  const start = core.stampMinutes(note.meta.started);
  if (start === null) return null;
  let day = start - (start % 1440);
  let previous = start % 1440;
  let last = start;
  for (const entry of core.sessionLog(note.body)) {
    const match = /^(\d{1,2}):(\d{2})$/.exec(entry.time);
    if (!match) continue;
    const minute = Number(match[1]) * 60 + Number(match[2]);
    if (minute < previous) day += 1440;
    previous = minute;
    last = day + minute;
  }
  return last;
}

function lastReply(body) {
  const match = /^## Last reply[ \t]*\n+((?:>.*(?:\n|$))+)/m.exec(String(body || ""));
  if (!match) return "";
  return match[1]
    .split("\n")
    .map((line) => line.replace(/^> ?/, ""))
    .join("\n")
    .trim();
}

// The words to show under a session: the model's last reply when the watcher was allowed to keep it,
// else the line that ended it, else its newest step.
export function finalMessage(note) {
  const reply = lastReply(note.body);
  if (reply) return { kind: "reply", time: "", text: reply };
  const log = core.sessionLog(note.body);
  const end = [...log].reverse().find((entry) => entry.kind === "end");
  if (end && note.status !== "live") return { kind: "end", time: end.time, text: end.text };
  const last = log.at(-1);
  return last ? { kind: last.kind, time: last.time, text: last.text } : null;
}

// For a live session one of: working, finished (its turn is over: a batch between two runs, or a session the
// watcher is about to end), error (turn failed), left (no step for a day). For one that ended: done, cut
// (stopped mid-turn) or error. Nothing here waits for a person: sessions run on their own.
export function sessionState(note, now) {
  const turn = word(note.meta.turn);
  const active = lastActivity(note);
  const idle = active === null || now === null ? 0 : Math.max(0, now - active);
  if (note.status === "live") {
    if (turn === "failed") return { key: "error", label: "Stopped on an error", idle };
    if (idle >= STALE_MIN) return { key: "left", label: `No step for ${minutesText(idle)}`, idle };
    if (turn === "done") return { key: "finished", label: number(note.meta.runs) ? "Between runs" : "Turn finished", idle };
    return { key: "working", label: "Working", idle };
  }
  if (turn === "working") return { key: "cut", label: "Stopped mid-turn", idle };
  if (turn === "failed") return { key: "error", label: "Ended on an error", idle };
  return { key: "done", label: RESULT[note.status] || "Ended", idle };
}

function row(note, now) {
  const state = sessionState(note, now);
  return {
    note,
    ...state,
    model: typeof note.meta.model === "string" ? note.meta.model.trim() : "",
    effort: typeof note.meta.effort === "string" ? note.meta.effort.trim() : "",
    tokens: number(note.meta.tokens),
    prompts: number(note.meta.prompts) || core.sessionLog(note.body).filter((entry) => entry.kind === "prompt").length,
    runs: number(note.meta.runs),
    message: finalMessage(note),
  };
}

const newestFirst = (a, b) => (b.at ?? 0) - (a.at ?? 0) || (a.note.path < b.note.path ? -1 : 1);

// Everything the recap band shows. `nowStamp` is YYYY-MM-DDTHH:MM.
export function recap(notes, nowStamp) {
  const now = core.stampMinutes(nowStamp);
  const sessions = notes.filter((note) => note.type === "session");
  const running = sessions
    .filter((note) => note.status === "live")
    .map((note) => ({ ...row(note, now), at: lastActivity(note) }))
    .sort((a, b) => ORDER[a.key] - ORDER[b.key] || newestFirst(a, b));
  const stale = running.filter((entry) => entry.key === "left");
  const live = running.filter((entry) => entry.key !== "left");
  const finished = sessions
    .filter((note) => note.status !== "live")
    .map((note) => ({ ...row(note, now), at: core.stampMinutes(note.meta.ended) ?? lastActivity(note) }))
    .filter((entry) => entry.at !== null && now !== null && now - entry.at >= 0 && now - entry.at <= FINISHED_WINDOW_MIN)
    .sort(newestFirst);
  const counts = { working: 0, finished: 0, error: 0, left: 0 };
  for (const entry of running) counts[entry.key] += 1;
  return {
    live,
    stale,
    finished: finished.slice(0, FINISHED_ROWS),
    finishedTotal: finished.length,
    counts,
    tokens: running.reduce((total, entry) => total + entry.tokens, 0),
  };
}
