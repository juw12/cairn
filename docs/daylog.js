// Daily log and memory: pure functions over journal pages (no DOM, no storage).
// A day is journal/YYYY-MM-DD.md; an entry is a top-level bullet, usually "- 09:41 text".

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const ENTRY_RE = /^- (?:(\d{1,2}:\d{2})\s+)?(.*\S.*)$/;
const KIND_RE = /^(Task|Idea|Decision|Note|Mood):\s*(.*)$/;
const TAG_RE = /(^|\s)#([A-Za-z0-9][A-Za-z0-9_/-]*)/g;
const LINK_RE = /\[\[([^\]|\n]+)(?:\|([^\]\n]+))?\]\]/g;

export const MOODS = [
  { score: 1, label: "awful", words: ["awful", "terrible", "horrible", "miserable", "exhausted"] },
  { score: 2, label: "low", words: ["low", "bad", "sad", "down", "tired", "stressed", "meh"] },
  { score: 3, label: "okay", words: ["okay", "ok", "alright", "fine", "so so", "so-so", "neutral"] },
  { score: 4, label: "good", words: ["good", "happy", "nice", "calm", "productive", "well"] },
  { score: 5, label: "great", words: ["great", "amazing", "awesome", "fantastic", "excellent", "wonderful", "excited"] },
];

export const PROMPTS = [
  "What went well today?",
  "What did you learn?",
  "What are you stuck on?",
  "Who helped you, or who did you help?",
  "What would make tomorrow great?",
  "What surprised you?",
  "What did you finish?",
  "What's on your mind right now?",
];

// ------------------------------------------------------------ dates (local calendar days as strings)

export function dayOf(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function addDays(day, count) {
  const [y, m, d] = day.split("-").map(Number);
  return dayOf(new Date(y, m - 1, d + count));
}

export function addMonths(day, count) {
  const [y, m, d] = day.split("-").map(Number);
  const last = new Date(y, m - 1 + count + 1, 0).getDate();
  return dayOf(new Date(y, m - 1 + count, Math.min(d, last)));
}

export function weekday(day) {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).getDay();
}

export function prettyDay(day, style = "long") {
  const [y, m, d] = day.split("-").map(Number);
  const options = style === "short" ? { weekday: "short", month: "short", day: "numeric" } : { weekday: "long", month: "long", day: "numeric", year: "numeric" };
  return new Date(y, m - 1, d).toLocaleDateString("en-US", options);
}

// ISO-8601 week: { year, week }; weeks start on Monday.
export function isoWeek(day) {
  const [y, m, d] = day.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  const dow = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - dow);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  return { year: date.getUTCFullYear(), week: Math.ceil(((date - yearStart) / 86400000 + 1) / 7) };
}

export function weekStart(day) {
  const dow = weekday(day) || 7;
  return addDays(day, 1 - dow);
}

// ------------------------------------------------------------ entries

export function moodFromWords(text) {
  const clean = String(text || "").toLowerCase().replace(/[^a-z\s-]/g, " ").replace(/\s+/g, " ").trim();
  if (!clean) return null;
  const numeric = /\b([1-5])\s*(?:\/\s*5|out of 5)?\b/.exec(clean);
  for (const mood of [...MOODS].reverse()) {
    if (mood.words.some((word) => new RegExp(`(^|\\s)${word}(\\s|$)`).test(clean))) {
      if (/\bnot\s+(?:so\s+|very\s+|too\s+)?(?:good|great|happy|well|nice)\b/.test(clean)) return MOODS[1];
      return mood;
    }
  }
  if (numeric) return MOODS[Number(numeric[1]) - 1];
  return null;
}

export function moodLine(mood, note = "") {
  return `Mood: ${mood.label} (${mood.score}/5)${note ? ` ${note}` : ""}`;
}

export function parseEntries(body) {
  const entries = [];
  const lines = String(body || "").replaceAll("\r\n", "\n").split("\n");
  lines.forEach((line, index) => {
    const match = ENTRY_RE.exec(line);
    if (!match) return;
    const [, time = "", rawText] = match;
    const text = rawText.trim();
    if (/^\[[ xX]\]\s*$/.test(text)) return;
    const kindMatch = KIND_RE.exec(text);
    const kind = kindMatch ? kindMatch[1].toLowerCase() : "log";
    let mood = null;
    if (kind === "mood") {
      const score = /\((\d)\/5\)/.exec(text);
      mood = score ? MOODS[Number(score[1]) - 1] || null : moodFromWords(kindMatch[2]);
    }
    entries.push({ line: index, time, text, kind, mood, tags: tagsIn(text), links: linksIn(text) });
  });
  return entries;
}

