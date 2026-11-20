// Live progress for a running session: a percentage, the time left, and how heavy the work is.
// Pure functions (no DOM, no imports) so they are easy to test and reuse.
//
// Progress comes from the best signal available, in this order:
//   1. reported  - the agent logged it: "[40%] wiring the UI", "progress 40%", "40% done", "[3/7]", "step 3/7"
//   2. history   - nothing reported: your own finished sessions say how long work like this usually runs.
//                  Time left is the median of (duration - elapsed) over past sessions that ran at least as
//                  long as this one has so far, so it stays honest when a session is already running long.
// Effort (light / medium / heavy) scores time spent, code churn, files touched, checks and failures.

const MINUTES_PER_DAY = 1440;
const MIN_HISTORY = 5;
const SAME_AGENT_MIN = 10;
const ESTIMATE_CAP = 97;

const PERCENT_PATTERNS = [
  /\[(\d{1,3})\s*%\]/, // [40%]
  /\bprogress:?\s*(\d{1,3})\s*%/i, // progress 40% / progress: 40%
  /^(\d{1,3})\s*%(?:\s|$|[:.,-])/, // "40% wiring the UI"
  /\b(\d{1,3})\s*%\s+done\b/i, // "40% done"
];
const FRACTION_PATTERNS = [
  /\[(\d{1,3})\s*\/\s*(\d{1,3})\]/, // [3/7]
  /\b(?:step|progress|phase|part):?\s*(\d{1,3})\s*(?:\/|of)\s*(\d{1,3})\b/i, // step 3/7, phase 2 of 5
];

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function clockMinutes(hhmm) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || ""));
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

// The last progress the agent reported in its log, as { pct, text, time } (time = "HH:MM"), or null.
export function reportedProgress(log) {
  for (let index = log.length - 1; index >= 0; index -= 1) {
    const entry = log[index];
    if (entry.kind === "end") return { pct: 100, text: entry.text, time: entry.time };
    const text = String(entry.text || "");
    for (const pattern of PERCENT_PATTERNS) {
      const match = pattern.exec(text);
      if (match && Number(match[1]) <= 100) return { pct: Number(match[1]), text: stripMarker(text), time: entry.time };
    }
    for (const pattern of FRACTION_PATTERNS) {
      const match = pattern.exec(text);
      if (match) {
        const done = Number(match[1]);
        const total = Number(match[2]);
        if (total > 0 && done <= total) {
          return { pct: Math.round((done * 100) / total), text: stripMarker(text), time: entry.time, done, total };
        }
      }
    }
  }
  return null;
}

function stripMarker(text) {
  return text.replace(/\[\d{1,3}\s*(?:%|\/\s*\d{1,3})\]\s*/, "").trim();
}

// Minutes from the session start to a log entry's HH:MM (log times carry no date; sessions are under a day).
function minutesIntoSession(startedClock, entryClock) {
  const start = clockMinutes(startedClock);
  const at = clockMinutes(entryClock);
  if (start === null || at === null) return null;
  return (at - start + MINUTES_PER_DAY) % MINUTES_PER_DAY;
}

// Expected minutes left after `elapsed`, from finished sessions that ran at least that long.
export function historyRemaining(durations, elapsed) {
  const longer = durations.filter((d) => d > elapsed);
  if (longer.length < MIN_HISTORY) return { remaining: null, sample: longer.length };
  return { remaining: Math.max(1, Math.round(median(longer.map((d) => d - elapsed)))), sample: longer.length };
}

/**
 * Progress for a live session.
 * @param {object} input
 * @param {Array<{time:string,kind:string,text:string}>} input.log  parsed session log
 * @param {number} input.elapsed   minutes since the session started
 * @param {string} input.startedClock  "HH:MM" of the session start
 * @param {number[]} input.durations  minutes of comparable finished sessions
 * @returns {{pct:number|null, remaining:number|null, basis:string, sample:number, step:string, overdue:boolean}}
 */
