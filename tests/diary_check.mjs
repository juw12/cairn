// Checks for docs/diary.js; run by tests/test_diary.py (needs Node).
import assert from "node:assert/strict";
import { noteFromFile } from "../docs/core.js";
import { activityDays, busyScore, dayLabel, dayRows, diaryDevices, diaryNotes, parseFigures, sessionDays } from "../docs/diary.js";

const PC = "ACME Roadrunner 14";
const MAC = "MacBook Pro 15,4"; // a comma in a name must survive every list it passes through
const TODAY = "2026-03-05";

const session = (slug, meta) =>
  noteFromFile(`sessions/${slug}.md`, `---\ntitle: ${slug}\ntype: session\n${Object.entries(meta).map(([key, value]) => `${key}: ${value}`).join("\n")}\n---\n`);
const hours = (list) => Array.from({ length: 24 }, (_, hour) => list[hour] || 0).join(" ");
const activity = (name, lines) =>
  noteFromFile(`machines/${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-activity.md`, `---\ntitle: ${name} activity\ntype: note\ntags: [activity]\nmachine: ${name}\nactivity_at: 2026-03-05T12:00\n---\nWritten by the watcher.\n\n${lines.join("\n")}\n`);

// The score: screen time up to 8 h is linear, tasks and agent time go by their square root, stars from 1 to 5.
assert.deepEqual(busyScore(0, 0, 0), { score: 0, stars: 1, screen: 0, tasks: 0, agent: 0 });
assert.deepEqual(busyScore(480, 200, 6000), { score: 100, stars: 5, screen: 35, tasks: 40, agent: 25 });
assert.deepEqual(busyScore(2000, 900, 90000), { score: 100, stars: 5, screen: 35, tasks: 40, agent: 25 });
assert.equal(busyScore(240, 50, 1500).score, 17.5 + 20 + 12.5);
assert.equal(busyScore(240, 50, 1500).stars, 3);
assert.equal(busyScore(30, 0, 0).stars, 1, "a day with any work in it has a star");

// Lines: figures that are missing stay undefined, so "not measured" never reads as zero.
assert.deepEqual(parseFigures([" screen 212 ", " cpu 23 ", " first 08:12 ", " source tracker ", " hours 0 5 60 "]), { screen: 212, cpu: 23, first: "08:12", source: "tracker", hours: [0, 5, 60] });
assert.deepEqual(parseFigures([" awake 30 ", " cpu x ", " last late "]), { awake: 30 });

const notes = [
  session("2026-03-05-a", { status: "shipped", started: "2026-03-05T09:00", ended: "2026-03-05T09:40", machine: PC, tokens_in: 1000, tokens_out: 500 }),
  session("2026-03-05-b", { status: "done", started: "2026-03-05T10:00", ended: "2026-03-05T12:00", minutes: 30, machine: PC }),
  session("2026-03-05-c", { status: "live", started: "2026-03-05T11:00", machine: PC }),
  session("2026-03-05-d", { status: "shipped", started: "2026-03-05T08:00", ended: "2026-03-05T08:20", machine: MAC, tokens: 700 }),
  session("2026-03-04-e", { status: "dropped", started: "2026-03-04T08:00", ended: "2026-03-04T08:10", machine: MAC }),
  activity(PC, [`- 2026-03-05 | screen 120 | awake 200 | cpu 30 | cpu_peak 90 | ram 60 | ram_peak 75 | heavy 4 | first 08:55 | last 12:00 | hours ${hours({ 9: 60, 10: 60 })}`]),
  activity(MAC, [
    `- 2026-03-05 | screen 45 | awake 50 | cpu 12 | cpu_peak 40 | ram 70 | ram_peak 71 | heavy 0 | first 07:58 | last 08:45 | hours ${hours({ 8: 45 })}`,
    "- 2026-03-03 | awake 20 | cpu 5 | cpu_peak 9 | ram 50 | ram_peak 51 | heavy 0",
  ]),
  noteFromFile("Productivity Diary/2026-03-05.md", `---\ntitle: "Productivity Diary - 2026-03-05"\ntype: note\ndate: 2026-03-05\ntags: [diary, productivity]\nscore: 40.0\nai_tokens: 99000\nupdated: 2026-03-05T12:00\ndevices:\n  - "${PC} | screen 150 | cpu 33 | cpu_peak 95 | ram 62 | ram_peak 80 | source tracker"\n  - "${MAC} | screen 40 | source watcher"\n---\n# The day\n`),
  noteFromFile("Productivity Diary/2026-03-01.md", "---\ntitle: old\ntype: note\ntags: [diary, productivity]\nrest_day: true\n---\n"),
  noteFromFile("Productivity Diary/Productivity Diary.md", "---\ntitle: Productivity Diary\ntags: [diary, productivity, index]\n---\n"),
  noteFromFile("machines/acme-roadrunner-14.md", "---\ntitle: ACME Roadrunner 14\ntype: note\ntags: [machine]\nmachine_at: 2026-03-05T12:00\nload: light\nload_cpu: 10\nload_ram: 50\n---\n"),
  noteFromFile("notes/plain.md", "---\ntitle: plain\n---\n- 2026-03-05 | screen 999\n"),
];