export function tagsIn(text) {
  const out = [];
  for (const match of String(text).matchAll(TAG_RE)) {
    const tag = match[2].toLowerCase();
    if (!out.includes(tag)) out.push(tag);
  }
  return out;
}

export function linksIn(text) {
  const out = [];
  for (const match of String(text).matchAll(LINK_RE)) {
    const target = match[1].trim();
    if (!out.includes(target)) out.push(target);
  }
  return out;
}

// Plain words for reading an entry aloud or matching a search: links show their alias, no markup.
export function plainText(text) {
  return String(text)
    .replace(LINK_RE, (_, target, alias) => alias || target.split("/").pop())
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

// Append "- HH:MM text" under the page's "## Log" heading (created if missing), keeping everything else as is.
export function appendEntry(body, time, text) {
  const line = `- ${time} ${text}`.replace(/\s+$/, "");
  let source = String(body || "").replaceAll("\r\n", "\n");
  source = source.replace(/\n- \s*(?=\n|$)/g, "");
  if (!/^## Log\s*$/m.test(source)) {
    const trimmed = source.replace(/\s+$/, "");
    return `${trimmed ? `${trimmed}\n\n` : ""}## Log\n\n${line}\n`;
  }
  const lines = source.split("\n");
  const start = lines.findIndex((l) => /^## Log\s*$/.test(l));
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^#{1,2}\s/.test(lines[i])) {
      end = i;
      break;
    }
  }
  let insertAt = end;
  while (insertAt > start + 1 && lines[insertAt - 1].trim() === "") insertAt--;
  lines.splice(insertAt, 0, line);
  if (insertAt === start + 1) lines.splice(insertAt, 0, "");
  return lines.join("\n").replace(/\n*$/, "\n");
}

export function replaceEntry(body, lineIndex, time, text) {
  const lines = String(body || "").replaceAll("\r\n", "\n").split("\n");
  if (lineIndex < 0 || lineIndex >= lines.length) return body;
  lines[lineIndex] = time ? `- ${time} ${text}` : `- ${text}`;
  return lines.join("\n");
}

export function removeEntry(body, lineIndex) {
  const lines = String(body || "").replaceAll("\r\n", "\n").split("\n");
  if (lineIndex < 0 || lineIndex >= lines.length) return body;
  lines.splice(lineIndex, 1);
  return lines.join("\n");
}

// ------------------------------------------------------------ voice / typed commands

const FILLER_RE = /^(?:(?:hey|ok|okay|hi)\s+)?cairn[,.!]?\s+/i;

function sentence(text) {
  const clean = String(text || "").trim().replace(/\s+/g, " ");
  return clean ? clean.charAt(0).toUpperCase() + clean.slice(1) : "";
}

