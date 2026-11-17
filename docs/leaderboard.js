// Friends and a leaderboard without a server: everyone who wants to be ranked publishes one small note
// of totals (vibe-stats.md) in their public brain, and this page reads those notes straight from GitHub.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import * as gh from "./github.js";
import * as auth from "./auth.js";
import { ask, icon, toast, today } from "./ui.js";
import { fileByPath, readOnly, scheduleGitRefresh, state, upsertFile } from "./state.js";
import { compact } from "./recap.js";
import { hoursText } from "./share.js";
import { renderView } from "./app.js";

export const STATS_PATH = "vibe-stats.md";
export const FRIENDS_KEY = "cairn.friends";
// Where a brain's notes live inside its repo, most likely first.
const ROOTS = ["brain/", ".brain/", ""];

const days = (value) => `${value}d`;

// One entry per ranking. `key` is the frontmatter field in the stats note, `field` the core.vibeStats one.
export const STATS = {
  tokens: { label: "Tokens", key: "vibe_tokens", field: "tokens", text: compact },
  hours: { label: "Hours", key: "vibe_minutes", field: "minutes", text: hoursText },
  sessions: { label: "Sessions", key: "vibe_sessions", field: "sessions", text: compact },
  prompts: { label: "Prompts", key: "vibe_prompts", field: "prompts", text: compact },
  streak: { label: "Streak", key: "vibe_streak", field: "streak", text: days },
  active: { label: "Active days", key: "vibe_active_days", field: "active_days", text: days },
};

// ------------------------------------------------------------ the stats note (pure)

// Totals only: no titles, folders, models or message text ever go into this note.
export function totalsOf(stats) {
  const totals = {};
  for (const [id, stat] of Object.entries(STATS)) totals[id] = Math.max(0, Math.round(Number(stats[stat.field]) || 0));
  return totals;
}

export function statsNote(stats, day) {
  const totals = totalsOf(stats);
  const meta = { title: "Vibe stats", type: "note", publish: "true", updated: day };
  for (const [id, stat] of Object.entries(STATS)) meta[stat.key] = totals[id];
  const lines = Object.entries(STATS).map(([id, stat]) => `- ${stat.label}: ${stat.text(totals[id])}`);
  const body = `My all-time totals from Cairn Vibe, published for friends' leaderboards. Self-reported.\n\n${lines.join("\n")}\n`;
  return core.serializeNote(meta, body);
}

// A friend's note is text from the internet: accept only whole, non-negative numbers, or nothing at all.
export function readStatsNote(text) {
  if (typeof text !== "string" || !text) return null;
  const { meta } = core.parseNote(text);
  const totals = {};
  for (const [id, stat] of Object.entries(STATS)) {
    const raw = meta[stat.key];
    if (typeof raw !== "string" || !/^\d{1,15}$/.test(raw)) return null;
    totals[id] = Number(raw);
  }
  totals.updated = /^\d{4}-\d{2}-\d{2}$/.test(meta.updated || "") ? meta.updated : "";
  return totals;
}

// Highest first; equal values share a place (1, 2, 2, 4).
export function rank(rows, statId) {
  const sorted = [...rows].sort((a, b) => b.totals[statId] - a.totals[statId] || a.login.localeCompare(b.login));
  let place = 0;
  return sorted.map((row, index) => {
    if (index === 0 || row.totals[statId] !== sorted[index - 1].totals[statId]) place = index + 1;
    return { ...row, place };
  });
}

// ------------------------------------------------------------ friends (kept on this device)

export function friends() {
  try {
    const list = JSON.parse(localStorage.getItem(FRIENDS_KEY) || "[]");
    return Array.isArray(list) ? list.filter((f) => f && f.owner && f.name && f.fullName) : [];
  } catch {
    return [];
  }
}

function saveFriends(list) {
  localStorage.setItem(FRIENDS_KEY, JSON.stringify(list));
}

const sameRepo = (a, b) => a.toLowerCase() === b.toLowerCase();

export async function addFriend(input) {
  const parsed = gh.parseGithubInput(input);
  if (!parsed) return toast("Type a GitHub username, or owner/repo for a specific brain.", { error: true });
  let repo;
  try {
    repo = parsed.repo ? await gh.fetchRepo(parsed.owner, parsed.repo) : (await gh.findBrains(parsed.owner))[0];
  } catch (error) {
    return toast(error.message || "Couldn't reach GitHub.", { error: true });
  }
  if (!repo) return toast(`@${parsed.owner} hasn't published a brain yet. Try owner/repo if you know it.`, { error: true });
  const list = friends();
  if (list.some((f) => sameRepo(f.fullName, repo.fullName))) return toast(`@${repo.owner} is already a friend.`);
  list.push({ owner: repo.owner, name: repo.name, fullName: repo.fullName, branch: repo.branch, avatar: repo.avatar || "" });
  saveFriends(list);
  toast(`Added @${repo.owner}.`);
  await loadLeaderboard(true);
}

