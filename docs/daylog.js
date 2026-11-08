// Daily log and memory: pure functions over journal pages (no DOM, no storage).
// A day is journal/YYYY-MM-DD.md; an entry is a top-level bullet, usually "- 09:41 text".

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const ENTRY_RE = /^- (?:(\d{1,2}:\d{2})\s+)?(.*\S.*)$/;
const KIND_RE = /^(Task|Idea|Decision|Note|Mood):\s*(.*)$/;
const TAG_RE = /(^|\s)#([A-Za-z0-9][A-Za-z0-9_/-]*)/g;
const LINK_RE = /\[\[([^\]|\n]+)(?:\|([^\]\n]+))?\]\]/g;
const PERSON_RE = /(^|[\s(])@([A-Za-z][A-Za-z0-9_.-]*[A-Za-z0-9]|[A-Za-z])/g;

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
    entries.push({ line: index, time, text, kind, mood, tags: tagsIn(text), links: linksIn(text), people: peopleIn(text) });
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

// @people mentioned in an entry ("lunch with @sam"), lower-cased, without the @.
export function peopleIn(text) {
  const out = [];
  for (const match of String(text).replace(LINK_RE, "").matchAll(PERSON_RE)) {
    const person = match[2].toLowerCase();
    if (!out.includes(person)) out.push(person);
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

export function voiceIntent(raw, today = dayOf()) {
  let text = String(raw || "").trim().replace(FILLER_RE, "");
  const lower = text.toLowerCase().replace(/[.!?]+$/, "").trim();
  if (!lower) return { kind: "empty", text: "" };
  if (/^(stop|stop listening|that'?s all|that is all|i'?m done|im done|done|finish|end)$/.test(lower)) return { kind: "stop", text: "" };
  if (/^(undo|scratch that|never ?mind|delete (the )?last( one| entry)?|remove (the )?last( one| entry)?)$/.test(lower)) return { kind: "undo", text: "" };
  if (/^(read( it)? back|read (me )?(today|today'?s (log|diary|journal|entries))|what did i do today|read today)$/.test(lower)) return { kind: "read", day: 0, date: today, text: "" };
  const readMatch = /^(?:what did i (?:do|log|say|write|get up to)|what happened|read(?: me)?(?: back)?|how was)\s+(.+)$/.exec(lower);
  if (readMatch) {
    const date = spokenDay(readMatch[1], today);
    if (date && date <= today) return { kind: "read", day: dayDiff(today, date), date, text: "" };
  }
  if (/^(open|show)( me)? (today|today'?s (page|log|journal|diary))$/.test(lower)) return { kind: "open-today", text: "" };
  const openMatch = /^(?:open|show)(?: me)?\s+(.+)$/.exec(lower);
  if (openMatch) {
    const date = spokenDay(openMatch[1], today);
    if (date && date <= today) return { kind: "open-day", date, text: "" };
  }
  const findMatch = /^(?:search(?: my (?:log|journal|diary))?(?: for)?|look up|find (?:entries|mentions|days) (?:about|of|with)|when did i (?:last )?(?:mention|talk about|write about|see|meet))\s+(.+)$/.exec(lower);
  if (findMatch) return { kind: "search", query: findMatch[1].replace(/^(the word|about)\s+/, "").trim(), text: "" };
  if (/^(open|show)( me)? (my )?(memories|journal|diary|log)$/.test(lower)) return { kind: "open-journal", text: "" };
  if (/^(weekly review|review (my |the |this )?week|week in review)$/.test(lower)) return { kind: "review", text: "" };
  if (/^(monthly review|review (my |the |this )?month|month in review)$/.test(lower)) return { kind: "month-review", text: "" };
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

// ------------------------------------------------------------ "Hey Cairn" (phonetic, so speech engines' guesses still match)

// Consonant skeleton: "cairn", "karen", "kern", "kieran" and "corn" all become "crn".
function skeleton(word) {
  return word.toLowerCase().replace(/[^a-z]/g, "").replace(/[kq]/g, "c").replace(/[aeiouyhw]/g, "").replace(/(.)\1+/g, "$1");
}

const WAKE_GREETING = new Set(["hey", "hi", "hay", "hei", "ok", "okay", "oi"]);

// "Hey Cairn, finished the export" -> "finished the export"; "Hey Cairn" -> ""; anything else -> null.
export function matchWake(text) {
  const words = String(text || "").trim().split(/\s+/);
  for (let i = 0; i < Math.min(words.length - 1, 3); i++) {
    if (!WAKE_GREETING.has(words[i].toLowerCase().replace(/[^a-z]/g, ""))) continue;
    const name = skeleton(words[i + 1]);
    let used = 0;
    if (name === "crn" || name === "crns" || words[i + 1].toLowerCase().replace(/[^a-z]/g, "") === "cairo") used = 1;
    else if (name === "cr" && /^(in|n)$/i.test((words[i + 2] || "").replace(/[^a-z]/gi, ""))) used = 2;
    if (used) return words.slice(i + 1 + used).join(" ").replace(/^[\s,.!?:;-]+/, "").trim();
  }
  return null;
}

// ------------------------------------------------------------ spoken days ("last Friday", "3 November", "two days ago")

const WEEKDAY_NAMES = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
const MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];
const NUMBER_WORDS = {
  a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18,
  nineteen: 19, twenty: 20, thirty: 30, first: 1, second: 2, third: 3, fourth: 4, fifth: 5, sixth: 6,
  seventh: 7, eighth: 8, ninth: 9, tenth: 10, eleventh: 11, twelfth: 12, thirteenth: 13, fourteenth: 14,
  fifteenth: 15, sixteenth: 16, seventeenth: 17, eighteenth: 18, nineteenth: 19, twentieth: 20, thirtieth: 30,
};

function spokenNumber(text) {
  const clean = text.replace(/(st|nd|rd|th)$/, "");
  if (/^\d+$/.test(clean)) return Number(clean);
  const parts = text.split(/[\s-]+/);
  if (parts.length === 2 && (parts[0] === "twenty" || parts[0] === "thirty") && NUMBER_WORDS[parts[1]] < 10) {
    return NUMBER_WORDS[parts[0]] + NUMBER_WORDS[parts[1]];
  }
  return NUMBER_WORDS[text] ?? null;
}

function monthIndex(word) {
  const w = word.toLowerCase();
  return MONTH_NAMES.findIndex((m) => m === w || (w.length >= 3 && m.startsWith(w)));
}

function validDay(y, m, d) {
  const date = new Date(y, m, d);
  return date.getMonth() === m && date.getDate() === d ? dayOf(date) : null;
}

export function dayDiff(from, to) {
  const [a, b] = [from, to].map((day) => {
    const [y, m, d] = day.split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  });
  return Math.round((b - a) / 86400000);
}

// A spoken or typed day relative to `today` -> "YYYY-MM-DD", or null when it isn't a day.
export function spokenDay(phrase, today) {
  let p = String(phrase || "").toLowerCase().replace(/[.,!?]/g, " ").replace(/\s+/g, " ").trim();
  p = p.replace(/^(on|for|from|of)\s+/, "").replace(/'s$/, "").replace(/\s+(log|diary|journal|entries|page)$/, "").replace(/'s$/, "").trim();
  if (!p) return null;
  if (p === "today" || p === "this morning" || p === "tonight") return today;
  if (p === "yesterday") return addDays(today, -1);
  if (p === "the day before yesterday" || p === "day before yesterday") return addDays(today, -2);
  if (/^\d{4}-\d{2}-\d{2}$/.test(p)) return validDay(...p.split("-").map((n, i) => Number(n) - (i === 1 ? 1 : 0)));
  let m = /^(.+?) (day|week|month|year)s? ago$/.exec(p);
  if (m) {
    const n = spokenNumber(m[1]);
    if (!n) return null;
    if (m[2] === "day") return addDays(today, -n);
    if (m[2] === "week") return addDays(today, -7 * n);
    return addMonths(today, m[2] === "month" ? -n : -12 * n);
  }
  if (p === "last week" || p === "a week ago") return addDays(today, -7);
  if (p === "last month") return addMonths(today, -1);
  if (p === "last year") return addMonths(today, -12);
  m = /^(?:(last|this|past|previous) )?(sunday|monday|tuesday|wednesday|thursday|friday|saturday)$/.exec(p);
  if (m) {
    const target = WEEKDAY_NAMES.indexOf(m[2]);
    let back = (weekday(today) - target + 7) % 7;
    if (back === 0 && m[1] && m[1] !== "this") back = 7;
    return addDays(today, -back);
  }
  const [ty, tm] = today.split("-").map(Number);
  const year = /\b(\d{4})$/.exec(p)?.[1];
  const body = year ? p.replace(/\s*\d{4}$/, "").trim() : p;
  m = /^(?:the )?(.+?) (?:of )?([a-z]+)$/.exec(body) || null;
  let dayNum = null;
  let month = -1;
  if (m && monthIndex(m[2]) >= 0) {
    dayNum = spokenNumber(m[1]);
    month = monthIndex(m[2]);
  } else if ((m = /^([a-z]+) (?:the )?(.+)$/.exec(body)) && monthIndex(m[1]) >= 0) {
    dayNum = spokenNumber(m[2]);
    month = monthIndex(m[1]);
  } else if ((m = /^the (.+)$/.exec(body))) {
    dayNum = spokenNumber(m[1]);
    month = tm - 1;
    if (dayNum) {
      const guess = validDay(ty, month, dayNum);
      return guess && guess <= today ? guess : validDay(month === 0 ? ty - 1 : ty, (month + 11) % 12, dayNum);
    }
    return null;
  }
  if (!dayNum || month < 0) return null;
  if (year) return validDay(Number(year), month, dayNum);
  const guess = validDay(ty, month, dayNum);
  return guess && guess > today ? validDay(ty - 1, month, dayNum) : guess;
}

// ------------------------------------------------------------ typo-tolerant matching for search

// Edit distance where swapping two neighbouring letters counts as one edit ("exprot" -> "export").
function editDistance(a, b, max) {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let before = null;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    let best = i;
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (before && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) row[j] = Math.min(row[j], before[j - 2] + 1);
      best = Math.min(best, row[j]);
    }
    if (best > max) return max + 1;
    before = prev;
    prev = row;
  }
  return prev[b.length];
}

// True when a word of the haystack is within 1 edit (words of 4-6 letters) or 2 edits (7+) of `word`.
export function fuzzyHas(hayWords, word) {
  if (word.length < 4) return false;
  const max = word.length >= 7 ? 2 : 1;
  return hayWords.some((h) => h.length >= 3 && (editDistance(h, word, max) <= max || (h.length > word.length + 1 && editDistance(h.slice(0, word.length), word, max) <= max)));
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
  const close = [];
  for (const day of [...days.keys()].sort().reverse()) {
    for (const entry of days.get(day).entries) {
      const hay = `${plainText(entry.text)} ${entry.tags.map((t) => "#" + t).join(" ")}`.toLowerCase();
      if (words.every((w) => hay.includes(w.replace(/^#/, "")) || hay.includes(w))) {
        hits.push({ day, entry });
        if (hits.length >= limit) return hits;
        continue;
      }
      if (close.length < limit) {
        const hayWords = hay.split(/[^\p{L}\p{N}@#]+/u).filter(Boolean);
        const fits = words.every((w) => hay.includes(w.replace(/^#/, "")) || fuzzyHas(hayWords, w.replace(/^[#@]/, "")));
        if (fits) close.push({ day, entry, fuzzy: true });
      }
    }
  }
  return hits.concat(close).slice(0, limit);
}

// Most mentioned #tags and [[links]] over a set of days.
export function mentions(days, fromDay, toDay) {
  const tags = new Map();
  const links = new Map();
  const people = new Map();
  for (const [day, info] of days) {
    if (day < fromDay || day > toDay) continue;
    for (const entry of info.entries) {
      for (const tag of entry.tags) tags.set(tag, (tags.get(tag) || 0) + 1);
      for (const link of entry.links) links.set(link, (links.get(link) || 0) + 1);
      for (const person of entry.people || []) people.set(person, (people.get(person) || 0) + 1);
    }
  }
  const top = (map) => [...map.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
  return { tags: top(tags), links: top(links), people: top(people) };
}

// Days (other than `exclude`) whose entries mention the same #tags or [[links]] as `entries`.
export function relatedDays(days, entries, exclude, limit = 5) {
  const tags = new Set(entries.flatMap((e) => e.tags).filter((t) => !t.startsWith("mood")));
  const links = new Set(entries.flatMap((e) => e.links));
  const people = new Set(entries.flatMap((e) => e.people || []));
  if (!tags.size && !links.size && !people.size) return [];
  const out = [];
  for (const day of [...days.keys()].sort().reverse()) {
    if (day === exclude) continue;
    const shared = [];
    for (const entry of days.get(day).entries) {
      for (const tag of entry.tags) if (tags.has(tag) && !shared.includes("#" + tag)) shared.push("#" + tag);
      for (const link of entry.links) if (links.has(link) && !shared.includes(link)) shared.push(link);
      for (const person of entry.people || []) if (people.has(person) && !shared.includes("@" + person)) shared.push("@" + person);
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
  const { tags, links, people } = mentions(days, start, end);
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
  if (tags.length || links.length || people.length) {
    lines.push("## Mentioned most", "");
    if (people.length) lines.push(`- People: ${people.slice(0, 8).map(([p, n]) => `@${p} (${n})`).join(", ")}`);
    if (tags.length) lines.push(`- Tags: ${tags.slice(0, 8).map(([t, n]) => `#${t} (${n})`).join(", ")}`);
    if (links.length) lines.push(`- Notes: ${links.slice(0, 8).map(([l, n]) => `[[${l}]] (${n})`).join(", ")}`);
    lines.push("");
  }
  lines.push("## Looking ahead", "", "- ", "");
  return { title: `Week ${week}, ${year}`, slug: `${year}-W${String(week).padStart(2, "0")}`, body: lines.join("\n") };
}

// ------------------------------------------------------------ monthly review

const MONTH_TITLES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function monthStart(day) {
  return `${day.slice(0, 7)}-01`;
}

export function monthlyReview(days, notes, anyDayInMonth) {
  const start = monthStart(anyDayInMonth);
  const end = addDays(addMonths(start, 1), -1);
  const year = Number(start.slice(0, 4));
  const monthName = MONTH_TITLES[Number(start.slice(5, 7)) - 1];
  const length = Number(end.slice(8));
  const logged = [];
  let run = 0;
  let bestRun = 0;
  for (let i = 0; i < length; i++) {
    const info = days.get(addDays(start, i));
    if (info && info.entries.length) {
      logged.push(info);
      run += 1;
      bestRun = Math.max(bestRun, run);
    } else {
      run = 0;
    }
  }
  const entryCount = logged.reduce((n, info) => n + info.entries.length, 0);
  const moodsOf = (infos) => infos.flatMap((info) => info.entries.filter((e) => e.mood).map((e) => e.mood.score));
  const average = (list) => (list.length ? list.reduce((a, b) => a + b, 0) / list.length : null);
  const avg = average(moodsOf(logged));
  const busiest = logged.reduce((best, info) => (!best || info.entries.length > best.entries.length ? info : best), null);
  const created = notes.filter((n) => n.type !== "journal" && typeof n.meta?.created === "string" && n.meta.created >= start && n.meta.created <= end);
  const done = notes.filter((n) => n.type === "task" && n.status === "done" && typeof n.meta?.done === "string" && n.meta.done >= start && n.meta.done <= end);
  const { tags, links, people } = mentions(days, start, end);
  const weeks = [];
  for (let week = weekStart(start); week <= end; week = addDays(week, 7)) {
    const inMonth = logged.filter((info) => info.day >= week && info.day <= addDays(week, 6));
    const { week: number } = isoWeek(week);
    weeks.push({ week, number, days: inMonth.length, entries: inMonth.reduce((n, info) => n + info.entries.length, 0), mood: average(moodsOf(inMonth)) });
  }
  const picked = (kind) => logged.flatMap((info) => info.entries.filter((e) => e.kind === kind).map((e) => ({ day: info.day, entry: e })));
  const decisions = picked("decision");
  const ideas = picked("idea");
  const moodLabel = (score) => MOODS[Math.round(score) - 1].label;
  const lines = [];
  lines.push(`${monthName} ${year}: ${prettyDay(start, "short")} to ${prettyDay(end, "short")}.`, "");
  lines.push("## Summary", "");
  lines.push(`- Days logged: ${logged.length} of ${length}`);
  lines.push(`- Entries: ${entryCount}`);
  if (bestRun > 1) lines.push(`- Longest run: ${bestRun} days in a row`);
  if (avg !== null) lines.push(`- Average mood: ${avg.toFixed(1)}/5 (${moodLabel(avg)})`);
  if (busiest) lines.push(`- Busiest day: [[${busiest.day}|${prettyDay(busiest.day, "short")}]] (${busiest.entries.length} entries)`);
  if (created.length) lines.push(`- Notes created: ${created.length}`);
  if (done.length) lines.push(`- Tasks done: ${done.length}`);
  lines.push("");
  lines.push("## Week by week", "");
  if (!logged.length) lines.push("Nothing logged this month.", "");
  else {
    for (const w of weeks) {
      const mood = w.mood === null ? "" : `, mood ${w.mood.toFixed(1)}/5`;
      lines.push(`- Week ${w.number} (from ${prettyDay(w.week, "short")}): ${w.days} ${w.days === 1 ? "day" : "days"}, ${w.entries} ${w.entries === 1 ? "entry" : "entries"}${mood}`);
    }
    lines.push("");
  }
  if (decisions.length || ideas.length) {
    lines.push("## Decisions and ideas", "");
    for (const { day, entry } of decisions) lines.push(`- [[${day}|${prettyDay(day, "short")}]] ${entry.text}`);
    for (const { day, entry } of ideas) lines.push(`- [[${day}|${prettyDay(day, "short")}]] ${entry.text}`);
    lines.push("");
  }
  if (created.length || done.length) {
    lines.push("## Added to the brain", "");
    for (const note of created) lines.push(`- ${note.type.charAt(0).toUpperCase() + note.type.slice(1)}: [[${note.slug}|${note.title}]]`);
    for (const note of done) if (!created.includes(note)) lines.push(`- Done: [[${note.slug}|${note.title}]]`);
    lines.push("");
  }
  if (tags.length || links.length || people.length) {
    lines.push("## Mentioned most", "");
    if (people.length) lines.push(`- People: ${people.slice(0, 8).map(([p, n]) => `@${p} (${n})`).join(", ")}`);
    if (tags.length) lines.push(`- Tags: ${tags.slice(0, 8).map(([t, n]) => `#${t} (${n})`).join(", ")}`);
    if (links.length) lines.push(`- Notes: ${links.slice(0, 8).map(([l, n]) => `[[${l}]] (${n})`).join(", ")}`);
    lines.push("");
  }
  lines.push("## Looking ahead", "", "- ", "");
  return { title: `${monthName} ${year}`, slug: start.slice(0, 7), body: lines.join("\n") };
}

// Reviews worth writing now: last week, and (in the first week of a month) last month,
// when they have entries but no review page yet.
export function pendingReviews(days, notes, today) {
  const written = new Set(notes.filter((n) => n.type === "journal").map((n) => n.slug));
  const count = (from, to) => {
    let n = 0;
    for (const [day, info] of days) if (day >= from && day <= to) n += info.entries.length;
    return n;
  };
  const out = [];
  const lastWeek = addDays(weekStart(today), -7);
  const { year, week } = isoWeek(lastWeek);
  const weekSlug = `${year}-W${String(week).padStart(2, "0")}`;
  const weekEntries = count(lastWeek, addDays(lastWeek, 6));
  if (weekEntries && !written.has(weekSlug)) {
    out.push({ kind: "week", day: lastWeek, slug: weekSlug, title: `Week ${week}, ${year}`, entries: weekEntries });
  }
  if (Number(today.slice(8)) <= 7) {
    const lastMonth = addMonths(monthStart(today), -1);
    const monthSlug = lastMonth.slice(0, 7);
    const monthEntries = count(lastMonth, addDays(monthStart(today), -1));
    if (monthEntries && !written.has(monthSlug)) {
      out.push({ kind: "month", day: lastMonth, slug: monthSlug, title: `${MONTH_TITLES[Number(lastMonth.slice(5, 7)) - 1]} ${lastMonth.slice(0, 4)}`, entries: monthEntries });
    }
  }
  return out;
}

// ------------------------------------------------------------ days that read alike

const STOP_WORDS = new Set(("a about after again all also am an and any are as at back be been before being but by can " +
  "could day did do does doing done for from get got had has have he her him his how i im in into is it its just " +
  "let like me more most much my no not now of off on one only or our out over really she so some still than that " +
  "the their them then there they think this to today too up us very want was way we went were what when which " +
  "who will with would yesterday you your task idea decision note mood log").split(" "));

function stem(word) {
  for (const suffix of ["ing", "ed", "es", "s"]) {
    if (word.length - suffix.length >= 4 && word.endsWith(suffix)) return word.slice(0, -suffix.length);
  }
  return word;
}

export function contentWords(text) {
  return plainText(text)
    .toLowerCase()
    .replace(/'/g, "")
    .split(/[^\p{L}\p{N}]+/u)
    .filter((w) => w.length >= 3 && !STOP_WORDS.has(w) && !/^\d+$/.test(w))
    .map(stem);
}

// Days whose words read most like `entries` (TF-IDF cosine over each day's text). Runs on the device; no model.
export function similarDays(days, entries, exclude, limit = 4, minScore = 0.12) {
  const docs = [];
  for (const [day, info] of days) {
    if (day === exclude || !info.entries.length) continue;
    docs.push({ day, words: info.entries.flatMap((e) => contentWords(e.text)) });
  }
  const query = entries.flatMap((e) => contentWords(e.text));
  if (!query.length || !docs.length) return [];
  const df = new Map();
  for (const doc of [...docs, { words: query }]) for (const w of new Set(doc.words)) df.set(w, (df.get(w) || 0) + 1);
  const total = docs.length + 1;
  const vector = (words) => {
    const tf = new Map();
    for (const w of words) tf.set(w, (tf.get(w) || 0) + 1);
    const out = new Map();
    for (const [w, n] of tf) {
      const weight = n * Math.log(total / df.get(w));
      if (weight > 0) out.set(w, weight);
    }
    return out;
  };
  const norm = (v) => Math.sqrt([...v.values()].reduce((s, x) => s + x * x, 0));
  const q = vector(query);
  const qNorm = norm(q);
  if (!qNorm) return [];
  const scored = [];
  for (const doc of docs) {
    const v = vector(doc.words);
    const vNorm = norm(v);
    if (!vNorm) continue;
    const shared = [];
    let dot = 0;
    for (const [w, weight] of q) {
      if (!v.has(w)) continue;
      dot += weight * v.get(w);
      shared.push([w, weight * v.get(w)]);
    }
    const score = dot / (qNorm * vNorm);
    if (score >= minScore) scored.push({ day: doc.day, score, words: shared.sort((a, b) => b[1] - a[1]).slice(0, 3).map(([w]) => w) });
  }
  return scored.sort((a, b) => b.score - a.score || (a.day < b.day ? 1 : -1)).slice(0, limit);
}