export function voiceIntent(raw) {
  let text = String(raw || "").trim().replace(FILLER_RE, "");
  const lower = text.toLowerCase().replace(/[.!?]+$/, "").trim();
  if (!lower) return { kind: "empty", text: "" };
  if (/^(stop|stop listening|that'?s all|that is all|i'?m done|im done|done|finish|end)$/.test(lower)) return { kind: "stop", text: "" };
  if (/^(undo|scratch that|never ?mind|delete (the )?last( one| entry)?|remove (the )?last( one| entry)?)$/.test(lower)) return { kind: "undo", text: "" };
  if (/^(read( it)? back|read (me )?(today|today'?s (log|diary|journal|entries))|what did i do today|read today)$/.test(lower)) return { kind: "read", day: 0, text: "" };
  if (/^(what did i do yesterday|read (me )?yesterday('?s (log|diary|journal))?|read yesterday)$/.test(lower)) return { kind: "read", day: -1, text: "" };
  if (/^(open|show)( me)? (today|today'?s (page|log|journal|diary))$/.test(lower)) return { kind: "open-today", text: "" };
  if (/^(open|show)( me)? (my )?(memories|journal|diary|log)$/.test(lower)) return { kind: "open-journal", text: "" };
  if (/^(weekly review|review (my |the |this )?week|week in review)$/.test(lower)) return { kind: "review", text: "" };
  const routes = [
    ["task", /^(?:new\s+)?(?:todo|to do|to-do|task|remind me to|reminder to|i need to|i have to)\b[\s,:-]*(.+)$/i],
    ["idea", /^(?:new\s+)?idea\b[\s,:-]*(.+)$/i],
    ["decision", /^(?:new\s+)?(?:decision|decide|we decided|i decided|decided)\b[\s,:-]*(?:to\s+|that\s+)?(.+)$/i],
    ["note", /^(?:new\s+)?note\b[\s,:-]+(.+)$/i],
    ["log", /^(?:log|diary|journal|dear diary)\b[\s,:-]+(.+)$/i],
  ];
  for (const [kind, re] of routes) {
    const match = re.exec(text);
    if (match && match[1].trim()) return { kind, text: sentence(match[1].replace(/[.]+$/, "")) };
  }
  const moodMatch = /^(?:mood|my mood is|i feel|i'?m feeling|feeling|today (?:was|felt))\b[\s,:-]*(.+)$/i.exec(text);
  if (moodMatch) {
    const mood = moodFromWords(moodMatch[1]);
    if (mood) return { kind: "mood", mood, text: sentence(moodMatch[1]) };
  }
  return { kind: "log", text: sentence(text) };
}

// ------------------------------------------------------------ memory over all days

export function journalDays(notes) {
  const days = new Map();
  for (const note of notes) {
    if (note.type !== "journal") continue;
    const day = DAY_RE.test(note.slug) ? note.slug : (DAY_RE.test(String(note.title)) ? note.title : null);
    if (!day || days.has(day)) continue;
    days.set(day, { day, path: note.path, note, entries: parseEntries(note.body) });
  }
  return days;
}

export function streaks(days, today) {
  const has = (day) => {
    const info = days.get(day);
    return !!info && info.entries.length > 0;
  };
  let current = 0;
  let cursor = has(today) ? today : addDays(today, -1);
  while (has(cursor)) {
    current++;
    cursor = addDays(cursor, -1);
  }
  let longest = 0;
  let run = 0;
  let previous = null;
  for (const day of [...days.keys()].filter(has).sort()) {
    run = previous && addDays(previous, 1) === day ? run + 1 : 1;
    longest = Math.max(longest, run);
    previous = day;
  }
  return { current, longest, loggedToday: has(today) };
}

// Columns of 7 days (Sunday first) ending with the week of `today`.
export function heatmap(days, today, weeks = 18) {
  const end = addDays(today, 6 - weekday(today));
  const start = addDays(end, -(weeks * 7 - 1));
  const columns = [];
  for (let w = 0; w < weeks; w++) {
    const column = [];
    for (let d = 0; d < 7; d++) {
      const day = addDays(start, w * 7 + d);
      const count = days.get(day)?.entries.length || 0;
      column.push({ day, count, future: day > today, level: count === 0 ? 0 : count < 3 ? 1 : count < 6 ? 2 : 3 });
    }
    columns.push(column);
  }
  return columns;
}

export function lookBacks(days, today) {
  const spots = [
    ["Yesterday", addDays(today, -1)],
    ["A week ago", addDays(today, -7)],
    ["A month ago", addMonths(today, -1)],
    ["A year ago", addMonths(today, -12)],
  ];
  return spots.map(([label, day]) => ({ label, day, info: days.get(day) || null }));
}

export function moodSeries(days, today, length = 30) {
  const out = [];
  for (let i = length - 1; i >= 0; i--) {
    const day = addDays(today, -i);
    const moods = (days.get(day)?.entries || []).filter((e) => e.mood).map((e) => e.mood.score);
    out.push({ day, score: moods.length ? moods.reduce((a, b) => a + b, 0) / moods.length : null });
  }
  return out;
}

export function averageMood(series) {
  const scores = series.map((p) => p.score).filter((s) => s !== null);
  return scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
}

export function searchEntries(days, query, limit = 60) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return [];
  const words = q.split(/\s+/);
  const hits = [];
  for (const day of [...days.keys()].sort().reverse()) {
    for (const entry of days.get(day).entries) {
      const hay = `${plainText(entry.text)} ${entry.tags.map((t) => "#" + t).join(" ")}`.toLowerCase();
      if (words.every((w) => hay.includes(w.replace(/^#/, "")) || hay.includes(w))) hits.push({ day, entry });
      if (hits.length >= limit) return hits;
    }
  }
  return hits;
}

// Most mentioned #tags and [[links]] over a set of days.
export function mentions(days, fromDay, toDay) {
  const tags = new Map();
  const links = new Map();
  for (const [day, info] of days) {
    if (day < fromDay || day > toDay) continue;
    for (const entry of info.entries) {
      for (const tag of entry.tags) tags.set(tag, (tags.get(tag) || 0) + 1);
      for (const link of entry.links) links.set(link, (links.get(link) || 0) + 1);
    }
  }
  const top = (map) => [...map.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
  return { tags: top(tags), links: top(links) };
}

// Days (other than `exclude`) whose entries mention the same #tags or [[links]] as `entries`.
export function relatedDays(days, entries, exclude, limit = 5) {
  const tags = new Set(entries.flatMap((e) => e.tags).filter((t) => !t.startsWith("mood")));
  const links = new Set(entries.flatMap((e) => e.links));
  if (!tags.size && !links.size) return [];
  const out = [];
  for (const day of [...days.keys()].sort().reverse()) {
    if (day === exclude) continue;
    const shared = [];
    for (const entry of days.get(day).entries) {
      for (const tag of entry.tags) if (tags.has(tag) && !shared.includes("#" + tag)) shared.push("#" + tag);
      for (const link of entry.links) if (links.has(link) && !shared.includes(link)) shared.push(link);
    }
    if (shared.length) out.push({ day, shared });
    if (out.length >= limit) break;
  }
  return out;
}

export function weeklyReview(days, notes, anyDayInWeek) {
  const start = weekStart(anyDayInWeek);
  const end = addDays(start, 6);
  const { year, week } = isoWeek(start);
  const logged = [];
  for (let i = 0; i < 7; i++) {
    const day = addDays(start, i);
    const info = days.get(day);
    if (info && info.entries.length) logged.push(info);
  }
  const entryCount = logged.reduce((n, info) => n + info.entries.length, 0);
  const moods = logged.flatMap((info) => info.entries.filter((e) => e.mood).map((e) => e.mood.score));
  const avg = moods.length ? moods.reduce((a, b) => a + b, 0) / moods.length : null;
  const created = notes.filter((n) => n.type !== "journal" && typeof n.meta?.created === "string" && n.meta.created >= start && n.meta.created <= end);
  const done = notes.filter((n) => n.type === "task" && n.status === "done" && typeof n.meta?.done === "string" && n.meta.done >= start && n.meta.done <= end);
  const { tags, links } = mentions(days, start, end);
  const lines = [];
  lines.push(`Week ${week} of ${year}: ${prettyDay(start, "short")} to ${prettyDay(end, "short")}.`, "");
  lines.push("## Summary", "");
  lines.push(`- Days logged: ${logged.length} of 7`);
  lines.push(`- Entries: ${entryCount}`);
  if (avg !== null) {
    const mood = MOODS[Math.round(avg) - 1];
    lines.push(`- Average mood: ${avg.toFixed(1)}/5 (${mood.label})`);
  }
  if (created.length) lines.push(`- Notes created: ${created.length}`);
  lines.push("");
  lines.push("## Day by day", "");
  if (!logged.length) lines.push("Nothing logged this week.", "");
  for (const info of logged) {
    lines.push(`### [[${info.day}|${prettyDay(info.day, "short")}]]`, "");
    for (const entry of info.entries) lines.push(`- ${entry.time ? entry.time + " " : ""}${entry.text}`);
    lines.push("");
  }
  if (created.length || done.length) {
    lines.push("## Added to the brain", "");
    for (const note of created) lines.push(`- ${note.type.charAt(0).toUpperCase() + note.type.slice(1)}: [[${note.slug}|${note.title}]]`);
    for (const note of done) if (!created.includes(note)) lines.push(`- Done: [[${note.slug}|${note.title}]]`);
    lines.push("");
  }
  if (tags.length || links.length) {
    lines.push("## Mentioned most", "");
    if (tags.length) lines.push(`- Tags: ${tags.slice(0, 8).map(([t, n]) => `#${t} (${n})`).join(", ")}`);
    if (links.length) lines.push(`- Notes: ${links.slice(0, 8).map(([l, n]) => `[[${l}]] (${n})`).join(", ")}`);
    lines.push("");
  }
  lines.push("## Looking ahead", "", "- ", "");
  return { title: `Week ${week}, ${year}`, slug: `${year}-W${String(week).padStart(2, "0")}`, body: lines.join("\n") };
}