export function removeFriend(fullName) {
  saveFriends(friends().filter((f) => !sameRepo(f.fullName, fullName)));
  if (state.leaderboard?.totals) delete state.leaderboard.totals[fullName];
  renderView();
}

// { totals, root }: the first folder that holds a stats note decides, and is remembered so that the
// next refresh asks for one file instead of probing every folder again.
async function friendTotals(friend) {
  const roots = typeof friend.root === "string" ? [friend.root] : ROOTS;
  for (const root of roots) {
    const text = await gh.rawText(friend.owner, friend.name, friend.branch, root + STATS_PATH).catch(() => null);
    if (text !== null) return { totals: readStatsNote(text), root };
  }
  return { totals: null, root: friend.root };
}

export async function loadLeaderboard(force = false) {
  const board = state.leaderboard;
  if (board && !force) return;
  state.leaderboard = { loading: true, stat: board?.stat || "tokens", totals: board?.totals || {} };
  const list = friends();
  const results = await Promise.all(list.map(friendTotals));
  const totals = {};
  const learned = {};
  list.forEach((friend, index) => {
    totals[friend.fullName] = results[index].totals;
    if (results[index].root !== friend.root) learned[friend.fullName] = results[index].root;
  });
  // Re-read the list: a friend may have been added or removed while GitHub was answering.
  if (Object.keys(learned).length) saveFriends(friends().map((f) => (f.fullName in learned ? { ...f, root: learned[f.fullName] } : f)));
  state.leaderboard = { loading: false, stat: state.leaderboard.stat, totals };
  if (state.view === "people") renderView();
}

export function setLeaderboardStat(statId) {
  if (!STATS[statId] || !state.leaderboard) return;
  state.leaderboard.stat = statId;
  renderView();
}

// ------------------------------------------------------------ publishing your own totals (opt-in)

function publishedTotals() {
  const file = fileByPath(STATS_PATH);
  return file ? readStatsNote(file.text) : null;
}

export async function publishStats() {
  if (readOnly()) return;
  const stats = core.vibeStats(state.notes, today());
  const totals = totalsOf(stats);
  const github = state.info.mode === "github";
  const ok = await ask({
    title: "Publish your totals?",
    text: github
      ? `This commits ${STATS_PATH} to your brain's repo now. If the repo is public, anyone can read these numbers.`
      : `This saves ${STATS_PATH} in your brain, marked publish: true. It becomes public the next time the brain is published or pushed to a public repo.`,
    list: [
      ...Object.entries(STATS).map(([id, stat]) => `${stat.label}: ${stat.text(totals[id])}`),
      "Not included: session titles, folders, model names, messages.",
    ],
    confirm: "Publish totals",
    noInput: true,
  });
  if (!ok) return;
  const text = statsNote(stats, today());
  try {
    const { mtime } = await state.store.write(STATS_PATH, text, fileByPath(STATS_PATH)?.mtime ?? null);
    upsertFile(STATS_PATH, text, mtime);
  } catch (error) {
    return toast(error.message || "Couldn't save your totals.", { error: true });
  }
  scheduleGitRefresh();
  renderView();
  toast(github ? "Totals published." : `Saved brain/${STATS_PATH}. It goes public with your next publish.`);
}

export async function unpublishStats() {
  if (readOnly() || !fileByPath(STATS_PATH)) return;
  try {
    await state.store.remove(STATS_PATH);
  } catch (error) {
    return toast(error.message || "Couldn't remove your totals.", { error: true });
  }
  upsertFile(STATS_PATH, null);
  scheduleGitRefresh();
  renderView();
  toast(`Removed brain/${STATS_PATH}. A copy already published stays until your next publish.`);
}

// ------------------------------------------------------------ view

function avatarHtml(row) {
  return row.avatar
    ? `<img class="lb-avatar" src="${esc(row.avatar)}" alt="" width="32" height="32" loading="lazy">`
    : `<span class="lb-avatar lb-initial" aria-hidden="true">${esc(row.login.slice(0, 1).toUpperCase())}</span>`;
}

