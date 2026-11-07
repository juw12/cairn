// Writing stats: how much is written, where, and when.

export const manifest = {
  id: "writing-stats",
  name: "Writing stats",
  version: "1.1.0",
  author: "Cairn",
  description: "Words written, notes per type, longest notes and an activity chart.",
  settings: [
    { key: "wordsPerMinute", label: "Reading speed", type: "number", default: 230, min: 60, max: 1000, step: 10, description: "Words per minute, for the reading-time estimates." },
    { key: "chartDays", label: "Activity chart", type: "select", default: "30", options: [{ value: "14", label: "Last 14 days" }, { value: "30", label: "Last 30 days" }, { value: "90", label: "Last 90 days" }] },
  ],
};

export const countWords = (text) => String(text).replace(/```[\s\S]*?```/g, " ").split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length;

export default function activate(cairn) {
  const esc = cairn.escape;
  const speed = () => Math.max(60, Number(cairn.settings.get("wordsPerMinute")) || 230);
  const days = () => Number(cairn.settings.get("chartDays")) || 30;
  cairn.settings.onChange(() => {
    if (document.querySelector(".ws-tiles")) cairn.rerender(); // only when the stats page is showing
  });

  function draw(el) {
    const notes = cairn.notes();
    const words = notes.map((n) => ({ note: n, words: countWords(n.body) }));
    const total = words.reduce((sum, w) => sum + w.words, 0);
    const byType = {};
    for (const n of notes) byType[n.type] = (byType[n.type] || 0) + 1;
    const longest = [...words].sort((a, b) => b.words - a.words).slice(0, 8);
    const day = 86_400_000;
    const now = Date.now();
    const span = days();
    const buckets = Array.from({ length: span }, () => 0);
    for (const n of notes) {
      const age = Math.floor((now - (n.mtime || 0)) / day);
      if (n.mtime && age >= 0 && age < span) buckets[span - 1 - age] += 1;
    }
    const peak = Math.max(1, ...buckets);
    const bars = buckets.map((count, i) => {
      const date = new Date(now - (span - 1 - i) * day).toISOString().slice(0, 10);
      return `<span class="ws-bar" style="--h:${Math.round((count / peak) * 100)}%" title="${date}: ${count} note${count === 1 ? "" : "s"} edited"></span>`;
    }).join("");
    el.innerHTML = `
      <div class="page-head"><div><p class="eyebrow">Plugin</p><h1>Writing stats</h1><p>What's in this brain, and how it has been growing.</p></div></div>
      <div class="ws-tiles">
        <div class="ws-tile"><strong>${total.toLocaleString()}</strong><span>words</span></div>
        <div class="ws-tile"><strong>${notes.length.toLocaleString()}</strong><span>notes</span></div>
        <div class="ws-tile"><strong>${notes.length ? Math.round(total / notes.length).toLocaleString() : 0}</strong><span>words per note</span></div>
        <div class="ws-tile"><strong>${Math.max(1, Math.round(total / speed())).toLocaleString()} min</strong><span>to read it all</span></div>
      </div>
      <section class="ws-section"><h2>Notes edited, last ${span} days</h2><div class="ws-chart" role="img" aria-label="Notes edited per day over the last ${span} days">${bars}</div></section>
      <section class="ws-section"><h2>By type</h2><ul class="ws-types">${Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([type, count]) => `<li><span>${esc(type)}</span><strong>${count}</strong></li>`).join("")}</ul></section>
      <section class="ws-section"><h2>Longest notes</h2><ol class="ws-longest">${longest.map((w) => `<li><a class="wikilink" href="#" data-open="${esc(w.note.path)}">${esc(w.note.title)}</a> <span class="muted">${w.words.toLocaleString()} words</span></li>`).join("")}</ol></section>`;
  }

  cairn.addView({ id: "stats", name: "Writing stats", icon: "sliders", render: draw });
  cairn.addCommand({
    id: "count-open",
    name: "Word count of this note",
    icon: "note",
    run: () => {
      const note = cairn.activeNote();
      if (!note) return cairn.toast("Open a note first.");
      const words = countWords(note.body);
      cairn.toast(`“${note.title}”: ${words.toLocaleString()} words, about ${Math.max(1, Math.round(words / speed()))} min to read.`);
    },
  });

  cairn.addStyle(`
    .ws-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
    .ws-tile { border: 1px solid var(--line); border-radius: 10px; background: var(--surface); padding: 14px 16px; display: flex; flex-direction: column; gap: 2px; }
    .ws-tile strong { font-family: var(--display); font-weight: 400; font-size: 30px; line-height: 1.1; }
    .ws-tile span { color: var(--muted); font-size: 12.5px; }
    .ws-section { margin-top: 26px; }
    .ws-section h2 { font-size: 13px; font-weight: 600; color: var(--muted); margin: 0 0 10px; }
    .ws-chart { display: flex; align-items: flex-end; gap: 3px; height: 90px; padding: 8px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }
    .ws-bar { flex: 1; height: max(2px, var(--h)); background: var(--accent); border-radius: 2px 2px 0 0; opacity: .85; }
    .ws-types { list-style: none; margin: 0; padding: 0; max-width: 360px; }
    .ws-types li { display: flex; justify-content: space-between; border-bottom: 1px solid var(--line); padding: 6px 0; }
    .ws-longest { margin: 0; padding-left: 20px; }
    .ws-longest li { padding: 3px 0; }
  `);
}