// Each source on its own.
const watched = activityDays(notes);
assert.deepEqual([...watched.keys()], ["acme roadrunner 14", "macbook pro 15,4"]);
assert.equal(watched.get("macbook pro 15,4").name, MAC);
assert.equal(watched.get("macbook pro 15,4").days.get("2026-03-03").screen, undefined, "a day without screen time measured");
assert.equal(watched.get("acme roadrunner 14").days.get("2026-03-05").hours[9], 60);
const reports = diaryNotes(notes);
assert.deepEqual([...reports.keys()].sort(), ["2026-03-01", "2026-03-05"], "the index note is not a day");
assert.deepEqual(reports.get("2026-03-05").devices.map((entry) => entry.name), [PC, MAC]);
assert.equal(reports.get("2026-03-05").devices[0].screen, 150);
assert.equal(reports.get("2026-03-05").path, "Productivity Diary/2026-03-05.md");
const worked = sessionDays(notes).get("2026-03-05");
assert.deepEqual([...worked.keys()], ["acme roadrunner 14", "macbook pro 15,4"]);
const pcWork = worked.get("acme roadrunner 14");
assert.deepEqual([pcWork.tasks, pcWork.finished, pcWork.live, pcWork.agent, pcWork.tokens], [3, 2, 1, 40 + 30, 1500]);
assert.equal(worked.get("macbook pro 15,4").tokens, 700);

// The rows: every computer together.
const rows = dayRows(notes, { today: TODAY, days: 5 });
assert.deepEqual(rows.map((row) => row.day), ["2026-03-05", "2026-03-04", "2026-03-03", "2026-03-02", "2026-03-01"]);
const [day5, day4, day3, day2, day1] = rows;
assert.equal(day5.screen, 150 + 45, "each computer's screen time is the most any recorder saw for it");
assert.deepEqual([day5.tasks, day5.finished, day5.live, day5.agent], [4, 3, 1, 90]);
assert.equal(day5.tokens, 99000, "the diary note's token count wins when it is the larger");
assert.deepEqual([day5.cpu, day5.cpuPeak, day5.ram, day5.ramPeak], [Math.round((33 + 12) / 2), 95, Math.round((62 + 70) / 2), 80]);
assert.equal(day5.note, "Productivity Diary/2026-03-05.md");
assert.equal(day5.rest, false);
assert.equal(day5.score, busyScore(195, 3, 90).score);
assert.ok(day5.stars >= 1);
assert.deepEqual(day5.devices.map((entry) => entry.name), [PC, MAC], "the busier computer first");
const [pc, mac] = day5.devices;
assert.deepEqual([pc.screen, pc.cpu, pc.cpuPeak, pc.source, pc.tasks, pc.first, pc.last], [150, 33, 95, "tracker", 3, "08:55", "12:00"]);
assert.deepEqual([mac.screen, mac.cpu, mac.ramPeak, mac.source, mac.tasks, mac.hours[8]], [45, 12, 71, "watcher", 1, 45]);
assert.equal(pc.sessions.length, 3);
assert.equal(mac.score, busyScore(45, 1, 20).score);

// A day with a dropped task is a day of work (one star, never none); a day awake with no screen time read and
// nothing asked is a rest day, and so is a day nothing was noted.
assert.deepEqual([day4.rest, day4.tasks, day4.finished, day4.stars, day4.screen], [false, 1, 0, 1, null]);
assert.deepEqual([day3.rest, day3.score, day3.stars, day3.cpu], [true, null, 0, 5]);
assert.deepEqual([day2.rest, day2.devices.length, day2.cpu], [true, 0, null]);
assert.deepEqual([day1.rest, day1.note], [true, "Productivity Diary/2026-03-01.md"]);
for (const row of rows) assert.ok(row.rest || row.stars >= 1, "no worked day without a star");

// One computer alone.
const macRows = dayRows(notes, { today: TODAY, days: 5, device: "macbook  pro 15,4" });
assert.deepEqual([macRows[0].screen, macRows[0].tasks, macRows[0].tokens, macRows[0].devices.length], [45, 1, 700, 1]);
assert.equal(macRows[1].tasks, 1);
assert.equal(dayRows(notes, { today: TODAY, days: 5, device: "Nobody's" })[0].rest, true);

// The computers are found from the notes: sessions, activity notes, load notes and the diary's own list.
assert.deepEqual(diaryDevices(notes, { today: TODAY, days: 5 }), [{ name: MAC, days: 2 }, { name: PC, days: 1 }]);
assert.deepEqual(diaryDevices([notes[10]], { today: TODAY }), [{ name: PC, days: 0 }], "a computer with only a load note is listed too");
assert.deepEqual(diaryDevices([], { today: TODAY }), []);
assert.equal(dayRows([], { today: TODAY }).length, 30);
assert.ok(dayRows([], { today: TODAY }).every((row) => row.rest));

// Days cross months and years on the calendar, not on the clock.
assert.deepEqual(dayRows([], { today: "2026-01-01", days: 2 }).map((row) => row.day), ["2026-01-01", "2025-12-31"]);
assert.equal(dayLabel("2026-03-05"), "Thu 5 Mar");

console.log("diary checks passed");