export function estimateProgress({ log, elapsed, startedClock, durations }) {
  const reported = reportedProgress(log);
  const history = historyRemaining(durations, elapsed);
  if (reported) {
    let remaining = null;
    const at = minutesIntoSession(startedClock, reported.time);
    if (reported.pct >= 100) {
      remaining = 0;
    } else if (reported.pct >= 3 && at !== null && at > 0) {
      // Keep the pace of the reported work: time so far per percent, times the percent left,
      // minus whatever time has passed since the report.
      remaining = Math.max(1, Math.round((at * (100 - reported.pct)) / reported.pct - (elapsed - at)));
    } else if (history.remaining !== null) {
      remaining = history.remaining;
    }
    return {
      pct: clamp(reported.pct, 0, 100),
      remaining,
      basis: "reported",
      sample: history.sample,
      step: reported.text,
      overdue: false,
    };
  }
  if (history.remaining === null) {
    return { pct: null, remaining: null, basis: durations.length ? "long" : "none", sample: durations.length, step: "", overdue: durations.length > 0 };
  }
  const pct = clamp(Math.round((elapsed * 100) / (elapsed + history.remaining)), 0, ESTIMATE_CAP);
  return { pct, remaining: history.remaining, basis: "history", sample: history.sample, step: "", overdue: false };
}

/**
 * How heavy the work is, with the reasons.
 * @param {object} input
 * @param {number} input.elapsed  minutes
 * @param {Array<{kind:string}>} input.log
 * @param {{added?:number,removed?:number,changedCount?:number,commits?:Array<{added:number,removed:number}>}|null} input.git
 * @param {number[]} input.durations  finished-session minutes, to place this one against your usual
 * @param {{level:string}|null} input.machine  the computer's load now (machineReading), when it applies to this session
 */
export function estimateEffort({ elapsed, log, git, durations = [], machine = null }) {
  const checks = log.filter((e) => e.kind === "pass" || e.kind === "fail");
  const failed = checks.filter((e) => e.kind === "fail").length;
  const prompts = log.filter((e) => e.kind === "prompt").length;
  const commits = git?.commits || [];
  const lines = (git?.added || 0) + (git?.removed || 0) + commits.reduce((sum, c) => sum + (c.added || 0) + (c.removed || 0), 0);
  const files = git?.changedCount || 0;

  const factors = [];
  const add = (points, text) => factors.push({ points, text });
  add(elapsed >= 120 ? 2 : elapsed >= 30 ? 1 : 0, `${elapsed} min so far`);
  if (git) {
    add(lines >= 500 ? 2 : lines >= 100 ? 1 : 0, `${lines} lines changed`);
    add(files >= 15 ? 2 : files >= 5 ? 1 : 0, `${files} file${files === 1 ? "" : "s"} touched`);
    if (commits.length) add(commits.length >= 5 ? 1 : 0, `${commits.length} commit${commits.length === 1 ? "" : "s"}`);
  }
  if (checks.length) add(failed >= 2 ? 2 : failed ? 1 : 0, `${checks.length} check${checks.length === 1 ? "" : "s"}, ${failed} failed`);
  if (prompts) add(prompts >= 4 ? 1 : 0, `${prompts} prompt${prompts === 1 ? "" : "s"}`);

  const score = factors.reduce((sum, f) => sum + f.points, 0);
  // The size of the work says nothing about a laptop that is flat out, so a measured load can lift the level, never lower it.
  const work = score >= 5 ? "heavy" : score >= 2 ? "medium" : "light";
  const laptop = LEVELS.includes(machine?.level) ? machine.level : null;
  const level = laptop && LEVELS.indexOf(laptop) > LEVELS.indexOf(work) ? laptop : work;
  const longerThan = durations.length >= MIN_HISTORY
    ? Math.round((durations.filter((d) => d < elapsed).length * 100) / durations.length)
    : null;
  return { level, work, laptop, score, factors, longerThan, lines, files, failed, checks: checks.length };
}

// ------------------------------------------------------------ the computer under the sessions

const LEVELS = ["light", "medium", "heavy"];
export const MACHINE_FRESH_MIN = 30; // an older reading is shown with its time, and no longer lifts a session's effort