function rowHtml(row, statId, top) {
  const value = row.totals[statId];
  const others = Object.entries(STATS)
    .filter(([id]) => id !== statId)
    .map(([id, stat]) => `<span>${esc(stat.text(row.totals[id]))} ${esc(stat.label.toLowerCase())}</span>`)
    .join("");
  const note = row.me
    ? row.published
      ? `published ${esc(row.published)}`
      : "only on this device"
    : row.totals.updated
      ? `updated ${esc(row.totals.updated)}`
      : "";
  return `
    <li class="lb-row${row.me ? " me" : ""}" data-login="${esc(row.login)}">
      <span class="lb-place">${row.place}</span>
      ${avatarHtml(row)}
      <div class="lb-who">
        <strong>${row.me ? "You" : `@${esc(row.login)}`}</strong>${note ? `<span class="muted">${note}</span>` : ""}
        <div class="lb-others">${others}</div>
      </div>
      <span class="lb-bar" aria-hidden="true"><i style="width:${top ? Math.max(2, Math.round((value / top) * 100)) : 0}%"></i></span>
      <span class="lb-value">${esc(STATS[statId].text(value))}</span>
      ${row.me ? "" : `<button class="btn sm ghost icon-only lb-remove" data-action="friend-remove" data-repo="${esc(row.fullName)}" aria-label="Remove @${esc(row.login)}">${icon("x", "sm")}</button>`}
    </li>`;
}

export function leaderboardView() {
  loadLeaderboard();
  const board = state.leaderboard;
  const statId = board.stat;
  const list = friends();
  const rows = [];
  const waiting = [];
  for (const friend of list) {
    const totals = board.totals[friend.fullName];
    if (totals) rows.push({ login: friend.owner, fullName: friend.fullName, avatar: friend.avatar, totals });
    else waiting.push(friend);
  }
  const mine = !readOnly();
  const published = mine ? publishedTotals() : null;
  if (mine) {
    const me = auth.session();
    rows.push({
      me: true,
      login: me?.login || "you",
      avatar: me?.avatar || "",
      totals: totalsOf(core.vibeStats(state.notes, today())),
      published: published?.updated || (published ? "earlier" : ""),
    });
  }
  const ranked = rank(rows, statId);
  const top = ranked.length ? ranked[0].totals[statId] : 0;
  const tabs = Object.entries(STATS)
    .map(([id, stat]) => `<button class="chip${id === statId ? " on" : ""}" data-action="lb-stat" data-stat="${id}" aria-pressed="${id === statId}">${stat.label}</button>`)
    .join("");
  const waitingHtml = waiting.length
    ? `<ul class="lb-waiting">${waiting
        .map(
          (friend) => `
        <li data-login="${esc(friend.owner)}">
          <span>@${esc(friend.owner)} <span class="muted">${board.loading ? "loading…" : "hasn't published totals yet"}</span></span>
          <button class="btn sm ghost icon-only lb-remove" data-action="friend-remove" data-repo="${esc(friend.fullName)}" aria-label="Remove @${esc(friend.owner)}">${icon("x", "sm")}</button>
        </li>`,
        )
        .join("")}</ul>`
    : "";
  const publish = !mine
    ? ""
    : published
      ? `<div class="lb-publish"><span class="muted">Your totals are in brain/${STATS_PATH} as of ${esc(published.updated || "an earlier save")}.</span><button class="btn sm" data-action="stats-publish">Update</button><button class="btn sm ghost" data-action="stats-unpublish">Stop publishing</button></div>`
      : `<div class="lb-publish"><span class="muted">Friends can't see you until you publish your totals. Nothing leaves this device before you do.</span><button class="btn sm" data-action="stats-publish">Publish my totals</button></div>`;
  return `
    <section class="lb" id="leaderboard">
      <div class="lb-head">
        <h3 class="section-title">Friends and leaderboard</h3>
        <button class="btn sm ghost" data-action="lb-refresh"${board.loading ? " disabled" : ""}>${board.loading ? "Loading…" : "Refresh"}</button>
      </div>
      <div class="lb-tabs" role="group" aria-label="Rank by">${tabs}</div>
      ${ranked.length ? `<ol class="lb-list">${ranked.map((row) => rowHtml(row, statId, top)).join("")}</ol>` : ""}
      ${waitingHtml}
      <form class="lb-add" id="friend-form" autocomplete="off">
        <label for="friend-query" class="sr-only">Friend's GitHub username or brain repo</label>
        <input class="input" id="friend-query" name="query" placeholder="Add a friend: GitHub username or owner/repo">
        <button class="btn" type="submit">Add friend</button>
      </form>
      ${publish}
      <p class="muted lb-note">All-time totals that each person chose to publish in their own brain. Self-reported, not verified. Your friends list stays on this device.</p>
    </section>`;
}
