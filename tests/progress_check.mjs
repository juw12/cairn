// Checks for docs/progress.js; run by tests/test_progress.py (needs Node).
import assert from "node:assert/strict";
import { MACHINE_FRESH_MIN, estimateEffort, estimateProgress, formatMinutes, historyRemaining, machineFromNote, machineReading, reportedProgress } from "../docs/progress.js";

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

// The laptop under the work: a measured load lifts the level, never lowers it, and both halves stay readable.
const lifted = estimateEffort({ elapsed: 8, log: [], git: null, machine: { level: "heavy" } });
assert.deepEqual([lifted.level, lifted.work, lifted.laptop], ["heavy", "light", "heavy"]);
const kept = estimateEffort({ elapsed: 45, log: [], git: { added: 150, removed: 30, changedCount: 4, commits: [] }, machine: { level: "light" } });
assert.deepEqual([kept.level, kept.work, kept.laptop], ["medium", "medium", "light"]);
assert.equal(light.laptop, null);
assert.equal(light.work, "light");
assert.equal(estimateEffort({ elapsed: 8, log: [], git: null, machine: { level: "melting" } }).level, "light");

// A watcher's machines/ note reads back in the shape the local app gives.
const noted = {
  title: "ACME Roadrunner 14",
  meta: {
    title: "ACME Roadrunner 14", machine_at: "2026-03-04T10:00", machine_cpu: "Intel Core i5", machine_cores: "8", machine_ram_gb: "15.8",
    load: "heavy", load_cpu: "91", load_ram: "80", load_commit: "", load_power: "on battery 62%",
    machine_uptime: "up 76 h (3 d 4 h) since restart",
    load_why: "CPU 91% busy; memory 80% full", load_top: "python x3: 2.0 GB, 60% CPU; node: 1.1 GB",
  },
};
const fromNote = machineFromNote(noted);
assert.deepEqual(
  [fromNote.name, fromNote.cpu, fromNote.cores, fromNote.ram_gb, fromNote.cpu_pct, fromNote.ram_pct, fromNote.commit_pct, fromNote.level],
  ["ACME Roadrunner 14", "Intel Core i5", 8, 15.8, 91, 80, null, "heavy"],
);
assert.deepEqual(fromNote.reasons, ["CPU 91% busy", "memory 80% full"]);
assert.deepEqual(fromNote.top, ["python x3: 2.0 GB, 60% CPU", "node: 1.1 GB"]);
assert.equal(fromNote.uptime, "up 76 h (3 d 4 h) since restart");
assert.equal(machineFromNote({ meta: { load: "light", machine_at: "2026-03-04T10:00" } }).uptime, "");
assert.equal(machineFromNote({ meta: { title: "Plain note" } }), null);
assert.equal(machineFromNote({ meta: { load: "heavy" } }), null);

// The page prefers what the local app measures now; a note counts for the present only while it is recent.
const at = (stamp) => new Date(stamp).getTime();
const older = { meta: { ...noted.meta, machine_at: "2026-03-04T08:00", load: "light" } };
const live = machineReading({ level: "medium", at: "2026-03-04T10:20" }, [noted], at("2026-03-04T10:20"));
assert.deepEqual([live.level, live.live, live.fresh], ["medium", true, true]);
const fresh = machineReading(null, [older, noted, { meta: {} }], at("2026-03-04T10:25"));
assert.deepEqual([fresh.level, fresh.live, fresh.age, fresh.fresh], ["heavy", false, 25, true]);
assert.equal(machineReading(undefined, [noted], at(`2026-03-04T10:${MACHINE_FRESH_MIN}`)).fresh, true);
const stale = machineReading(null, [noted], at("2026-03-04T11:05"));
assert.deepEqual([stale.age, stale.fresh], [65, false]);
assert.equal(estimateEffort({ elapsed: 8, log: [], git: null, machine: stale.fresh ? stale : null }).level, "light");
assert.equal(machineReading(null, [], at("2026-03-04T10:00")), null);

assert.equal(formatMinutes(0), "under a minute");
assert.equal(formatMinutes(45), "45 min");
assert.equal(formatMinutes(125), "2 h 5 min");
assert.equal(formatMinutes(120), "2 h");

console.log("progress checks passed");