const numberOrNull = (value) => (value === "" || value === null || value === undefined || !Number.isFinite(Number(value)) ? null : Number(value));
const listOf = (value) => String(value || "").split(";").map((part) => part.trim()).filter(Boolean);

/** The reading a watcher left in a machines/ note, in the shape the local app's /api/vibe gives. Null for any other note. */
export function machineFromNote(note) {
  const meta = note?.meta || {};
  if (!LEVELS.includes(meta.load) || typeof meta.machine_at !== "string" || !meta.machine_at) return null;
  return {
    name: String(meta.title || note.title || ""),
    cpu: String(meta.machine_cpu || ""),
    cores: numberOrNull(meta.machine_cores),
    ram_gb: numberOrNull(meta.machine_ram_gb),
    at: meta.machine_at,
    cpu_pct: numberOrNull(meta.load_cpu),
    ram_pct: numberOrNull(meta.load_ram),
    commit_pct: numberOrNull(meta.load_commit),
    power: String(meta.load_power || ""),
    uptime: String(meta.machine_uptime || ""),
    level: meta.load,
    reasons: listOf(meta.load_why),
    top: listOf(meta.load_top),
  };
}

/**
 * The computer and its load: measured now by the local app when there is one, else the newest machines/ note.
 * Adds `live`, `age` (minutes since the reading) and `fresh` (recent enough to describe the present).
 */
export function machineReading(live, notes, nowMs = Date.now()) {
  let reading = live && LEVELS.includes(live.level) ? { ...live, live: true } : null;
  if (!reading) {
    const noted = notes.map(machineFromNote).filter(Boolean).sort((a, b) => (a.at < b.at ? 1 : -1));
    reading = noted.length ? { ...noted[0], live: false } : null;
  }
  if (!reading) return null;
  const at = Date.parse(reading.at);
  const age = Number.isFinite(at) ? Math.max(0, Math.round((nowMs - at) / 60000)) : null;
  return { ...reading, age, fresh: reading.live || (age !== null && age <= MACHINE_FRESH_MIN) };
}

// ------------------------------------------------------------ GitHub Actions minutes

const GITHUB_LEVELS = ["ok", "warn", "critical", "over"];

/** One month of GitHub Actions minutes from a github-usage/ note the watcher left. Null for any other note. */
export function githubFromNote(note) {
  const meta = note?.meta || {};
  const used = numberOrNull(meta.gh_used);
  const limit = numberOrNull(meta.gh_limit);
  if (!/^\d{4}-\d{2}$/.test(String(meta.gh_month || "")) || used === null || !limit || limit < 0) return null;
  const pct = numberOrNull(meta.gh_pct) ?? Math.round((used * 100) / limit);
  const projected = numberOrNull(meta.gh_projected) ?? used;
  // Set when the count starts from the figure GitHub itself gave (cairn vibe github <minutes>).
  const anchorUsed = numberOrNull(meta.gh_anchor_used);
  const anchor = anchorUsed === null ? null : { used: anchorUsed, at: String(meta.gh_anchor_at || ""), api: numberOrNull(meta.gh_anchor_api) };
  const repos = listOf(meta.gh_repos)
    .map((part) => /^(.*\S)\s+(\d+)$/.exec(part))
    .filter(Boolean)
    .map((match) => ({ name: match[1], minutes: Number(match[2]) }));
  // github-usage/2026-11.md is the main account's month; github-usage/<name>/2026-11.md is another account's.
  const shelf = /(?:^|\/)github-usage\/([^/]+)\/[^/]+$/.exec(String(note?.path || ""))?.[1] || "";
  return {
    month: String(meta.gh_month),
    account: String(meta.gh_account || shelf),
    extra: Boolean(shelf),
    at: String(meta.gh_at || ""),
    used,
    limit,
    pct,
    level: GITHUB_LEVELS.includes(meta.gh_level) ? meta.gh_level : pct >= 100 ? "over" : pct >= 90 ? "critical" : pct >= 75 ? "warn" : "ok",
    projected,
    projectedPct: numberOrNull(meta.gh_projected_pct) ?? Math.round((projected * 100) / limit),
    resets: String(meta.gh_resets || ""),
    repos,
    days: listOf(meta.gh_days).map(Number).filter(Number.isFinite),
    public: numberOrNull(meta.gh_public) ?? 0,
    final: meta.gh_final === "yes" || meta.gh_final === true,
    anchor,
    apiUsed: numberOrNull(meta.gh_api_used) ?? used,
    since: numberOrNull(meta.gh_since) ?? 0,
    scale: numberOrNull(meta.gh_scale) ?? 1,
    pace: anchor ? numberOrNull(meta.gh_pace) : null,
  };
}

