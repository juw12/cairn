// Checks for docs/progress.js; run by tests/test_progress.py (needs Node).
import assert from "node:assert/strict";
import { estimateEffort, estimateProgress, formatMinutes, historyRemaining, reportedProgress } from "../docs/progress.js";

const log = (...entries) => entries.map(([time, kind, text]) => ({ time, kind, text }));

// Reported progress: the markers agents write, newest wins; numbers that are not progress are ignored.
assert.equal(reportedProgress(log(["10:00", "note", "[40%] wiring the UI"])).pct, 40);
assert.equal(reportedProgress(log(["10:00", "note", "progress: 65% tests next"])).pct, 65);
assert.equal(reportedProgress(log(["10:00", "note", "30% done with the parser"])).pct, 30);
assert.equal(reportedProgress(log(["10:00", "note", "step 3/4 migrations"])).pct, 75);
assert.equal(reportedProgress(log(["10:00", "note", "[2/8] scaffolding"])).pct, 25);
assert.equal(reportedProgress(log(["10:00", "note", "[10%] a"], ["10:30", "note", "[60%] b"])).pct, 60);
assert.equal(reportedProgress(log(["10:00", "pass", "pytest 311/311 passed, coverage 85%"])), null);
assert.equal(reportedProgress(log(["10:00", "note", "council 8.6, sw v54"])), null);
assert.equal(reportedProgress(log(["10:00", "end", "shipped"])).pct, 100);
assert.equal(reportedProgress(log(["10:00", "note", "[40%] wiring the UI"])).text, "wiring the UI");

// History: time left comes from sessions that ran at least as long as this one so far.
const durations = [10, 20, 30, 40, 60, 90, 120, 180, 240, 600];
assert.deepEqual(historyRemaining(durations, 0), { remaining: 75, sample: 10 });
assert.deepEqual(historyRemaining(durations, 100), { remaining: null, sample: 4 });
assert.equal(historyRemaining(durations, 50).sample, 6);
assert.equal(historyRemaining(durations, 50).remaining, 100); // median of 10, 40, 70, 130, 190, 550

// Reported pace: 40% after 20 min, checked 5 min later -> 30 min of work at that pace, minus 5 = 25 left.
const paced = estimateProgress({ log: log(["10:20", "note", "[40%] halfway-ish"]), elapsed: 25, startedClock: "10:00", durations });
assert.equal(paced.basis, "reported");
assert.equal(paced.pct, 40);
assert.equal(paced.remaining, 25);
assert.equal(paced.step, "halfway-ish");

// Nothing reported: estimated from history, never shown as finished.
const guessed = estimateProgress({ log: [], elapsed: 50, startedClock: "10:00", durations });
assert.equal(guessed.basis, "history");
assert.equal(guessed.remaining, 100);
assert.equal(guessed.pct, 33); // 50 / (50 + 100)
const early = estimateProgress({ log: [], elapsed: 0, startedClock: "10:00", durations });
assert.equal(early.pct, 0);

// Running longer than almost every past session: no made-up number.
const long = estimateProgress({ log: [], elapsed: 500, startedClock: "10:00", durations });
assert.equal(long.pct, null);
assert.equal(long.basis, "long");
assert.equal(long.overdue, true);

// No history at all.
assert.equal(estimateProgress({ log: [], elapsed: 5, startedClock: "10:00", durations: [] }).basis, "none");

// Effort: small and quick is light; long, big and bumpy is heavy; the reasons are listed.
const light = estimateEffort({ elapsed: 8, log: log(["10:00", "pass", "pytest"]), git: { added: 20, removed: 4, changedCount: 2, commits: [] } });
assert.equal(light.level, "light");
const medium = estimateEffort({ elapsed: 45, log: [], git: { added: 150, removed: 30, changedCount: 4, commits: [] } });
assert.equal(medium.level, "medium");
const heavy = estimateEffort({
  elapsed: 150,
  log: log(["10:00", "fail", "pytest"], ["10:10", "fail", "pytest"], ["10:20", "pass", "pytest"]),
  git: { added: 400, removed: 200, changedCount: 18, commits: [{ added: 50, removed: 10 }] },
  durations,
});
assert.equal(heavy.level, "heavy");
assert.ok(heavy.factors.some((f) => f.text === "3 checks, 2 failed"));
assert.equal(heavy.lines, 660);
assert.equal(heavy.longerThan, 70);

assert.equal(formatMinutes(0), "under a minute");
assert.equal(formatMinutes(45), "45 min");
assert.equal(formatMinutes(125), "2 h 5 min");
assert.equal(formatMinutes(120), "2 h");

console.log("progress checks passed");
