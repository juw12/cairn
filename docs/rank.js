// Priority: the running sessions in the order their user gave them, the most important first.
// The order is one small file in the brain (CAIRN-PRIORITY.md: the sessions' names, one numbered line each),
// so the desktop app, the web app and the phone show the same list and any of them can change it. A running
// session that is not listed yet comes after the listed ones, in the order it started. The same rules are in
// cairn/rank.py, which also tells each assistant its place at its next step (cairn vibe log).

import * as core from "./core.js";
import { STALE_MIN, lastActivity } from "./recap.js";
import { fileByPath, readOnly, state, upsertFile } from "./state.js";

export const RANK_FILE = "CAIRN-PRIORITY.md";
const SAVE_DELAY_MS = 600;

const INTRO = `---
type: settings
---

# Cairn priority

The running sessions in the order they matter to you, the most important first. Arrange them in Cairn
(Vibe, Priority: the up and down buttons, Top, or dragging the handle) or with \`cairn vibe rank\`. Every
device shows the same order, and an assistant that logs its steps with \`cairn vibe\` is told its place at its
next step. A running session that is not listed yet comes after these, in the order it started. Editing
these lines by hand works too.
`;

// A session's name as the file may spell it: its slug, its path, or a [[link]] to it.
function nameOf(value) {
  let name = String(value || "").trim().replace(/^\[\[/, "").replace(/\]\]$/, "").split("|")[0].trim();
  name = name.replaceAll("\\", "/").split("/").at(-1);
  return name.toLowerCase().endsWith(".md") ? name.slice(0, -3) : name;
}

// The sessions the file lists, in its order. A name listed twice counts where it stands first.
export function parseRank(text) {
  const names = [];
  for (const match of String(text || "").replaceAll("\r\n", "\n").matchAll(/^[ \t]*\d+[.)][ \t]+(.+?)[ \t]*$/gm)) {
    const name = nameOf(match[1]);
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
}

// The whole file for this order.
export function rankText(names) {
  const lines = names.map((name, index) => `${index + 1}. ${name}`).join("\n");
  return lines ? `${INTRO}\n${lines}\n` : INTRO;
}

// Live, and not left open: a session with no step for a day is not one to put in order. `now` is on the
// core.stampMinutes clock.
export function isRunning(note, now) {
  if (note.type !== "session" || note.status !== "live") return false;
  const last = lastActivity(note);
  return last === null || now === null || now - last < STALE_MIN;
}

// `notes` in the user's order: the listed ones as listed, then the others as they started.
export function arrange(notes, names) {
  const place = new Map(names.map((name, index) => [name, index]));
  const key = (note) => [place.get(note.slug) ?? names.length, String(note.meta.started || ""), note.path];
  return [...notes].sort((a, b) => {
    const [pa, sa, fa] = key(a);
    const [pb, sb, fb] = key(b);
    return pa - pb || (sa < sb ? -1 : sa > sb ? 1 : 0) || (fa < fb ? -1 : fa > fb ? 1 : 0);
  });
}

// ------------------------------------------------------------ the brain's copy

// The order just chosen on this device, until the brain file has it.
let pending = null;
let saveTimer = null;
let saving = false;

export const canRank = () => !readOnly() && typeof state.store?.write === "function" && state.store.writable !== false;

// A choice of this device that the brain does not hold yet: the live page leaves the file alone meanwhile.
export const rankBusy = () => pending !== null || saving;

export const rankNames = () => pending || parseRank(fileByPath(RANK_FILE)?.text);

// The running sessions of every computer, in order: [{ note, place, listed }]. `ranked` is false until one of
// them was placed; before that the order is only the order they started in.
export function ranking(notes, nowStamp) {
  const now = core.stampMinutes(nowStamp);
  const names = rankNames();
  const rows = arrange(notes.filter((note) => isRunning(note, now)), names)
    .map((note, index) => ({ note, place: index + 1, listed: names.includes(note.slug) }));
  return { rows, ranked: rows.some((row) => row.listed) };
}

async function writeRank(text) {
  const existing = fileByPath(RANK_FILE);
  let saved;
  try {
    saved = await state.store.write(RANK_FILE, text, existing ? existing.mtime : null);
  } catch (error) {
    // Another device (or a terminal) saved in between: the newest choice wins.
    if (typeof error?.mtime !== "number") throw error;
    saved = await state.store.write(RANK_FILE, text, error.mtime);
  }
  upsertFile(RANK_FILE, text, saved?.mtime ?? Date.now());
}

// Keep `names` as the order. Written to the brain once the clicking stops, so a few quick moves make one change.
// `failed(error)` is called when the brain could not take it; the page then shows the brain's order again.
export function saveRank(names, failed = () => {}) {
  pending = [...names];
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    const written = pending;
    saving = true;
    try {
      await writeRank(rankText(written));
      if (pending === written) pending = null;
    } catch (error) {
      if (pending === written) pending = null;
      failed(error);
    } finally {
      saving = false;
    }
  }, window.CAIRN_TEST_SAVE_MS ?? SAVE_DELAY_MS);
}

// Where the session at index `at` of `count` goes for "top", "up", "down" or "bottom".
export function rankTarget(to, at, count) {
  if (to === "top") return 0;
  if (to === "bottom") return count - 1;
  if (to === "up") return Math.max(0, at - 1);
  if (to === "down") return Math.min(count - 1, at + 1);
  return at;
}