/**
 * What is left of a month's minutes and how thin to spread it: `left`, whole `days` until the reset
 * (it happens at 00:00 UTC), `perDay` that keeps the month under the limit, and `runsOut`, the day
 * the minutes end at the pace since GitHub's own count, when that is before the reset.
 */
export function githubBudget(reading, nowMs = Date.now()) {
  const left = Math.max(0, reading.limit - reading.used);
  const reset = Date.parse(`${reading.resets}T00:00:00Z`);
  if (reading.final || !Number.isFinite(reset)) return { left, days: null, perDay: null, runsOut: null };
  const days = Math.max(1, Math.ceil((reset - nowMs) / 86400000));
  const lasts = reading.pace > 0 ? left / reading.pace : Infinity;
  const runsOut = lasts < days ? new Date(nowMs + lasts * 86400000).toISOString().slice(0, 10) : null;
  return { left, days, perDay: Math.floor(left / days), runsOut };
}

// ------------------------------------------------------------ which AI ran a session

const AGENT_LABELS = {
  openbot: "OpenBot",
  opencode: "OpenCode",
  vector: "Vector",
  codex: "Codex",
  claude: "Claude Code",
  "claude-code": "Claude Code",
  "claude code": "Claude Code",
  copilot: "Copilot",
  cursor: "Cursor",
  gemini: "Gemini",
};

// A bot that drives OpenCode from its own workspace (…/openbot-pegaai-bridge/workspace) is its own group,
// so its runs do not hide among the sessions you opened in OpenCode yourself. The watcher files those under
// "opencode", which is the engine; any other agent name is what the session called itself and stays.
function botOf(meta, agent) {
  if (agent && agent !== "opencode") return "";
  const folder = typeof meta?.folder === "string" ? meta.folder.toLowerCase() : "";
  return folder.split(/[\\/]+/).some((part) => part.startsWith("openbot")) ? "openbot" : "";
}

/**
 * The AI a session belongs to, for grouping: the agent it names, else the bot whose workspace it ran in,
 * else the source that wrote the note. Spelling and case do not split a group ("Vector" and "vector" are
 * one); no name at all is "Other".
 */
export function agentGroup(meta) {
  const agent = String(meta?.agent || "").trim().toLowerCase();
  const name = botOf(meta, agent) || agent || String(meta?.source || "").trim().toLowerCase();
  if (!name || name === "logged") return { key: "other", label: "Other" };
  const label = AGENT_LABELS[name] || name.replace(/^./, (letter) => letter.toUpperCase());
  const key = label === "Claude Code" ? "claude" : name;
  return { key, label };
}

const newestFirst = (a, b) => (a.month < b.month ? 1 : -1);

/** Every month the watcher noted for the main account, newest first. */
export function githubUsage(notes) {
  return notes.map(githubFromNote).filter((reading) => reading && !reading.extra).sort(newestFirst);
}

/**
 * The months of every account that has notes: [{ account, extra, months }], the main account first and
 * the others by name. An account that is the main one somewhere and named on another computer shows once.
 */
export function githubAccounts(notes) {
  const main = githubUsage(notes);
  const mainName = (main[0]?.account || "").toLowerCase();
  const others = new Map();
  for (const reading of notes.map(githubFromNote)) {
    if (!reading?.extra || !reading.account || reading.account.toLowerCase() === mainName) continue;
    const key = reading.account.toLowerCase();
    if (!others.has(key)) others.set(key, []);
    others.get(key).push(reading);
  }
  const rest = [...others.keys()].sort().map((key) => {
    const months = others.get(key).sort(newestFirst);
    return { account: months[0].account, extra: true, months };
  });
  return [...(main.length ? [{ account: main[0].account, extra: false, months: main }] : []), ...rest];
}

