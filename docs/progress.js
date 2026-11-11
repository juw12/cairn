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
 */
export function estimateEffort({ elapsed, log, git, durations = [] }) {
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
  const level = score >= 5 ? "heavy" : score >= 2 ? "medium" : "light";
  const longerThan = durations.length >= MIN_HISTORY
    ? Math.round((durations.filter((d) => d < elapsed).length * 100) / durations.length)
    : null;
  return { level, score, factors, longerThan, lines, files, failed, checks: checks.length };
}

export function formatMinutes(minutes) {
  if (minutes === null || minutes === undefined) return "";
  if (minutes < 1) return "under a minute";
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}