const MOVE_WAIT_MS = 24 * 3600 * 1000; // GitHub keeps the link it mails to the new owner for a day

/** Repo moves Cairn asked GitHub for, from the notes in github-usage/moves/: [{ repo, from, to, at }], newest first. */
export function githubMoves(notes) {
  return notes
    .map((note) => note?.meta || {})
    .filter((meta) => meta.gh_move_repo && meta.gh_move_from && meta.gh_move_to)
    .map((meta) => ({ repo: String(meta.gh_move_repo), from: String(meta.gh_move_from), to: String(meta.gh_move_to), at: String(meta.gh_move_at || "") }))
    .sort((a, b) => (a.at < b.at ? 1 : -1));
}

/** True while a move can still be waiting for the new owner to open GitHub's mail. */
export function movePending(move, nowMs = Date.now()) {
  const at = Date.parse(move?.at);
  return Number.isFinite(at) && nowMs - at < MOVE_WAIT_MS;
}

// ------------------------------------------------------------ one brain, several computers

/** The computer a session ran on, as its watcher or `cairn vibe start` named it. "" when it names none. */
export function deviceOf(note) {
  return String(note?.meta?.machine ?? "").trim();
}

/**
 * The computers this brain's sessions ran on, the one used last first:
 * [{ name, sessions, live, tokens, last }]. Sessions that name no computer are one entry named "", listed last.
 * A computer that has left a machines/ note and no session yet is listed too.
 */
export function deviceList(notes) {
  const found = new Map();
  const entry = (name) => {
    if (!found.has(name)) found.set(name, { name, sessions: 0, live: 0, tokens: 0, last: "" });
    return found.get(name);
  };
  for (const note of notes) {
    if (note.type === "session") {
      const device = entry(deviceOf(note));
      const seen = String(note.meta.ended || note.meta.started || note.meta.created || "");
      device.sessions += 1;
      device.live += note.status === "live" ? 1 : 0;
      device.tokens += Number(note.meta.tokens) || 0;
      if (seen > device.last) device.last = seen;
      continue;
    }
    const reading = machineFromNote(note);
    if (reading?.name) {
      const device = entry(reading.name.trim());
      if (reading.at > device.last) device.last = reading.at;
    }
  }
  return [...found.values()].sort((a, b) => (a.name === "") - (b.name === "") || (a.last < b.last ? 1 : a.last > b.last ? -1 : a.name < b.name ? -1 : 1));
}

/** The notes as one computer has them: its sessions and its machines/ note. Every other kind of note is kept. */
export function deviceNotes(notes, device) {
  return notes.filter((note) => {
    if (note.type === "session") return deviceOf(note) === device;
    const reading = machineFromNote(note);
    return reading ? Boolean(device) && reading.name.trim() === device : true;
  });
}

/**
 * The notes that count when only some computers are picked: the sessions those computers ran, and every note
 * that is not a session. `names` null or empty means all of them.
 */
export function notesOfDevices(notes, names) {
  const picked = new Set(names || []);
  return picked.size ? notes.filter((note) => note.type !== "session" || picked.has(deviceOf(note))) : notes;
}

/**
 * A kept choice of computers, checked against the ones the brain holds sessions of (`list` from deviceList):
 * the names that still exist, in the list's order, or null when that is none of them or all of them.
 */
export function deviceChoice(list, kept) {
  const wanted = new Set(Array.isArray(kept) ? kept.filter((name) => typeof name === "string") : []);
  const names = list.map((device) => device.name).filter((name) => wanted.has(name));
  return names.length && names.length < list.length ? names : null;
}

export function formatMinutes(minutes) {
  if (minutes === null || minutes === undefined) return "";
  if (minutes < 1) return "under a minute";
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}
